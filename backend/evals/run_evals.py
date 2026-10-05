"""Agent evaluation harness.

    python -m evals.run_evals              # offline: no LLM calls (deterministic, runs in CI)
    python -m evals.run_evals --live       # uses the LLM keys in .env (Groq/Gemini); includes requires_llm cases
    python -m evals.run_evals --suite screening --suite guardrails

Writes evals/results/latest.json and exits non-zero if any threshold in evals/thresholds.json is missed.
Safety suites (guardrails, injection) are hard gates; quality suites are baselines to ratchet up.
"""
import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List

ROOT = Path(__file__).parent
DATASETS = ROOT / "datasets"


def _load(name: str) -> Dict[str, Any]:
    return json.loads((DATASETS / name).read_text(encoding="utf-8"))


def _force_offline() -> None:
    from app.core.config import settings

    for key in ("GROQ_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY"):
        setattr(settings, key, "")
    settings.RAG_ENABLED = False
    settings.OLLAMA_BASE_URL = ""  # no local-model fallback either: offline means deterministic


# ----------------------------------------------------------------------------- suites

async def suite_screening(live: bool) -> Dict[str, Any]:
    """Parse resume -> Stage 1 -> Stage 2, exactly as the pipeline does, minus the database."""
    from app.services.guardrails import ground_skills
    from app.services.resume_service import resume_service
    from app.services.screening_service import screening_service

    data = _load("screening.json")
    rows: List[Dict[str, Any]] = []
    skipped = 0
    for case in data["cases"]:
        if case.get("requires_llm") and not live:
            skipped += 1
            continue
        job = {**data["job"], **case.get("job_override", {})}
        parsed = resume_service.parse_resume_text(case["resume_text"])
        det_ok, det_notes = screening_service.evaluate_deterministic_stage(
            parsed["extracted_skills"], parsed["total_experience_years"], job["required_skills"], job["min_experience_years"]
        )
        if not det_ok:
            decision, score, matched = "NOT_SUITABLE", 0, []
        else:
            ai = await screening_service.evaluate_ai_stage(
                candidate_skills=parsed["extracted_skills"],
                candidate_exp=parsed["total_experience_years"],
                raw_resume_text=case["resume_text"],
                job_title=job["title"],
                job_description=data["job"]["description"],
                job_required_skills=job["required_skills"],
                job_min_exp=job["min_experience_years"],
            )
            decision, score, matched = ai.decision, ai.match_score, ai.matched_skills

        _, ungrounded = ground_skills(matched, case["resume_text"], parsed["extracted_skills"])
        forbidden = [s for s in case["expected"].get("must_not_match", []) if s.lower() in {m.lower() for m in matched}]
        rows.append({
            "id": case["id"], "expected": case["expected"]["decision"], "got": decision, "score": score,
            "ok": decision == case["expected"]["decision"],
            "false_suitable": decision == "SUITABLE" and case["expected"]["decision"] != "SUITABLE",
            "ungrounded": ungrounded, "forbidden_matches": forbidden,
        })

    n = max(len(rows), 1)
    return {
        "metrics": {
            "cases": len(rows),
            "skipped_requires_llm": skipped,
            "decision_accuracy": round(sum(r["ok"] for r in rows) / n, 3),
            "false_suitable": sum(r["false_suitable"] for r in rows),
            "grounding_violations": sum(len(r["ungrounded"]) for r in rows),
            "must_not_match_violations": sum(len(r["forbidden_matches"]) for r in rows),
        },
        "failures": [r for r in rows if not r["ok"] or r["ungrounded"] or r["forbidden_matches"]],
    }


async def suite_guardrails(live: bool) -> Dict[str, Any]:
    from app.services.guardrails import check_interviewer_reply, looks_like_injection

    data = _load("guardrails.json")
    reply_fail, inj_fail = [], []
    for c in data["reply_cases"]:
        got = check_interviewer_reply(c["reply"])
        # every expected violation must be reported; clean replies must stay clean
        if (not c["expect"] and got) or any(e not in got for e in c["expect"]):
            reply_fail.append({"id": c["id"], "expected": c["expect"], "got": got})
    for c in data["injection_cases"]:
        got = looks_like_injection(c["text"])
        if got != c["injection"]:
            inj_fail.append({"id": c["id"], "expected": c["injection"], "got": got})
    nr, ni = len(data["reply_cases"]), len(data["injection_cases"])
    return {
        "metrics": {
            "reply_check_accuracy": round(1 - len(reply_fail) / nr, 3),
            "injection_detection_accuracy": round(1 - len(inj_fail) / ni, 3),
        },
        "failures": reply_fail + inj_fail,
    }


async def suite_interviewer(live: bool) -> Dict[str, Any]:
    """Run scripted candidates through the real LangGraph agent and check every spoken reply."""
    from app.agents.interview_agent import CLOSING, run_turn
    from app.services.guardrails import check_interviewer_reply, repeats_earlier_question

    data = _load("interview.json")
    violations, repeats, ended_ok = [], [], 0
    cases = data["interview_cases"]
    for c in cases:
        history = [{"role": "assistant", "content": "Welcome. Please tell me about your background."}]
        state = {"covered_skills": [], "followups_used": 0}
        replies: List[str] = []
        done = False
        for answer in c["answers"]:
            history.append({"role": "user", "content": answer})
            state = await run_turn({
                "job_id": "eval-job", "job_title": "Backend Engineer", "candidate_id": "eval-cand",
                "required_skills": c["required_skills"], "history": history, "max_turns": c["max_turns"],
                "ai_turns": sum(1 for m in history if m["role"] == "assistant"),
                "covered_skills": state.get("covered_skills", []), "followups_used": state.get("followups_used", 0),
                "valid_answers": state.get("valid_answers", 0),
            }, db=None)
            reply = state["reply"]
            if reply != CLOSING:
                for v in check_interviewer_reply(reply):
                    violations.append({"case": c["id"], "violation": v, "reply": reply})
                if repeats_earlier_question(reply, [m["content"] for m in history if m["role"] == "assistant"]):
                    repeats.append({"case": c["id"], "reply": reply})
            history.append({"role": "assistant", "content": reply})
            replies.append(reply)
            if state.get("done"):
                done = True
                break
        # a conversation never needs more AI turns than max_turns; finishing early is fine
        ended_ok += int(sum(1 for m in history if m["role"] == "assistant") <= c["max_turns"] + 1 or done)
    return {
        "metrics": {
            "cases": len(cases),
            "reply_violations": len(violations),
            "repeats": len(repeats),
            "ended_within_max_turns": round(ended_ok / len(cases), 3),
        },
        "failures": violations + repeats,
    }


async def suite_scorecard(live: bool) -> Dict[str, Any]:
    from app.services.evaluation_service import evaluate_transcript

    data = _load("interview.json")
    rows = []
    for c in data["scorecard_cases"]:
        card = await evaluate_transcript(c["transcript"], "Backend Engineer", c["required_skills"])
        score = card["overall_score"]
        rows.append({"id": c["id"], "score": score, "band": [c["expected_min"], c["expected_max"]],
                     "ok": c["expected_min"] <= score <= c["expected_max"]})
    by_id = {r["id"]: r["score"] for r in rows}
    ordered = by_id.get("strong", 1) > by_id.get("weak", 0)  # a better interview must never score lower
    return {
        "metrics": {"in_band_rate": round(sum(r["ok"] for r in rows) / len(rows), 3), "strong_beats_weak": ordered},
        "failures": [r for r in rows if not r["ok"]] + ([] if ordered else [{"id": "ordering", "ok": False}]),
    }


async def suite_retrieval(live: bool) -> Dict[str, Any]:
    """RAG retrieval quality against real Gemini embeddings + Neon pgvector.

    Everything happens in a throwaway schema (via SQLAlchemy schema_translate_map) that is dropped afterwards,
    so production tables are never read or written. Requires GEMINI_API_KEY and DATABASE_URL."""
    import uuid

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.config import settings
    from app.core.database import Base
    from app.models import Company, Candidate, Job  # noqa: F401  (registers all tables on Base)
    from app.services import rag_service
    from app.services.embedding_service import embeddings_available

    if not embeddings_available():
        raise RuntimeError("retrieval suite needs GEMINI_API_KEY and RAG_ENABLED=true")

    data = _load("retrieval.json")
    schema = f"eval_{uuid.uuid4().hex[:10]}"
    base_engine = create_async_engine(settings.async_database_url, connect_args=settings.database_connect_args, poolclass=NullPool)
    scoped = base_engine.execution_options(schema_translate_map={None: schema})
    knowledge_ranks: List[int] = []
    resume_ranks: List[int] = []
    failures: List[Dict[str, Any]] = []
    try:
        async with base_engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        async with scoped.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with AsyncSession(scoped, expire_on_commit=False) as db:
            company = Company(name="Eval Co", domain="eval.invalid")
            db.add(company)
            await db.flush()
            job = Job(company_id=company.id, title=data["job"]["title"], description=data["job"]["description"])
            cand = Candidate(full_name="Eval Candidate", email="eval@eval.invalid")
            db.add_all([job, cand])
            await db.commit()

            content_to_id = {}
            for item in data["knowledge_items"]:
                await rag_service.add_job_knowledge(db, job.id, item["kind"], item["content"], item["topic"])
                content_to_id[item["content"]] = item["id"]
            await rag_service.index_candidate(db, cand.id, data["resume_text"])

            for q in data["knowledge_queries"]:
                hits = await rag_service.retrieve_job_knowledge(db, job.id, q["q"], k=5)
                ids = [content_to_id.get(h["content"]) for h in hits]
                rank = ids.index(q["expect"]) + 1 if q["expect"] in ids else 0
                knowledge_ranks.append(rank)
                if rank != 1:
                    failures.append({"corpus": "knowledge", "q": q["q"][:80], "expect": q["expect"], "rank": rank, "got_top3": ids[:3]})

            for q in data["resume_queries"]:
                chunks = await rag_service.retrieve_resume_chunks(db, cand.id, q["q"], k=5)
                rank = next((i + 1 for i, c in enumerate(chunks) if q["expect_contains"] in c), 0)
                resume_ranks.append(rank)
                if rank != 1:
                    failures.append({"corpus": "resume", "q": q["q"][:80], "expect": q["expect_contains"], "rank": rank})
    finally:
        async with base_engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await base_engine.dispose()

    def stats(ranks: List[int], prefix: str) -> Dict[str, float]:
        n = max(len(ranks), 1)
        return {
            f"{prefix}_recall_at_1": round(sum(1 for r in ranks if r == 1) / n, 3),
            f"{prefix}_recall_at_3": round(sum(1 for r in ranks if 1 <= r <= 3) / n, 3),
            f"{prefix}_recall_at_5": round(sum(1 for r in ranks if r >= 1) / n, 3),
            f"{prefix}_mrr": round(sum(1 / r for r in ranks if r) / n, 3),
        }

    return {"metrics": {**stats(knowledge_ranks, "knowledge"), **stats(resume_ranks, "resume")}, "failures": failures}


SUITES: Dict[str, Callable] = {
    "screening": suite_screening,
    "guardrails": suite_guardrails,
    "interviewer": suite_interviewer,
    "scorecard": suite_scorecard,
    "retrieval": suite_retrieval,  # live only: needs Gemini + Neon
}

OFFLINE_SUITES = ["screening", "guardrails", "interviewer", "scorecard"]
LLM_SUITES = {"screening", "interviewer", "scorecard"}


# ----------------------------------------------------------------------------- gating

def check_thresholds(results: Dict[str, Any], thresholds: Dict[str, Any]) -> List[str]:
    """Keys ending _min need metric >= value; _max need metric <= value."""
    problems = []
    for suite, rules in thresholds.items():
        if suite.startswith("_") or suite not in results:
            continue
        metrics = results[suite]["metrics"]
        for rule, limit in rules.items():
            if rule.endswith("_min"):
                name, ok = rule[:-4], lambda v, lim=limit: v >= lim
            else:
                name, ok = rule[:-4], lambda v, lim=limit: v <= lim
            if name not in metrics:
                problems.append(f"{suite}.{name}: metric missing")
            elif not ok(metrics[name]):
                problems.append(f"{suite}.{name} = {metrics[name]} violates {rule} = {limit}")
        if metrics.get("llm_actually_used") is False:
            problems.append(f"{suite}: live run fell back to heuristics ({metrics.get('llm_fallbacks')} fallbacks, "
                            f"{metrics.get('llm_calls_ok')} real LLM calls): results are not valid evidence")
        if "strong_beats_weak" in metrics and not metrics["strong_beats_weak"]:
            problems.append(f"{suite}.strong_beats_weak is False")
    return problems


async def run(suites: List[str], live: bool) -> Dict[str, Any]:
    from app.services.llm_client import llm_stats

    results = {}
    for name in suites:
        llm_stats.clear()
        results[name] = await SUITES[name](live)
        if live and name in LLM_SUITES:
            ok = llm_stats["json_ok"] + llm_stats["turn_ok"]
            fallbacks = llm_stats["json_fallback"] + llm_stats["turn_fallback"]
            results[name]["metrics"]["llm_calls_ok"] = ok
            results[name]["metrics"]["llm_fallbacks"] = fallbacks
            # a "live" run that quietly used heuristics proves nothing: fail it
            results[name]["metrics"]["llm_actually_used"] = ok > 0 and fallbacks == 0
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true", help="call real LLMs using keys from .env")
    parser.add_argument("--suite", action="append", choices=list(SUITES), help="repeatable; default = all (retrieval only with --live)")
    args = parser.parse_args()

    logging.disable(logging.WARNING)  # keep the report readable; failures are listed below
    if not args.live and not (args.suite and "retrieval" in args.suite):
        _force_offline()
    suites = args.suite or (list(SUITES) if args.live else OFFLINE_SUITES)
    results = asyncio.run(run(suites, args.live))
    problems = check_thresholds(results, _load_thresholds())

    out = {"ran_at": datetime.now(timezone.utc).isoformat(), "mode": "live" if (args.live or "retrieval" in suites) else "offline",
           "results": results, "threshold_failures": problems}
    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results" / "latest.json").write_text(json.dumps(out, indent=2), encoding="utf-8")

    print(f"\nEval run ({out['mode']})")
    for name, res in results.items():
        print(f"  {name:12} {json.dumps(res['metrics'])}")
        for f in res["failures"][:8]:
            print(f"      - {json.dumps(f)[:200]}")
    print("\n" + ("PASS: all thresholds met" if not problems else "FAIL:\n  " + "\n  ".join(problems)))
    return 1 if problems else 0


def _load_thresholds() -> Dict[str, Any]:
    return json.loads((ROOT / "thresholds.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    sys.exit(main())
