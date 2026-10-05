# Debugging Guide

## 1. Trace one request end to end
Every HTTP response carries `X-Request-ID` (send your own header to pin it). Every log line includes it:

```
14:02:11 INFO    [a1b2c3d4e5f6] app.pipeline_service: application=… Stage 2 score=82 decision=SUITABLE -> SUITABLE
14:02:11 INFO    [a1b2c3d4e5f6] app.http: POST /api/v1/applications/jobs/… -> 201 (412ms) rid=a1b2c3d4e5f6
```
Celery logs use `[task:<id8>]` instead. Error bodies include the same ID:
`{"error": {"code", "message", "request_id", "details"?}, "detail": …}` (`detail` is kept for older clients).
A user reporting "500, request_id X" → `grep X` in the API logs gives the stack trace.

## 2. Logging config
| Setting | Effect |
| --- | --- |
| `LOG_LEVEL=DEBUG` | verbose, also prints SQL statements |
| `ENVIRONMENT=production` | one JSON object per line (`ts, level, logger, request_id, msg, exc`) |

Loggers: `app.http` (requests), `app.errors`, `app.worker`, plus one per module (`app.services.*`).
Fallbacks are never silent: LLM/TTS/Redis/PDF failures log a WARNING naming the provider and reason.

## 3. Health checks
- `GET /health` – process is up.
- `GET /health/ready` – DB (503 if down), Redis, which LLM keys are set, and whether telephony is `configured` or `simulated`.
  Start here when "nothing works": usually a wrong Neon/Upstash URL, a missing key (simulated mode), or `redis` unavailable (call limiter degrades to per-process and logs an error).

## 4. Where to look by symptom
| Symptom | Layer / file | Log lines to grep |
| --- | --- | --- |
| Candidate not screened / odd score | `services/pipeline_service.py`, `screening_service.py` | `Screening application=`, `Stage 1`, `Stage 2`, `unexpected shape` |
| Everything scores by heuristic | `services/llm_client.py` | `Groq`, `Gemini`, `No LLM provider` |
| Application stuck in `PROCESSING`/`APPLIED` | `ASYNC_PROCESSING=true` and no `screening` worker running | `Task start`, `Task FAILED` |
| Resume has no skills / `MANUAL_REVIEW` | `services/resume_service.py` | `Could not open PDF`, `SCANNED_DOCUMENT_REQUIRES_OCR` |
| 409 on status change | `services/state_machine.py` | `rejected transition` |
| Dispatch returns 409 | app not `SHORTLISTED` or interview already active | response `detail.skipped[]` lists the reason per application |
| Interview stays `QUEUED` | call cap or Vobiz error | `at call cap`, `dial attempt … failed` |
| Call placed, nothing happens | webhooks can't reach us: check `PUBLIC_BASE_URL` | `CALLING call_id=`, then absence of `PIN verified` |
| Candidate can't get in | `api/v1/interviews.py` gather-pin | `Wrong PIN on call` |
| Call dropped | `interview_orchestrator.handle_hangup` | `hangup duration=… -> CALL_INTERRUPTED` |
| No scorecard | `evaluation_service.py` | `evaluating N transcript turns`, `heuristic fallback` |

## 4b. AI-specific debugging
- **LangSmith** (`LANGSMITH_TRACING=true`): one trace per interview turn (`agent.interview_turn`) with child runs for
  retrieval, assessment and the LLM call, plus `llm.complete_json` for screening and scoring. Start here for "why did it ask that / score that".
- `GET /jobs/{id}/knowledge/search?q=...` shows exactly what the interviewer would retrieve for a candidate answer.
- Logs: `Indexed candidate=… chunks`, `No resume chunks … falling back`, `Embedding HTTP …`, `agent turn: route-covered=…`.
- Screening has no RAG context when `GEMINI_API_KEY` is missing (embedding calls are skipped); `/health/ready` shows which LLM keys are set.

## 4c. Evals and guardrails
```bash
python -m evals.run_evals                 # offline, deterministic (also runs in pytest as tests/test_evals.py)
python -m evals.run_evals --live          # real LLMs from .env, adds requires_llm cases
python -m evals.run_evals --suite screening
python -m evals.run_evals --suite retrieval   # Gemini embeddings + Neon pgvector, in a throwaway schema
```
A `--live` run FAILS if any LLM suite fell back to heuristics (`llm_actually_used`), so a dead model name or bad key can't produce a fake pass.
Results land in `evals/results/latest.json`; gates live in `evals/thresholds.json`.
Datasets: `evals/datasets/*.json` (seed data: replace with recruiter-labelled real cases and add every production failure).
Runtime enforcement uses the same code (`services/guardrails.py`): the agent's `guard` node (one short question, no protected topics,
no leaked scores, no repeats, then a safe canned question), prompt-injection detection on candidate speech, and skill grounding in screening.
Log lines to grep: `failed guard`, `prompt-injection`, `ungrounded skill claim`.

**DB tests** run in a throwaway schema on your Neon database (dropped afterwards), or on `TEST_DATABASE_URL` if set;
production tables are never touched. A full run takes a few minutes because every connection crosses the network.

## 5. Code layout (request flow)
```
api/v1/*.py          HTTP only: parse, call a service, shape the response
services/            business logic (pipeline, orchestrator, screening, evaluation, telephony, notifications)
  state_machine.py   the only place that defines legal status changes
  llm_client.py      single Groq→Gemini gateway
workers/*.py         thin Celery wrappers around the same services (workers/db.py runs async DB code)
models/ schemas/     persistence vs. API contracts
core/                config, database, logging, errors
```
Because the API and workers call the same service functions, a bug reproduces the same way inline
(`ASYNC_PROCESSING=false`, easiest to step through) and in Celery.

## 6. Reproducing locally
```bash
ASYNC_PROCESSING=false LOG_LEVEL=DEBUG uvicorn app.main:app --reload   # inline processing; DB=Neon, Redis=Upstash
TEST_DATABASE_URL=<neon test branch> python -m pytest tests -q          # DB tests need a separate Neon branch
```
With no Vobiz keys calls are simulated; drive the flow by hand:
`POST /interviews/telephony/vobiz/gather-pin?interview_id=…&Digits=<pin>` →
`/speech?interview_id=…&SpeechResult=…` → `/hangup?interview_id=…&Duration=600`
(or `POST /interviews/{id}/complete` to force-finish).
