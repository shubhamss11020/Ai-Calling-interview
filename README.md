# TalentVoice

AI-assisted recruitment platform. It screens incoming resumes against a job, lets a recruiter shortlist candidates, and then **phones each shortlisted candidate for an AI-led technical interview**, producing a transcript and a scorecard.

| Phase | What happens |
| --- | --- |
| **1. Screening** | Resume upload (PDF/DOCX/TXT) → parsing → deterministic eligibility check → AI skill match with evidence → recruiter shortlists. |
| **2. Interview** | Outbound call via Vobiz → candidate enters a 6-digit PIN → live, voice-to-voice interview → automatic scoring. |

---

## Contents
1. [Architecture](#architecture)
2. [Tech stack](#tech-stack)
3. [Quick start](#quick-start)
4. [Configuration](#configuration)
5. [Using the dashboard](#using-the-dashboard)
6. [How a phone interview works](#how-a-phone-interview-works)
7. [AI design and safeguards](#ai-design-and-safeguards)
8. [API overview](#api-overview)
9. [Testing and evaluation](#testing-and-evaluation)
10. [Deployment notes](#deployment-notes)
11. [Project layout](#project-layout)
12. [Known limitations](#known-limitations)

---

## Architecture

```
                         ┌────────────────────────┐
  Recruiter ───────────► │  Next.js dashboard     │
                         └───────────┬────────────┘
                                     │ REST
                         ┌───────────▼────────────┐        ┌──────────────────┐
                         │  FastAPI backend       │◄──────►│ Neon Postgres    │
                         │  • screening pipeline  │        │ + pgvector (RAG) │
                         │  • interview agent     │        └──────────────────┘
                         │  • voice session       │        ┌──────────────────┐
                         └───┬───────────┬────────┘◄──────►│ Upstash Redis    │
                  webhooks / │           │ LLM / STT / TTS  │ (queues, limits) │
                  audio WS   │           ▼                  └──────────────────┘
                         ┌───▼──────┐  Groq · Gemini · Deepgram
  Candidate's phone ◄───►│  Vobiz   │
                         └──────────┘
```

**Screening pipeline.** Stage 1 is plain code (experience floor, mandatory skills) and rejects clear mismatches at zero LLM cost. Stage 2 asks an LLM for a score, matched/missing skills and a written justification, retrieving only the resume passages relevant to the job (RAG). Every skill the LLM claims must be present in the resume, or it is dropped.

**Interview agent.** A LangGraph graph decides each turn: follow up on a thin answer, move to the next uncovered skill, or wrap up. It draws on the job's question bank and the candidate's resume, and an output guard blocks protected-topic questions, leaked scores and repeated questions.

**State machine.** Applications move through a fixed set of states and invalid moves are rejected:
`APPLIED → PROCESSING → SUITABLE / MANUAL_REVIEW / REJECTED → SHORTLISTED → INTERVIEW_QUEUED → IN_PROGRESS → COMPLETED → EVALUATED → HIRED`

---

## Tech stack

| Layer | Technology |
| --- | --- |
| Frontend | Next.js 16, React 19, Tailwind CSS 4 |
| Backend | Python 3.12+, FastAPI, async SQLAlchemy 2, Pydantic v2 |
| Data | Neon Postgres with `pgvector`; Upstash Redis |
| Background work | Celery (optional; see `ASYNC_PROCESSING`) |
| AI | Groq (`gpt-oss`), Gemini (`gemini-2.5-flash`, embeddings), LangGraph, LangSmith tracing |
| Voice | Vobiz (telephony), Deepgram (speech-to-text `nova-3`, text-to-speech `aura`) |

The database and Redis are **hosted services only**. SQLite and local Postgres/Redis are not supported.

---

## Quick start

### Prerequisites
- Python 3.12+ and Node.js 20+
- A [Neon](https://neon.tech) Postgres database and an [Upstash](https://upstash.com) Redis database
- API keys for Groq, Gemini and Deepgram
- For real phone calls: a [Vobiz](https://vobiz.ai) account with a calling number, and [ngrok](https://ngrok.com) (or any public HTTPS host)

### 1. Install
```powershell
# backend
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# frontend
cd ..\frontend
npm install
```

### 2. Configure
```powershell
copy backend\.env.example backend\.env   # then fill in the values, see Configuration
```

### 3. Run
```powershell
.\run-dev.ps1              # ngrok tunnel + backend (:8000) + dashboard (:3000)
.\run-dev.ps1 -NoTunnel    # same, without phone calls; use the Practice interview instead
```
`run-dev.ps1` writes the ngrok URL into `PUBLIC_BASE_URL` for you. Prefer to start things manually?
```powershell
cd backend;  uvicorn app.main:app --port 8000
cd frontend; npm run dev
```

| URL | Purpose |
| --- | --- |
| http://localhost:3000 | Dashboard |
| http://localhost:8000/docs | Interactive API docs |
| http://localhost:8000/health/ready | Readiness: database, Redis, LLM keys, telephony |

Docker is also supported: `docker-compose up --build` starts the API and the Celery workers (queues: `resume-parsing`, `screening`, `telephony`) using `backend/.env`.

---

## Configuration

All settings live in `backend/.env`. The application refuses to start without `DATABASE_URL` and `REDIS_URL`.

| Variable | Required | Description |
| --- | :---: | --- |
| `DATABASE_URL` | ✅ | Neon connection string (`postgresql://…?sslmode=require`). Driver prefix and TLS are handled automatically. Use the **pooled** host. |
| `REDIS_URL` | ✅ | Upstash TLS URL (`rediss://default:<password>@<host>:6379`). No `/N` suffix. |
| `SECRET_KEY` | ✅ | Signs JWTs and webhook tokens. Use a long random value outside development. |
| `GROQ_API_KEY` | ✅ | Interview turns, screening, scoring. |
| `GEMINI_API_KEY` | ✅ | Fallback LLM and embeddings for RAG. |
| `DEEPGRAM_API_KEY` | ✅ | Live speech-to-text and text-to-speech. |
| `VOBIZ_AUTH_ID`, `VOBIZ_AUTH_TOKEN`, `VOBIZ_PHONE_NUMBER` | calls | Without these, calls are simulated. |
| `PUBLIC_BASE_URL` | calls | Public **https** URL of this backend. Vobiz calls back to it. Set automatically by `run-dev.ps1`. |
| `ASYNC_PROCESSING` | | `false` (default for local use) runs screening inline. `true` queues it on Celery workers. |
| `GROQ_MODEL`, `GROQ_FAST_MODEL`, `GEMINI_MODEL` | | Provider models. They are retired from time to time; list what your key can use if calls return 404. |
| `VOICE_MODE` | | `stream` (default) uses the live audio pipeline; `gather` falls back to Vobiz speech recognition. |
| `VOICE_SILENCE_SECONDS`, `MAX_CALL_SECONDS`, `VOICE_ENDPOINTING_MS` | | Silence and turn-taking tuning. |
| `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT` | | Optional tracing of every LLM call, retrieval and agent turn. |
| `FULL_API_HOSTS` | | Extra hostnames allowed to use the full API (see [Deployment notes](#deployment-notes)). |
| `LOG_LEVEL` | | `INFO` (default) or `DEBUG`. |

---

## Using the dashboard

1. **New job.** Enter the title, description, required skills, minimum experience and number of interview questions.
2. **Questions.** Optionally add role-specific interview questions and scoring guides. The interviewer prefers them to inventing its own.
3. **Add candidates.** Drop in resumes. Each is parsed, screened and shown with a match score, evidence and status. Scanned or unreadable resumes go to **Needs review**.
4. **Shortlist** suitable candidates. Only shortlisted candidates can be interviewed.
5. **Start AI interviews.** Tick candidates and start. Each receives a call; the dashboard shows live status.
6. **Review.** Open a candidate to read the transcript, per-skill scores and summary, then **Mark hired** or **Reject**.

**Practice interview.** On a shortlisted candidate, *Practice interview (no phone)* runs the same agent in the browser while you type the candidate's answers. Use it to try a job's questions without placing a call.

---

## How a phone interview works

```
dispatch ─► Vobiz places call ─► candidate answers ─► PIN (keypad, 3 attempts)
        ─► <Stream> opens an audio WebSocket to the backend
        ─► caller audio ─► Deepgram STT ─► end of turn ─► agent (1 LLM call) ─► Deepgram TTS ─► caller
        ─► hangup ─► transcript scored ─► application becomes EVALUATED
```

- **Latency design.** Nothing on the hot path touches the database or embeddings; context is preloaded when the call starts. Replies are capped at about 30 words and streamed over a persistent connection. A short filler plays if a reply is slow.
- **Natural conversation.** Talking over the AI interrupts it (barge-in). Short back-channel sounds ("mm-hmm") are ignored. Silence re-asks once, then ends the call politely.
- **Resilience.** A call that drops under two minutes becomes `CALL_INTERRUPTED`; the candidate can call back with the same PIN and resume. At most 20 concurrent calls per company; the rest queue.
- **Security.** Webhook URLs carry an HMAC token bound to the interview, and the audio WebSocket verifies it. Through a public tunnel the API serves only the Vobiz webhooks and `/health`.

---

## AI design and safeguards

| Concern | Mitigation |
| --- | --- |
| Invented skills | Every claimed match must appear in the resume text; unsupported claims are removed and the score reduced. |
| Unfair or unsafe questions | An output guard blocks questions about age, family, religion, origin, health and similar topics, leaked scores, multiple questions and repeats. |
| Prompt injection | Candidate speech and resume text are treated as untrusted data; instruction-like phrases are flagged and ignored. |
| Unsupported scores | No candidate speech scores 0 without calling the LLM; very thin transcripts are capped. |
| Premature endings | The interview needs several substantive answers before it can end early. |
| Provider failure | Groq → Gemini → rule-based fallback; embeddings fall back to plain truncation. |

The AI recommends; **a recruiter always makes the decision**. Candidates should be told they are speaking with an AI and that the call is processed.

---

## API overview

Interactive documentation is at `/docs`. Main resources under `/api/v1`:

| Resource | Endpoints |
| --- | --- |
| Jobs | `POST/GET /jobs`, `PATCH /jobs/{id}`, `GET/POST/DELETE /jobs/{id}/knowledge`, `GET /jobs/{id}/knowledge/search?q=` |
| Candidates | `POST /candidates`, `POST /candidates/upload-resume`, `GET /candidates` |
| Applications | `POST /applications/jobs/{job_id}`, `GET /applications`, `PATCH /applications/{id}/status` |
| Interviews | `POST /interviews/dispatch-batch`, `GET /interviews?application_id=`, `GET /interviews/{id}`, `POST /interviews/practice`, `POST /interviews/{id}/practice/{start,turn}`, `POST /interviews/{id}/complete` |
| Carrier callbacks | `/interviews/telephony/vobiz/{answer,gather-pin,speech,hangup}` and the `/ws/vobiz/{interview}/{token}` audio stream |

Errors share one shape: `{"error": {"code", "message", "request_id"}}`.

---

## Testing and evaluation

```powershell
cd backend
pytest tests -q                              # ~50 tests; DB tests run in a throw-away schema on your Neon database
python -m evals.run_evals                    # offline evals: screening, guardrails, interviewer, scorecard
python -m evals.run_evals --live             # same, against the real LLMs; also runs retrieval quality
python -m scripts.simulate_call              # simulated phone call; reports voice latency and checks barge-in
```

- Database tests use an isolated schema that is dropped afterwards, or `TEST_DATABASE_URL` if set. Production tables are never touched, and tests never contact Vobiz or the LLM providers.
- A `--live` eval run **fails** if any LLM suite silently fell back to heuristics, so a dead model name cannot produce a false pass.
- Release gates are in `backend/evals/thresholds.json`. The datasets in `backend/evals/datasets/` are seed data: replace them with recruiter-labelled real cases.

**Debugging.** Every response carries `X-Request-ID`, and every log line includes it. See [`backend/docs/DEBUGGING.md`](backend/docs/DEBUGGING.md) for a symptom-to-file lookup, log lines to search for, and AI-specific guidance.

---

## Deployment notes

- **Region.** Deepgram, Groq and Neon are in the US. From other regions each round trip costs roughly 300 ms, which is audible on a call. Deploy the backend in a US-East region next to Neon.
- **No authentication yet.** The API has no login. By default only `localhost` can use it; requests on any other hostname may reach only the Vobiz webhooks and `/health`. Add your deployed hostnames to `FULL_API_HOSTS` only after adding authentication.
- **Process model.** Run the API with several workers behind a TLS-terminating proxy that supports WebSockets. Run Celery workers per queue if `ASYNC_PROCESSING=true`.
- **Secrets.** Keep `.env` out of version control. Rotate any key that appears in logs or screenshots.

---

## Project layout

```
backend/
  app/
    api/            REST routers (v1/) and the audio WebSocket (ws.py)
    agents/         LangGraph interview agent
    core/           settings, database, logging, errors, tracing
    models/         SQLAlchemy models (incl. pgvector tables)
    schemas/        Pydantic request/response models
    services/       screening, RAG, guardrails, telephony, orchestration, scoring
    voice/          live call session, Deepgram STT/TTS, audio helpers
    workers/        Celery tasks
  evals/            datasets, runner and release thresholds
  scripts/          simulate_call.py
  tests/            unit, integration and end-to-end tests
  docs/             system design and debugging guide
frontend/src/
  app/              dashboard page
  components/       drawer, modals, interview panel, UI primitives
  lib/              API client and pipeline helpers
run-dev.ps1         one-command local launcher
docker-compose.yml  API + Celery workers (database and Redis are hosted)
```

Design background: [`backend/docs/BACKEND_SYSTEM_DESIGN.md`](backend/docs/BACKEND_SYSTEM_DESIGN.md).

---

## Known limitations

- **No authentication or multi-tenancy** (a single default company).
- **Call recording** is not implemented (it requires Vobiz's `Record` verb), so interviews have transcripts but no audio.
- **SMS** is not available: Vobiz has no SMS API, so a candidate who exhausts their PIN attempts is simply disconnected.
- **Real-call behaviour of the audio stream** was built from Vobiz's documentation and has been exercised with a simulator; validate it on a real call in your environment.
- **Seed evaluation data** is synthetic; screening quality claims need recruiter-labelled data.
- Speech recognition is configured for English (`en-US`).
