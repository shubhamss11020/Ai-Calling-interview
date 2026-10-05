"""Vobiz telephony adapter, written against the documented Vobiz API:

* REST:     POST {base}/Account/{auth_id}/Call/   (JSON; auth via X-Auth-ID / X-Auth-Token) -> {"request_uuid": ...}
* Webhooks: Vobiz POSTs form-encoded data to answer_url / Gather action / hangup_url and expects VobizXML
            (Content-Type: application/xml, < 100 KB, within 1-2 s).
* Gather posts back `Digits` (keypad) or `Speech` (transcript); hangup posts `Duration`, `Status`, `CallUUID`.

Webhook URLs carry an HMAC token bound to the interview id, because Vobiz callbacks are otherwise unauthenticated.
"""
import hmac
import hashlib
import logging
from typing import Any, Dict, Iterable, Optional
from urllib.parse import urlencode, urlsplit
from xml.sax.saxutils import escape, quoteattr

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

WEBHOOK_BASE_PATH = "/api/v1/interviews/telephony/vobiz"


def sign_interview(interview_id: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), interview_id.encode(), hashlib.sha256).hexdigest()[:24]


def verify_interview_token(interview_id: str, token: Optional[str]) -> bool:
    return bool(token) and hmac.compare_digest(sign_interview(interview_id), token)


def webhook_url(path: str, interview_id: Optional[str] = None, **extra: Any) -> str:
    """Absolute, token-carrying URL for a webhook (Vobiz requires fully-qualified URLs)."""
    params: Dict[str, Any] = {}
    if interview_id:
        params["interview_id"] = interview_id
        params["token"] = sign_interview(interview_id)
    params.update({k: v for k, v in extra.items() if v is not None})
    query = f"?{urlencode(params)}" if params else ""
    return f"{settings.PUBLIC_BASE_URL.rstrip('/')}{WEBHOOK_BASE_PATH}/{path}{query}"


def public_url_problem() -> Optional[str]:
    """Vobiz must reach our webhooks over public HTTPS. Returns a human-readable problem, or None if fine."""
    url = settings.PUBLIC_BASE_URL
    host = urlsplit(url).hostname or ""
    if not url.startswith("https://"):
        return f"PUBLIC_BASE_URL must be an https:// URL reachable from the internet (got {url!r}); run ngrok and set it"
    if host in ("localhost", "127.0.0.1") or host.endswith(".example.com") or host == "your-api.example.com":
        return f"PUBLIC_BASE_URL {url!r} is not a real public host; Vobiz cannot call it"
    return None


# ----------------------------------------------------------------------------- VobizXML builders

def _wrap(*elements: str) -> str:
    return '<?xml version="1.0" encoding="UTF-8"?><Response>' + "".join(elements) + "</Response>"


def _speak(text: str) -> str:
    return f'<Speak voice="WOMAN" language="{settings.VOBIZ_SPEECH_LANGUAGE}">{escape(text)}</Speak>'


def xml_gather_pin(prompt: str, action_url: str) -> str:
    """Collect a 6-digit PIN on the keypad; if nothing is entered the call ends politely."""
    return _wrap(
        f'<Gather action={quoteattr(action_url)} method="POST" inputType="dtmf" numDigits="{settings.PIN_LENGTH}" '
        f'finishOnKey="#" executionTimeout="20">{_speak(prompt)}</Gather>',
        _speak("We did not receive a PIN. Please call back when you are ready. Goodbye."),
        "<Hangup/>",
    )


def xml_gather_speech(prompt: str, action_url: str, retry_url: str, hints: Iterable[str] = ()) -> str:
    """Speak `prompt`, listen for the candidate's answer (Vobiz speech recognition). On silence, redirect to
    `retry_url` so the server decides whether to re-ask or end the call."""
    hint_attr = f" hints={quoteattr(','.join(h for h in hints if h)[:2000])}" if any(hints) else ""
    return _wrap(
        f'<Gather action={quoteattr(action_url)} method="POST" inputType="speech" '
        f'language="{settings.VOBIZ_SPEECH_LANGUAGE}" speechModel="telephony" executionTimeout="30" '
        f'speechEndTimeout="auto"{hint_attr}>{_speak(prompt)}</Gather>',
        f'<Redirect method="POST">{escape(retry_url)}</Redirect>',
    )


def ws_stream_url(interview_id: str) -> str:
    """wss:// URL of our audio-stream endpoint (path carries the interview id and its HMAC token)."""
    base = settings.PUBLIC_BASE_URL.rstrip("/")
    host = base.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
    return f"{host}/ws/vobiz/{interview_id}/{sign_interview(interview_id)}"


def xml_stream(ws_url: str, prelude: str = "") -> str:
    """Bidirectional audio stream (linear16). keepCallAlive holds the call while the stream runs; when our server
    closes the socket the following <Hangup/> ends the call."""
    rate = settings.STREAM_SAMPLE_RATE
    return _wrap(
        _speak(prelude) if prelude else "",
        f'<Stream bidirectional="true" audioTrack="inbound" keepCallAlive="true" '
        f'contentType="audio/x-l16;rate={rate}" streamTimeout="{settings.MAX_CALL_SECONDS + 300}">{escape(ws_url)}</Stream>',
        "<Hangup/>",
    )


def xml_say_and_hangup(text: str) -> str:
    return _wrap(_speak(text), "<Hangup/>")


# ----------------------------------------------------------------------------- REST client

class VobizTelephonyService:
    """Outbound dialling via the Vobiz REST API."""

    @property
    def configured(self) -> bool:
        return bool(settings.VOBIZ_AUTH_ID and settings.VOBIZ_AUTH_TOKEN and settings.VOBIZ_PHONE_NUMBER)

    def _headers(self) -> Dict[str, str]:
        return {
            "X-Auth-ID": settings.VOBIZ_AUTH_ID,
            "X-Auth-Token": settings.VOBIZ_AUTH_TOKEN,
            "Content-Type": "application/json",
        }

    async def initiate_outbound_call(
        self, candidate_phone: str, interview_id: str, pin_code: str, callback_url: str = ""
    ) -> Dict[str, Any]:
        """Place the call. Returns {"call_id": uuid} on success, {"status": "ERROR", "error": ...} on failure,
        or a simulated payload when Vobiz credentials are not configured."""
        if not self.configured:
            logger.info("Vobiz not configured; simulating call for interview %s", interview_id)
            return {"status": "SIMULATED", "call_id": f"vobiz_sim_{interview_id[:8]}", "to": candidate_phone}

        problem = public_url_problem()
        if problem:
            logger.error("Cannot place call for interview %s: %s", interview_id, problem)
            return {"status": "ERROR", "error": problem}

        payload = {
            "from": settings.VOBIZ_PHONE_NUMBER,
            "to": candidate_phone,
            "answer_url": webhook_url("answer", interview_id),
            "answer_method": "POST",
            "hangup_url": webhook_url("hangup", interview_id),
            "hangup_method": "POST",
        }
        url = f"{settings.VOBIZ_BASE_URL.rstrip('/')}/Account/{settings.VOBIZ_AUTH_ID}/Call/"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.post(url, json=payload, headers=self._headers())
            if res.status_code >= 400:
                logger.error("Vobiz call API HTTP %s for interview %s: %s", res.status_code, interview_id, res.text[:300])
                return {"status": "ERROR", "error": f"Vobiz HTTP {res.status_code}: {res.text[:200]}"}
            data = res.json()
            call_id = data.get("request_uuid") or data.get("call_uuid")
            if not call_id:
                logger.error("Vobiz call API returned no call id: %s", data)
                return {"status": "ERROR", "error": f"Unexpected Vobiz response: {str(data)[:200]}"}
            logger.info("Vobiz call placed: interview=%s call_uuid=%s", interview_id, call_id)
            return {"status": "PLACED", "call_id": call_id}
        except Exception as exc:
            logger.error("Vobiz outbound call to %s failed for interview %s: %s", candidate_phone, interview_id, exc)
            return {"status": "ERROR", "error": str(exc)}

    async def hangup_call(self, call_uuid: str) -> bool:
        if not self.configured:
            return False
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.delete(
                    f"{settings.VOBIZ_BASE_URL.rstrip('/')}/Account/{settings.VOBIZ_AUTH_ID}/Call/{call_uuid}/",
                    headers=self._headers(),
                )
            return res.status_code < 400
        except Exception as exc:
            logger.warning("Vobiz hangup of %s failed: %s", call_uuid, exc)
            return False


vobiz_service = VobizTelephonyService()
