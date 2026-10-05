import logging

import httpx
from typing import Dict, Any, List, Optional
from app.core.config import settings
from app.core.tracing import trace
from app.services.llm_client import llm_stats

logger = logging.getLogger(__name__)


class VoiceAIService:
    """
    Zero-Cost & Free-Credit Voice AI Engine:
    - TTS: ElevenLabs (10k free credits)
    - STT: Deepgram ($200 free credits)
    - LLM: Groq (gpt-oss, see GROQ_MODEL) or Gemini (see GEMINI_MODEL) or Local Ollama
    """

    @staticmethod
    async def transcribe_audio_deepgram(audio_bytes: bytes) -> str:
        """
        Deepgram Nova-2 streaming / pre-recorded STT using free $200 credits.
        """
        if not settings.DEEPGRAM_API_KEY:
            return "Simulated transcription: Candidate explained their experience with FastAPI and PostgreSQL."

        headers = {
            "Authorization": f"Token {settings.DEEPGRAM_API_KEY}",
            "Content-Type": "audio/wav"
        }
        url = "https://api.deepgram.com/v1/listen?model=nova-2&smart_format=true"

        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                res = await client.post(url, content=audio_bytes, headers=headers)
                data = res.json()
                transcript = data["results"]["channels"][0]["alternatives"][0]["transcript"]
                return transcript
            except Exception as e:
                logger.error("Deepgram transcription failed: %s", e)
                return ""

    @staticmethod
    async def synthesize_speech_elevenlabs(text: str) -> Optional[bytes]:
        """
        Synthesize natural AI speech using ElevenLabs (10k free tier credits).
        """
        if not settings.ELEVENLABS_API_KEY:
            return None  # Fallback to carrier default IVR voice

        url = f"https://api.elevenlabs.io/v1/text-to-speech/{settings.ELEVENLABS_VOICE_ID}"
        headers = {
            "xi-api-key": settings.ELEVENLABS_API_KEY,
            "Content-Type": "application/json"
        }
        payload = {
            "text": text,
            "model_id": "eleven_turbo_v2_5",  # Fastest & lowest credit consumption
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75
            }
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                res = await client.post(url, json=payload, headers=headers)
                if res.status_code == 200:
                    return res.content
                logger.warning("ElevenLabs HTTP %s (quota/key issue?); falling back to Edge-TTS", res.status_code)
            except Exception as exc:
                logger.warning("ElevenLabs call failed: %s", exc)
        return None

    @staticmethod
    async def synthesize_speech_edge(text: str) -> Optional[bytes]:
        """Free neural TTS fallback (edge-tts). Returns None if the package is unavailable."""
        if not settings.USE_EDGE_TTS_FALLBACK:
            return None
        try:
            import edge_tts
            chunks = []
            async for chunk in edge_tts.Communicate(text, "en-US-JennyNeural").stream():
                if chunk["type"] == "audio":
                    chunks.append(chunk["data"])
            return b"".join(chunks) or None
        except Exception as exc:
            logger.warning("Edge-TTS unavailable: %s", exc)
            return None

    @classmethod
    async def synthesize_speech(cls, text: str) -> Optional[bytes]:
        """ElevenLabs -> Edge-TTS -> None (None means: let the carrier speak the text itself)."""
        audio = await cls.synthesize_speech_elevenlabs(text)
        if audio:
            return audio
        return await cls.synthesize_speech_edge(text)

    @classmethod
    @trace("interview_turn_llm", run_type="llm")
    async def generate_interview_turn_llm(
        cls,
        history: List[Dict[str, str]],
        job_title: str,
        job_skills: List[str],
        guidance: str = "",
    ) -> str:
        """
        Generate conversational AI response during phone interview.
        Tries Groq -> Gemini -> local Ollama (models come from settings).
        """
        # 1. Option A: Groq Cloud (Free tier, ultra-fast 800 tokens/sec, best for conversational phone voice)
        if settings.GROQ_API_KEY:
            try:
                headers = {
                    "Authorization": f"Bearer {settings.GROQ_API_KEY}",
                    "Content-Type": "application/json"
                }
                system_prompt = (
                    f"You are conducting a phone interview for {job_title}. "
                    f"Required skills: {', '.join(job_skills)}. "
                    "Keep questions concise (1-2 sentences), friendly, and conversational because this is a voice call. "
                    "Ask one technical question at a time. Reply in at most 30 words."
                    + (f" {guidance}" if guidance else "")
                )
                messages = [{"role": "system", "content": system_prompt}] + history
                payload = {
                    "model": settings.GROQ_FAST_MODEL,
                    "messages": messages,
                    # reasoning models spend tokens thinking before they speak: keep effort low, leave headroom
                    "max_tokens": 400 if "gpt-oss" in settings.GROQ_FAST_MODEL else 120,
                    "temperature": 0.7,
                    **({"reasoning_effort": "low"} if "gpt-oss" in settings.GROQ_FAST_MODEL else {}),
                }
                async with httpx.AsyncClient(timeout=6.0) as client:
                    res = await client.post("https://api.groq.com/openai/v1/chat/completions", json=payload, headers=headers)
                    if res.status_code == 200:
                        text = (res.json()["choices"][0]["message"].get("content") or "").strip()
                        if text:
                            llm_stats["turn_ok"] += 1
                            return text
                        logger.warning("Groq returned an empty reply (reasoning exhausted max_tokens?)")
                    else:
                        logger.warning("Groq interview turn HTTP %s: %s", res.status_code, res.text[:150])
            except Exception as exc:
                logger.warning("Groq interview turn failed: %s", exc)

        # 2. Option B: Google Gemini 1.5 Flash (Free tier / Jio plan)
        if settings.GEMINI_API_KEY:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.GEMINI_MODEL}:generateContent"
                system_text = (
                    f"You are conducting a phone interview for {job_title}. Required skills: {', '.join(job_skills)}. "
                    "Reply with ONE concise spoken question or comment (1-2 sentences)."
                    + (f" {guidance}" if guidance else "")
                )
                contents = [
                    {"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
                    for m in history
                ] or [{"role": "user", "parts": [{"text": "Hello"}]}]
                if contents[0]["role"] == "model":  # Gemini wants the conversation to open with the user
                    contents.insert(0, {"role": "user", "parts": [{"text": "Hello, I'm ready to begin."}]})
                payload = {
                    "systemInstruction": {"parts": [{"text": system_text}]},
                    "contents": contents,
                    "generationConfig": {"temperature": 0.7, "thinkingConfig": {"thinkingBudget": 0}},
                }
                async with httpx.AsyncClient(timeout=8.0) as client:
                    res = await client.post(url, json=payload, headers={"x-goog-api-key": settings.GEMINI_API_KEY})
                    if res.status_code == 200:
                        llm_stats["turn_ok"] += 1
                        return res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
                    logger.warning("Gemini interview turn HTTP %s: %s", res.status_code, res.text[:150])
            except Exception as exc:
                logger.warning("Gemini interview turn failed: %s", exc)

        # 3. Option C: Local Ollama (Self-hosted on localhost, zero API cost); skipped when OLLAMA_BASE_URL is empty
        try:
            if not settings.OLLAMA_BASE_URL:
                raise RuntimeError("Ollama not configured")
            payload = {
                "model": "llama3.1:8b",
                "prompt": f"You are interviewing for {job_title}. Candidate said: {history[-1]['content'] if history else 'Ready'}. Keep reply to 1 spoken question. {guidance}",
                "stream": False
            }
            async with httpx.AsyncClient(timeout=5.0) as client:
                res = await client.post(f"{settings.OLLAMA_BASE_URL}/api/generate", json=payload)
                if res.status_code == 200:
                    return res.json().get("response", "").strip()
        except Exception as exc:
            logger.warning("Ollama interview turn failed: %s", exc)

        llm_stats["turn_fallback"] += 1
        logger.warning("All LLM providers failed for interview turn; using canned follow-up")
        # Fallback intelligent default
        return "That's very interesting. Could you also share a challenging bug you encountered in production and how you resolved it?"


voice_ai_service = VoiceAIService()
