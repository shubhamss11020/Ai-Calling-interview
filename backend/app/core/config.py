import os
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from typing import List, Union
from pydantic import AnyHttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_ignore_empty=True,
        extra="ignore"
    )

    PROJECT_NAME: str = "AI Recruitment & Calling Platform"
    VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"
    API_V1_STR: str = "/api/v1"
    LOG_LEVEL: str = "INFO"  # DEBUG also enables SQL statement logging
    SECRET_KEY: str = "change_this_to_a_secure_random_key_in_production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 1 day

    # CORS Origins
    BACKEND_CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ]

    # Neon Postgres (required; no local/SQLite fallback). Paste the Neon connection string as-is,
    # e.g. postgresql://user:pass@ep-xxx-pooler.region.aws.neon.tech/db?sslmode=require
    DATABASE_URL: str

    # Upstash Redis (required). Use the TLS URL: rediss://default:<password>@<host>.upstash.io:6379
    # Upstash exposes a single logical DB (0), so broker/result/limiter all share it under different key prefixes.
    REDIS_URL: str
    CELERY_BROKER_URL: str = ""      # defaults to REDIS_URL
    CELERY_RESULT_BACKEND: str = ""  # defaults to REDIS_URL

    # AI Keys & Providers (Zero-cost stack)
    GEMINI_API_KEY: str = ""
    OPENAI_API_KEY: str = ""
    GROQ_API_KEY: str = ""  # Free tier: 30 RPM, 14.4k RPD on Llama 3.3 70B & Llama 3.1 8B (ultra-fast TTFT < 300ms)
    # Models are configurable because providers retire them (the old defaults 404 for current keys).
    # List what your key can use: GET https://api.groq.com/openai/v1/models  /  GET .../v1beta/models
    GROQ_MODEL: str = "openai/gpt-oss-120b"        # screening, scoring, answer assessment (JSON)
    GROQ_FAST_MODEL: str = "openai/gpt-oss-20b"    # live phone turns (latency matters)
    GEMINI_MODEL: str = "gemini-2.5-flash"
    OLLAMA_BASE_URL: str = "http://localhost:11434"  # For local self-hosted LLM (Llama 3.1 / Mistral)

    # RAG (pgvector on Neon). Embeddings come from Gemini; without GEMINI_API_KEY RAG degrades to plain truncation.
    RAG_ENABLED: bool = True
    EMBEDDING_MODEL: str = "gemini-embedding-001"
    EMBEDDING_DIM: int = 768
    RAG_CHUNK_CHARS: int = 900
    RAG_CHUNK_OVERLAP: int = 150
    RAG_TOP_K: int = 5

    # LangSmith tracing (optional)
    LANGSMITH_TRACING: bool = False
    LANGSMITH_API_KEY: str = ""
    LANGSMITH_PROJECT: str = "talentvoice"
    LANGSMITH_ENDPOINT: str = ""

    # Storage
    S3_BUCKET_NAME: str = "recruitment-resumes"
    UPLOAD_DIR: str = "./uploads"

    # Voice & Telephony (Vobiz + Free Credits Stack)
    VOBIZ_AUTH_ID: str = ""
    VOBIZ_AUTH_TOKEN: str = ""
    VOBIZ_PHONE_NUMBER: str = ""
    VOBIZ_BASE_URL: str = "https://api.vobiz.ai/api/v1"
    VOBIZ_SPEECH_LANGUAGE: str = "en-US"   # Gather/Speak language; try en-IN if Vobiz supports it for your account
    PIN_LENGTH: int = 6

    # Live voice (Vobiz bidirectional audio stream + Deepgram). VOICE_MODE=gather falls back to Vobiz's built-in
    # speech recognition, which in practice returned no transcripts for this account.
    VOICE_MODE: str = "stream"                  # stream | gather
    STREAM_SAMPLE_RATE: int = 16000
    DEEPGRAM_STT_MODEL: str = "nova-3"
    DEEPGRAM_TTS_MODEL: str = "aura-asteria-en"  # ~0.35 s to first audio; aura-2 voices take ~1.4 s
    VOICE_ENDPOINTING_MS: int = 500             # silence that ends the candidate's turn
    VOICE_SILENCE_SECONDS: float = 12.0         # candidate silent this long -> re-ask the question
    VOICE_FILLER_AFTER_SECONDS: float = 1.2     # play a short "let me think" if the reply isn't ready by then
    MAX_CALL_SECONDS: int = 900
    DEEPGRAM_API_KEY: str = ""      # $200 free credit on signup
    ELEVENLABS_API_KEY: str = ""    # 10,000 characters/month free credit
    ELEVENLABS_VOICE_ID: str = "21m00Tcm4TlvDq8ikWAM"  # Rachel (default natural voice)
    USE_EDGE_TTS_FALLBACK: bool = True  # 100% free, unlimited high-quality neural TTS fallback

    # Public URL Vobiz uses to reach our webhooks
    # The API has no login yet. Requests arriving on any host other than localhost (e.g. an ngrok tunnel) may only
    # reach the carrier webhooks and /health. List extra hostnames here to expose the full API (e.g. your deployed UI/API).
    FULL_API_HOSTS: List[str] = []
    PUBLIC_BASE_URL: str = "http://localhost:8000"
    SMS_FROM_NUMBER: str = ""
    SMS_ENABLED: bool = False  # Vobiz exposes no SMS API (WhatsApp only); keep off until an SMS provider is wired

    # Pipeline behaviour
    ASYNC_PROCESSING: bool = False  # True: API enqueues screening/evaluation on Celery instead of running inline
    SUITABLE_THRESHOLD: int = 65
    MARGINAL_THRESHOLD: int = 45
    MIN_SKILL_COVERAGE_FOR_SUITABLE: float = 0.6  # matched/required below this can never be SUITABLE
    MIN_RESUME_TEXT_CHARS: int = 50

    # Telephony rules
    MAX_CONCURRENT_CALLS_PER_COMPANY: int = 20
    CALL_SLOT_TTL_SECONDS: int = 1800  # safety expiry so a lost hangup webhook can't leak a slot
    PIN_MAX_ATTEMPTS: int = 3
    MIN_CALL_DURATION_SECONDS: int = 120  # shorter hangup => CALL_INTERRUPTED
    MAX_CALL_ATTEMPTS: int = 3

    # Legacy Twilio (optional)
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_PHONE_NUMBER: str = ""


    @field_validator("DATABASE_URL")
    @classmethod
    def _require_postgres(cls, v: str) -> str:
        if not v.lower().startswith(("postgres://", "postgresql://", "postgresql+asyncpg://")):
            raise ValueError("DATABASE_URL must be a Neon/Postgres URL (postgresql://...). SQLite is not supported.")
        return v

    @field_validator("REDIS_URL")
    @classmethod
    def _require_redis(cls, v: str) -> str:
        if not v.lower().startswith(("redis://", "rediss://")):
            raise ValueError("REDIS_URL must be an Upstash Redis URL (rediss://...).")
        if v.lower().startswith("redis://") and "localhost" in v:
            raise ValueError("REDIS_URL points at localhost. Use your Upstash rediss:// URL.")
        return v

    @property
    def async_database_url(self) -> str:
        """Neon URL rewritten for SQLAlchemy+asyncpg (driver prefix; libpq-only query params removed)."""
        return _split_db_url(self.DATABASE_URL)[0]

    @property
    def database_connect_args(self) -> dict:
        """asyncpg takes TLS via connect_args, not ?sslmode=. Neon always requires TLS."""
        return {"ssl": True}

    @property
    def broker_url(self) -> str:
        return self.CELERY_BROKER_URL or self.REDIS_URL

    @property
    def result_backend_url(self) -> str:
        return self.CELERY_RESULT_BACKEND or self.REDIS_URL


def _split_db_url(url: str):
    parts = urlsplit(url)
    scheme = "postgresql+asyncpg"
    drop = {"sslmode", "channel_binding", "options", "sslrootcert"}
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if k.lower() not in drop])
    return urlunsplit((scheme, parts.netloc, parts.path, query, "")), parts


settings = Settings()
