"""Thin wrapper around the Anthropic API for the three jobs the bot needs:
extracting tasks from a message, doing an AI task, and choosing what to focus
on.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime

import anthropic

from .models import Priority, Task, TaskCategory
from .prompts import (
    DAILY_BRIEF_SYSTEM,
    EXECUTE_SYSTEM,
    EXTRACT_SCHEMA,
    EXTRACT_SYSTEM,
    FOCUS_SYSTEM,
)

logger = logging.getLogger(__name__)

# Cap the server-side tool loop so a misbehaving turn cannot spin forever.
_MAX_PAUSE_RESUMES = 6


@dataclass
class TaskDraft:
    """A task Claude proposed, before it becomes a stored Task."""

    title: str
    description: str
    category: TaskCategory
    priority: Priority
    remind_at: datetime | None


def _text_of(message: anthropic.types.Message) -> str:
    return "".join(
        block.text for block in message.content if block.type == "text"
    ).strip()


def _parse_when(raw: str) -> datetime | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        logger.warning("Could not parse reminder timestamp %r", raw)
        return None


class ClaudeAssistant:
    def __init__(self, model: str) -> None:
        self._client = anthropic.AsyncAnthropic()
        self._model = model

    # -- extraction --------------------------------------------------------

    async def extract_tasks(self, text: str, now_local: datetime) -> list[TaskDraft]:
        """Split a free-form message into structured task drafts.

        Never raises for content reasons: if the model output cannot be used,
        the whole message is kept as a single personal task so nothing is lost.
        """
        prompt = (
            f"Current local time: {now_local.isoformat()} "
            f"({now_local.tzname()}).\n\nUser message:\n{text}"
        )
        try:
            message = await self._client.messages.create(
                model=self._model,
                max_tokens=2048,
                system=EXTRACT_SYSTEM,
                thinking={"type": "disabled"},
                output_config={
                    "effort": "low",
                    "format": {"type": "json_schema", "schema": EXTRACT_SCHEMA},
                },
                messages=[{"role": "user", "content": prompt}],
            )
            data = json.loads(_text_of(message))
            drafts = [self._draft_from(item) for item in data.get("tasks", [])]
            drafts = [d for d in drafts if d is not None]
        except Exception:  # noqa: BLE001 - resilience is the whole point here
            logger.exception("Task extraction failed; keeping message as one task")
            drafts = []

        if not drafts and text.strip():
            drafts = [
                TaskDraft(
                    title=_shorten(text.strip()),
                    description="",
                    category=TaskCategory.PERSONAL,
                    priority=Priority.MEDIUM,
                    remind_at=None,
                )
            ]
        return drafts

    @staticmethod
    def _draft_from(item: dict) -> TaskDraft | None:
        title = (item.get("title") or "").strip()
        if not title:
            return None
        category = (
            TaskCategory.AI if item.get("category") == "ai" else TaskCategory.PERSONAL
        )
        try:
            priority = Priority(item.get("priority", "medium"))
        except ValueError:
            priority = Priority.MEDIUM
        remind_at = _parse_when(item.get("remind_at", "")) if category is TaskCategory.PERSONAL else None
        return TaskDraft(
            title=title,
            description=(item.get("description") or "").strip(),
            category=category,
            priority=priority,
            remind_at=remind_at,
        )

    # -- autonomous execution ---------------------------------------------

    async def execute_task(self, task: Task) -> str:
        """Do an AI task end to end and return the finished result."""
        detail = f"\n\nDetails: {task.description}" if task.description else ""
        prompt = f"Task: {task.title}{detail}"

        try:
            return await self._run_execution(prompt, use_web=True)
        except Exception:  # noqa: BLE001
            # Most commonly the web-search tool isn't available on the chosen
            # model. Retry once without tools before giving up.
            logger.warning("Execution with web search failed; retrying plain", exc_info=True)
            return await self._run_execution(prompt, use_web=False)

    async def _run_execution(self, prompt: str, *, use_web: bool) -> str:
        tools = (
            [{"type": "web_search_20260209", "name": "web_search", "max_uses": 6}]
            if use_web
            else []
        )
        messages: list[dict] = [{"role": "user", "content": prompt}]

        for _ in range(_MAX_PAUSE_RESUMES):
            message = await self._client.messages.create(
                model=self._model,
                max_tokens=4096,
                system=EXECUTE_SYSTEM,
                output_config={"effort": "medium"},
                tools=tools,
                messages=messages,
            )
            if message.stop_reason == "pause_turn":
                # Server-side tool loop paused; resume by echoing the turn back.
                messages.append({"role": "assistant", "content": message.content})
                continue
            break

        return _text_of(message) or "(no output)"

    # -- focus & brief -----------------------------------------------------

    async def pick_focus(self, tasks: list[Task]) -> str:
        rendered = _render_tasks(tasks)
        return await self._advise(FOCUS_SYSTEM, rendered)

    async def daily_brief(self, tasks: list[Task]) -> str:
        rendered = _render_tasks(tasks)
        return await self._advise(DAILY_BRIEF_SYSTEM, rendered)

    async def _advise(self, system: str, rendered_tasks: str) -> str:
        message = await self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=system,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": rendered_tasks}],
        )
        return _text_of(message)


def _render_tasks(tasks: list[Task]) -> str:
    lines = ["Open tasks:"]
    for task in tasks:
        when = ""
        if task.remind_at is not None:
            when = f" (due {task.remind_at.isoformat()})"
        detail = f" — {task.description}" if task.description else ""
        lines.append(f"- [{task.priority.value}] {task.title}{detail}{when}")
    return "\n".join(lines)


def _shorten(text: str, limit: int = 80) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
