import secrets
import string
import time
from typing import Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.interview import Interview

import logging

logger = logging.getLogger(__name__)


class CallSlotLimiter:
    """Caps concurrent outbound calls per company (default 20).

    Each active call holds a slot keyed by interview id with an expiry timestamp, so a lost hangup
    webhook cannot leak capacity. Uses Redis when reachable (shared across API/worker processes),
    otherwise an in-process dict.
    """

    _LUA = """
    redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
    if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[3]) then return 0 end
    redis.call('ZADD', KEYS[1], ARGV[2], ARGV[4])
    return 1
    """

    def __init__(self):
        self._local: Dict[str, Dict[str, float]] = {}
        self._redis = None
        self._redis_checked = False

    def _client(self):
        if not self._redis_checked:
            self._redis_checked = True
            try:
                import redis
                client = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=3, socket_timeout=3)
                client.ping()
                self._redis = client
            except Exception as exc:
                logger.warning("Redis unavailable for call-slot limiter (%s); using in-process limiter", exc)
                self._redis = None
        return self._redis

    def acquire(self, company_id: str, interview_id: str) -> bool:
        now = time.time()
        expiry = now + settings.CALL_SLOT_TTL_SECONDS
        limit = settings.MAX_CONCURRENT_CALLS_PER_COMPANY
        client = self._client()
        if client is not None:
            try:
                return bool(client.eval(self._LUA, 1, f"call_slots:{company_id}", now, expiry, limit, interview_id))
            except Exception as exc:
                logger.warning("Redis slot acquire failed (%s); falling back to in-process", exc)
        slots = self._local.setdefault(company_id, {})
        for key in [k for k, exp in slots.items() if exp <= now]:
            del slots[key]
        if interview_id in slots:
            return True
        if len(slots) >= limit:
            return False
        slots[interview_id] = expiry
        return True

    def release(self, company_id: str, interview_id: str) -> None:
        client = self._client()
        if client is not None:
            try:
                client.zrem(f"call_slots:{company_id}", interview_id)
            except Exception as exc:
                logger.warning("Redis slot release failed: %s", exc)
        self._local.get(company_id, {}).pop(interview_id, None)


call_slot_limiter = CallSlotLimiter()


class InterviewService:
    @staticmethod
    def generate_pin_code(length: int = 6) -> str:
        """Cryptographically random numeric PIN."""
        return "".join(secrets.choice(string.digits) for _ in range(length))

    @classmethod
    async def generate_unique_pin(cls, db: AsyncSession, length: int = 6) -> str:
        for _ in range(20):
            pin = cls.generate_pin_code(length)
            exists = await db.execute(select(Interview.id).where(Interview.pin_code == pin))
            if exists.first() is None:
                return pin
        return cls.generate_pin_code(length + 2)  # keyspace pressure: widen


interview_service = InterviewService()
