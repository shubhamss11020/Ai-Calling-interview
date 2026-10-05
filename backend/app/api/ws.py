"""WebSocket endpoint Vobiz connects to for the live audio stream of an interview call."""
import logging

from fastapi import APIRouter, WebSocket

from app.services.telephony_service import verify_interview_token
from app.voice.session import VoiceSession, load_context

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws/vobiz/{interview_id}/{token}")
async def vobiz_audio_stream(ws: WebSocket, interview_id: str, token: str):
    # HTTP middleware does not cover WebSockets, so authenticate here with the interview-bound HMAC token.
    if not verify_interview_token(interview_id, token):
        logger.warning("Rejected audio stream with invalid token (interview=%s)", interview_id)
        await ws.close(code=1008)
        return
    ctx = await load_context(interview_id)
    if ctx is None:
        await ws.close(code=1008)
        return
    await VoiceSession(ws, ctx).run()
