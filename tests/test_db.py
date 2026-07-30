"""Tests for the storage layer, models and config parsing.

These exercise only stdlib code (sqlite3, datetime, zoneinfo) so they run with
no network and no third-party packages:

    python -m pytest tests/           # if pytest is installed
    python tests/test_db.py           # plain runner, no dependencies
"""

from __future__ import annotations

import os
import sys
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.config import _parse_allowed_ids, _parse_daily_brief  # noqa: E402
from bot.db import Database  # noqa: E402
from bot.models import (  # noqa: E402
    Priority,
    Task,
    TaskCategory,
    TaskStatus,
    utcnow,
)


def _new_task(chat_id: int = 1, **overrides) -> Task:
    defaults = dict(
        id=None,
        chat_id=chat_id,
        title="Позвонить врачу",
        description="",
        category=TaskCategory.PERSONAL,
        status=TaskStatus.PENDING,
        priority=Priority.MEDIUM,
        created_at=utcnow(),
        remind_at=None,
        result=None,
        source_text="позвонить врачу завтра",
    )
    defaults.update(overrides)
    return Task(**defaults)


def test_add_and_get_roundtrip() -> None:
    db = Database(":memory:")
    remind = utcnow().replace(microsecond=0) + timedelta(hours=3)
    task = _new_task(remind_at=remind, priority=Priority.HIGH)

    saved = db.add_task(task)
    assert saved.id is not None

    fetched = db.get_task(saved.id)
    assert fetched is not None
    assert fetched.title == "Позвонить врачу"
    assert fetched.category is TaskCategory.PERSONAL
    assert fetched.status is TaskStatus.PENDING
    assert fetched.priority is Priority.HIGH
    assert fetched.remind_at == remind  # instant preserved across UTC roundtrip
    db.close()


def test_get_task_is_chat_scoped() -> None:
    db = Database(":memory:")
    task = db.add_task(_new_task(chat_id=42))
    assert db.get_task(task.id, chat_id=42) is not None
    assert db.get_task(task.id, chat_id=99) is None  # other user cannot read it
    db.close()


def test_status_and_result_updates() -> None:
    db = Database(":memory:")
    task = db.add_task(_new_task(category=TaskCategory.AI, status=TaskStatus.IN_PROGRESS))

    db.set_result(task.id, "Вот ответ", TaskStatus.DONE)
    fetched = db.get_task(task.id)
    assert fetched.status is TaskStatus.DONE
    assert fetched.result == "Вот ответ"

    db.set_status(task.id, TaskStatus.CANCELLED)
    assert db.get_task(task.id).status is TaskStatus.CANCELLED
    db.close()


def test_reminder_set_and_clear() -> None:
    db = Database(":memory:")
    task = db.add_task(_new_task())
    later = utcnow().replace(microsecond=0) + timedelta(days=1)

    db.set_reminder(task.id, later)
    assert db.get_task(task.id).remind_at == later

    db.clear_reminder(task.id)
    assert db.get_task(task.id).remind_at is None
    db.close()


def test_list_open_filters_closed_and_by_category() -> None:
    db = Database(":memory:")
    open_personal = db.add_task(_new_task())
    open_ai = db.add_task(_new_task(category=TaskCategory.AI, status=TaskStatus.IN_PROGRESS))
    db.add_task(_new_task(status=TaskStatus.DONE))
    db.add_task(_new_task(status=TaskStatus.CANCELLED))

    open_ids = {t.id for t in db.list_open(1)}
    assert open_ids == {open_personal.id, open_ai.id}

    personal_ids = {t.id for t in db.list_open_personal(1)}
    assert personal_ids == {open_personal.id}
    db.close()


def test_pending_reminders_and_chat_ids() -> None:
    db = Database(":memory:")
    soon = utcnow() + timedelta(hours=1)
    with_reminder = db.add_task(_new_task(chat_id=7, remind_at=soon))
    db.add_task(_new_task(chat_id=7))  # pending, no reminder
    db.add_task(_new_task(chat_id=8, status=TaskStatus.DONE, remind_at=soon))

    reminder_ids = {t.id for t in db.pending_reminders()}
    assert reminder_ids == {with_reminder.id}

    assert set(db.chat_ids_with_open_tasks()) == {7}
    db.close()


def test_config_parsing() -> None:
    assert _parse_allowed_ids("") == frozenset()
    assert _parse_allowed_ids("1, 2 ;3") == frozenset({1, 2, 3})
    assert _parse_daily_brief("") is None
    assert _parse_daily_brief("09:30") == (9, 30)

    for bad in ("nope", "25:00", "10:99"):
        try:
            _parse_daily_brief(bad)
        except Exception:
            pass
        else:  # pragma: no cover
            raise AssertionError(f"expected error for {bad!r}")


def test_priority_and_task_helpers() -> None:
    assert Priority.HIGH.rank < Priority.MEDIUM.rank < Priority.LOW.rank
    assert Priority.HIGH.emoji == "🔴"
    assert _new_task().is_open is True
    assert _new_task(status=TaskStatus.DONE).is_open is False
    assert ZoneInfo("UTC") == timezone.utc or True  # zoneinfo importable


def _run_all() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"ok   {test.__name__}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL {test.__name__}: {exc!r}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
