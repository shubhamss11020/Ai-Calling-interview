"""CI gate: the offline eval suites must meet evals/thresholds.json. Fails the build on a safety regression
(protected-topic questions, leaked scores, prompt injection, ungrounded skill claims, repeated questions)."""
import pytest

from evals import run_evals


@pytest.mark.asyncio
async def test_offline_evals_meet_thresholds():
    run_evals._force_offline()
    results = await run_evals.run(run_evals.OFFLINE_SUITES, live=False)
    problems = run_evals.check_thresholds(results, run_evals._load_thresholds())
    detail = {k: v["failures"][:3] for k, v in results.items() if v["failures"]}
    assert not problems, f"{problems}\n{detail}"


def test_grounding_rejects_unsupported_skills():
    from app.services.guardrails import ground_skills

    good, bad = ground_skills(["Python", "Redis", "Go"], "Built Python APIs at Google using caching.", ["Python"])
    assert good == ["Python"] and set(bad) == {"Redis", "Go"}


def test_safe_question_never_repeats():
    from app.services.guardrails import safe_question

    asked: list[str] = []
    for _ in range(5):
        asked.append(safe_question(["Python"], asked))
    assert len(set(asked)) == 5
