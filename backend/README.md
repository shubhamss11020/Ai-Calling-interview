# TalentVoice Backend

FastAPI service that powers resume screening, the AI interview agent and the live phone-call pipeline.

The full documentation (architecture, configuration, API overview, testing, deployment) is in the **[project README](../README.md)**. This file only covers backend-specific commands.

## Run
```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env        # fill in DATABASE_URL, REDIS_URL and the API keys
uvicorn app.main:app --port 8000
```

## Useful commands
```powershell
pytest tests -q                                   # tests (DB tests use a throw-away schema)
python -m evals.run_evals [--live]                # agent evaluations
python -m scripts.simulate_call --base http://127.0.0.1:8000
celery -A app.workers.celery_app.celery_app worker -Q screening --pool=solo   # only if ASYNC_PROCESSING=true
```

## More
- [Debugging guide](docs/DEBUGGING.md): request-ID tracing, log lines, symptom-to-file lookup
- [System design](docs/BACKEND_SYSTEM_DESIGN.md): original architecture document
