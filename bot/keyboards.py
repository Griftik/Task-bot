"""Inline-keyboard helpers and the callback-data vocabulary.

Callback data is a compact ``action:task_id`` string so buttons can be handled
by one dispatcher.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .models import Task, TaskCategory

DONE = "done"
CANCEL = "cancel"
SNOOZE = "snooze"  # snooze a reminder by one hour
RUN = "run"  # (re)run an AI task


def parse_callback(data: str) -> tuple[str, int] | None:
    action, _, raw_id = data.partition(":")
    if not raw_id.isdigit():
        return None
    return action, int(raw_id)


def task_buttons(task: Task) -> InlineKeyboardMarkup:
    """Action buttons shown next to a task in a list."""
    row = [InlineKeyboardButton("✅ Готово", callback_data=f"{DONE}:{task.id}")]
    if task.category is TaskCategory.AI:
        row.append(InlineKeyboardButton("🔁 Ещё раз", callback_data=f"{RUN}:{task.id}"))
    row.append(InlineKeyboardButton("🚫 Убрать", callback_data=f"{CANCEL}:{task.id}"))
    return InlineKeyboardMarkup([row])


def reminder_buttons(task_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Готово", callback_data=f"{DONE}:{task_id}"),
                InlineKeyboardButton("😴 +1 час", callback_data=f"{SNOOZE}:{task_id}"),
            ]
        ]
    )
