"""Candidate notifications (PIN reminder / resume-after-disconnect).

Vobiz has no SMS API (only WhatsApp), so SMS is disabled by default: messages are logged, never sent to a
guessed endpoint. The call flow itself already tells candidates their PIN rules; to enable real delivery, plug a
provider into `_deliver` and set SMS_ENABLED=true.
"""
import logging

from app.core.config import settings

logger = logging.getLogger("notifications")


async def _deliver(to: str, body: str) -> bool:  # pragma: no cover - provider hook
    raise NotImplementedError("No SMS provider configured")


async def send_sms(to: str, body: str) -> bool:
    if not settings.SMS_ENABLED:
        logger.info("SMS disabled; not sent to %s: %s", to, body)
        return False
    try:
        return await _deliver(to, body)
    except Exception as exc:
        logger.warning("SMS to %s failed: %s", to, exc)
        return False


def inbound_number() -> str:
    return settings.VOBIZ_PHONE_NUMBER or "the interview number"


async def send_pin_sms(to: str, pin: str) -> bool:
    return await send_sms(to, f"Your AI interview PIN is {pin}. Call {inbound_number()} and enter it to begin.")


async def send_resume_sms(to: str, pin: str) -> bool:
    return await send_sms(
        to, f"Your interview was disconnected. Dial {inbound_number()} and enter PIN {pin} to resume where you left off."
    )
