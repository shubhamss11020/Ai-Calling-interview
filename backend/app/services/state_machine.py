"""Application pipeline finite state machine (see BACKEND_SYSTEM_DESIGN.md section 3.4)."""
from typing import Dict, Set

APPLIED = "APPLIED"
PROCESSING = "PROCESSING"
MANUAL_REVIEW = "MANUAL_REVIEW"
SUITABLE = "SUITABLE"
SHORTLISTED = "SHORTLISTED"
INTERVIEW_QUEUED = "INTERVIEW_QUEUED"
IN_PROGRESS = "IN_PROGRESS"
CALL_INTERRUPTED = "CALL_INTERRUPTED"
COMPLETED = "COMPLETED"
EVALUATED = "EVALUATED"
REJECTED = "REJECTED"
HIRED = "HIRED"

TRANSITIONS: Dict[str, Set[str]] = {
    APPLIED: {PROCESSING, REJECTED},
    PROCESSING: {SUITABLE, REJECTED, MANUAL_REVIEW},
    MANUAL_REVIEW: {PROCESSING, SUITABLE, REJECTED},
    SUITABLE: {SHORTLISTED, REJECTED},
    SHORTLISTED: {INTERVIEW_QUEUED, REJECTED},
    INTERVIEW_QUEUED: {IN_PROGRESS, SHORTLISTED, REJECTED},
    IN_PROGRESS: {COMPLETED, CALL_INTERRUPTED},
    CALL_INTERRUPTED: {IN_PROGRESS, INTERVIEW_QUEUED, COMPLETED, REJECTED},
    COMPLETED: {EVALUATED},
    EVALUATED: {HIRED, REJECTED},
    REJECTED: set(),
    HIRED: set(),
}


class InvalidTransition(ValueError):
    pass


def can_transition(current: str, target: str) -> bool:
    return current == target or target in TRANSITIONS.get(current, set())


def assert_transition(current: str, target: str) -> None:
    if not can_transition(current, target):
        allowed = ", ".join(sorted(TRANSITIONS.get(current, set()))) or "none (terminal)"
        raise InvalidTransition(f"Cannot move application from {current} to {target}. Allowed: {allowed}.")
