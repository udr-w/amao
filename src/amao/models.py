from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class MilestoneStatus(str, Enum):
    """Mirrors the actual states the orchestrator transitions milestones through."""

    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    HALTED = "HALTED"


@dataclass(frozen=True, slots=True)
class Milestone:
    id: int
    title: str
    description: str
    status: MilestoneStatus
    attempts: int
    last_error: str | None


@dataclass(frozen=True, slots=True)
class ReviewResult:
    status: str  # "APPROVED" | "REJECTED"
    feedback: str

    @property
    def approved(self) -> bool:
        return self.status == "APPROVED"


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    status: str
    diff: str


@dataclass(frozen=True, slots=True)
class ProgressSummary:
    total: int
    pending: int
    in_progress: int
    completed: int
    halted: int
    current_milestone_title: str | None
    current_milestone_attempts: int
    average_completed_seconds: float | None
    estimated_remaining_seconds: float | None
