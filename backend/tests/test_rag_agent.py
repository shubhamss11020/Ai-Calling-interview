import pytest

from app.agents import interview_agent as agent
from app.core import tracing
from app.services import rag_service
from app.services.embedding_service import _normalise, embeddings_available


# ---------------------------------------------------------------- chunking

def test_chunk_text_short_text_is_single_chunk():
    assert rag_service.chunk_text("Python developer with 5 years of experience.") == [
        "Python developer with 5 years of experience."
    ]


def test_chunk_text_respects_size_and_overlaps():
    text = "\n\n".join(f"Project {i}: " + ("built a service " * 20) for i in range(12))
    chunks = rag_service.chunk_text(text, size=500, overlap=80)
    assert len(chunks) > 1
    assert all(len(c) <= 500 + 80 for c in chunks)
    assert "Project 0" in chunks[0] and "Project 11" in chunks[-1]


def test_chunk_text_splits_one_huge_paragraph():
    chunks = rag_service.chunk_text("x" * 2500, size=1000, overlap=100)
    assert len(chunks) >= 3 and all(len(c) <= 1000 for c in chunks)


def test_chunk_text_empty():
    assert rag_service.chunk_text("   ") == []


def test_normalise_unit_length():
    v = _normalise([3.0, 4.0])
    assert abs(sum(x * x for x in v) - 1.0) < 1e-9


def test_rag_degrades_without_gemini_key():
    assert embeddings_available() is False


@pytest.mark.asyncio
async def test_retrieval_returns_empty_without_embeddings():
    assert await rag_service.retrieve_resume_chunks(None, "cand", "python") == []
    assert await rag_service.retrieve_job_knowledge(None, "job", "python") == []
    ctx = await rag_service.resume_context_for_job(None, "c", "raw resume", "T", [], "")
    assert ctx == "raw resume"


# ---------------------------------------------------------------- tracing

def test_trace_is_noop_when_disabled():
    def fn(x):
        return x

    assert tracing.trace("x")(fn) is fn
    assert tracing.configure_tracing() is False


def test_trace_truncates_long_inputs():
    out = tracing._process_inputs({"text": "a" * 5000, "db": object(), "n": 3})
    assert "db" not in out and out["n"] == 3 and len(out["text"]) < 500


# ---------------------------------------------------------------- LangGraph interviewer

BASE = {
    "job_id": "j", "job_title": "Backend Engineer", "candidate_id": "c",
    "required_skills": ["Python", "PostgreSQL", "Redis"],
    "max_turns": 6, "ai_turns": 2, "covered_skills": [], "followups_used": 0,
}


@pytest.fixture
def fake_llm(monkeypatch):
    calls = {"guidance": []}

    async def fake_turn(history, title, skills, guidance=""):
        calls["guidance"].append(guidance)
        return "next question?"

    monkeypatch.setattr(agent.voice_ai_service, "generate_interview_turn_llm", fake_turn)

    async def no_llm(*a, **k):
        return None  # force the heuristic assessor

    monkeypatch.setattr(agent, "complete_json", no_llm)
    return calls


def _hist(answer):
    return [{"role": "assistant", "content": "Tell me about your background"}, {"role": "user", "content": answer}]


@pytest.mark.asyncio
async def test_thin_answer_triggers_one_followup_then_moves_on(fake_llm):
    s1 = await agent.run_turn({**BASE, "history": _hist("I use it.")})
    assert s1["followups_used"] == 1 and "follow-up" in fake_llm["guidance"][-1]

    s2 = await agent.run_turn({**BASE, "history": _hist("Not much."), "followups_used": 1})
    assert s2["followups_used"] == 0 and "new topic" in fake_llm["guidance"][-1]


@pytest.mark.asyncio
async def test_detailed_answer_marks_skill_covered_and_advances(fake_llm):
    long_answer = ("I designed our Python services with PostgreSQL as the system of record, tuned slow queries with "
                   "EXPLAIN ANALYZE, added composite indexes and partitioned the largest tables by month.")
    s = await agent.run_turn({**BASE, "history": _hist(long_answer)})
    assert {"Python", "PostgreSQL"} <= set(s["covered_skills"])
    assert "Redis" in fake_llm["guidance"][-1]  # moves to the remaining uncovered skill
    assert s["done"] is False


@pytest.mark.asyncio
async def test_wraps_up_at_turn_limit(fake_llm):
    s = await agent.run_turn({**BASE, "ai_turns": 6, "history": _hist("anything")})
    assert s["done"] is True and s["reply"] == agent.CLOSING and fake_llm["guidance"] == []


@pytest.mark.asyncio
async def test_wraps_up_early_when_all_skills_evidenced(fake_llm):
    answer = "I run Python services backed by PostgreSQL and use Redis for caching and rate limiting across regions."
    s = await agent.run_turn({**BASE, "ai_turns": 3, "valid_answers": 2, "history": _hist(answer)})
    assert s["done"] is True


@pytest.mark.asyncio
async def test_does_not_end_early_on_a_single_real_answer(fake_llm):
    answer = "I run Python services backed by PostgreSQL and use Redis for caching and rate limiting across regions."
    s = await agent.run_turn({**BASE, "ai_turns": 3, "valid_answers": 0, "history": _hist(answer)})
    assert s["done"] is False and s["valid_answers"] == 1
