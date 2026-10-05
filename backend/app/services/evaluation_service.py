"""Post-call scorecard: LLM evaluation of the transcript against the job's required skills,
with a deterministic heuristic fallback."""
from typing import Any, Dict, List

from app.core.tracing import trace
from app.services.llm_client import complete_json

import logging

logger = logging.getLogger(__name__)

COMPETENCIES = ["technical_depth", "problem_solving", "communication", "architecture"]


def _heuristic(transcript: List[Dict[str, Any]], job_skills: List[str]) -> Dict[str, Any]:
    answers = [t.get("text", "") for t in transcript if str(t.get("speaker", "")).lower().startswith("cand")]
    blob = " ".join(answers).lower()
    if not answers:
        return {"overall_score": 0, "summary": "No candidate responses were captured.",
                "competency_scores": {c: 0 for c in COMPETENCIES}}
    covered = [s for s in job_skills if s.lower() in blob]
    coverage = len(covered) / len(job_skills) if job_skills else 0.5
    words = len(blob.split()) / len(answers)
    communication = min(100, int(40 + words * 3))
    technical = int(30 + coverage * 70)
    scores = {"technical_depth": technical, "problem_solving": int((technical + communication) / 2),
              "communication": communication, "architecture": int(30 + coverage * 60)}
    return {
        "overall_score": int(sum(scores.values()) / len(scores)),
        "summary": f"Candidate answered {len(answers)} question(s) and referenced "
                   f"{len(covered)} of {len(job_skills)} required skills"
                   f"{' (' + ', '.join(covered) + ')' if covered else ''}.",
        "competency_scores": scores,
    }


MIN_WORDS_FOR_LLM_SCORE = 25   # below this there is too little evidence for the LLM to judge fairly
THIN_EVIDENCE_CAP = 40         # ceiling for any score derived from a very short candidate transcript


def _candidate_words(transcript: List[Dict[str, Any]]) -> int:
    return sum(
        len(str(t.get("text", "")).split())
        for t in transcript
        if str(t.get("speaker", "")).lower().startswith("cand")
    )


@trace("interview.evaluate_transcript")
async def evaluate_transcript(transcript: List[Dict[str, Any]], job_title: str, job_skills: List[str]) -> Dict[str, Any]:
    """Score an interview. Evidence rule (enforced in code, not left to the LLM): no candidate speech => 0 without
    calling the LLM, and a very thin transcript can never score above THIN_EVIDENCE_CAP."""
    words = _candidate_words(transcript)
    if words == 0:
        logger.info("Scorecard: no candidate speech captured; scoring 0 without LLM")
        return _heuristic(transcript, job_skills)
    card = await _evaluate_with_llm(transcript, job_title, job_skills)
    if words < MIN_WORDS_FOR_LLM_SCORE and card["overall_score"] > THIN_EVIDENCE_CAP:
        logger.warning("Scorecard capped: only %s candidate words but score was %s", words, card["overall_score"])
        card["overall_score"] = THIN_EVIDENCE_CAP
        card["competency_scores"] = {k: min(v, THIN_EVIDENCE_CAP) for k, v in card["competency_scores"].items()}
        card["summary"] = (card.get("summary", "") + " Score capped: too little candidate speech to assess.").strip()
    return card


async def _evaluate_with_llm(transcript: List[Dict[str, Any]], job_title: str, job_skills: List[str]) -> Dict[str, Any]:
    if transcript:
        rendered = "\n".join(f"{t.get('speaker')}: {t.get('text')}" for t in transcript)[:12000]
        llm = await complete_json(
            system=(
                "You evaluate a phone technical interview. Respond ONLY with JSON: "
                '{"overall_score": int 0-100, "summary": "3 sentences", "competency_scores": '
                '{"technical_depth": int, "problem_solving": int, "communication": int, "architecture": int}}'
            ),
            user=f"ROLE: {job_title}\nREQUIRED SKILLS: {', '.join(job_skills)}\n\nTRANSCRIPT:\n{rendered}",
            timeout=30.0,
        )
        if llm:
            try:
                return {
                    "overall_score": max(0, min(100, int(llm["overall_score"]))),
                    "summary": str(llm.get("summary", "")),
                    "competency_scores": {k: max(0, min(100, int(v))) for k, v in llm["competency_scores"].items()},
                }
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                logger.warning("LLM scorecard had unexpected shape (%s); using heuristic", exc)
    logger.info("Scorecard produced by heuristic fallback")
    return _heuristic(transcript, job_skills)
