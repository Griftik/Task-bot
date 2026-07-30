"""Domain types: the Task record and its enums."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


class TaskCategory(str, Enum):
    """Whether Claude can finish the task itself, or it needs the human."""

    AI = "ai"  # Claude can complete it end to end (research, drafting, code…).
    PERSONAL = "personal"  # Needs the user (a call, an errand, a decision…).


class TaskStatus(str, Enum):
    PENDING = "pending"  # Waiting to be done (personal) or queued (ai).
    IN_PROGRESS = "in_progress"  # Claude is working on it right now.
    DONE = "done"
    CANCELLED = "cancelled"
    FAILED = "failed"  # An AI task Claude could not complete.


class Priority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def rank(self) -> int:
        return {"high": 0, "medium": 1, "low": 2}[self.value]

    @property
    def emoji(self) -> str:
        return {"high": "🔴", "medium": "🟡", "low": "🟢"}[self.value]


# Statuses that still need attention (shown in lists, focus and the brief).
OPEN_STATUSES = (TaskStatus.PENDING, TaskStatus.IN_PROGRESS)


@dataclass
class Task:
    id: int | None
    chat_id: int
    title: str
    description: str
    category: TaskCategory
    status: TaskStatus
    priority: Priority
    created_at: datetime
    remind_at: datetime | None = None
    result: str | None = None
    source_text: str = ""

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES

    def status_emoji(self) -> str:
        return {
            TaskStatus.PENDING: "⬜️",
            TaskStatus.IN_PROGRESS: "⏳",
            TaskStatus.DONE: "✅",
            TaskStatus.CANCELLED: "🚫",
            TaskStatus.FAILED: "⚠️",
        }[self.status]


def utcnow() -> datetime:
    """Timezone-aware UTC now. Centralised so tests can reason about it."""
    return datetime.now(timezone.utc)
