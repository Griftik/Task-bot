"""SQLite storage for tasks.

The connection is created once and only ever touched from the asyncio event
loop thread (Telegram handlers and JobQueue callbacks all run there), so no
extra locking is required. SQLite operations are tiny and fast for a personal
bot, so they run synchronously.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from .models import Priority, Task, TaskCategory, TaskStatus

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     INTEGER NOT NULL,
    title       TEXT    NOT NULL,
    description TEXT    NOT NULL DEFAULT '',
    category    TEXT    NOT NULL,
    status      TEXT    NOT NULL,
    priority    TEXT    NOT NULL,
    created_at  TEXT    NOT NULL,
    remind_at   TEXT,
    result      TEXT,
    source_text TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_tasks_chat_status ON tasks (chat_id, status);
CREATE INDEX IF NOT EXISTS idx_tasks_remind ON tasks (remind_at);
"""


def _to_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _from_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _row_to_task(row: sqlite3.Row) -> Task:
    return Task(
        id=row["id"],
        chat_id=row["chat_id"],
        title=row["title"],
        description=row["description"],
        category=TaskCategory(row["category"]),
        status=TaskStatus(row["status"]),
        priority=Priority(row["priority"]),
        created_at=_from_iso(row["created_at"]),  # type: ignore[arg-type]
        remind_at=_from_iso(row["remind_at"]),
        result=row["result"],
        source_text=row["source_text"],
    )


class Database:
    def __init__(self, path: str) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- writes ------------------------------------------------------------

    def add_task(self, task: Task) -> Task:
        cur = self._conn.execute(
            """
            INSERT INTO tasks
                (chat_id, title, description, category, status, priority,
                 created_at, remind_at, result, source_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task.chat_id,
                task.title,
                task.description,
                task.category.value,
                task.status.value,
                task.priority.value,
                _to_iso(task.created_at),
                _to_iso(task.remind_at),
                task.result,
                task.source_text,
            ),
        )
        self._conn.commit()
        task.id = cur.lastrowid
        return task

    def set_status(self, task_id: int, status: TaskStatus) -> None:
        self._conn.execute(
            "UPDATE tasks SET status = ? WHERE id = ?", (status.value, task_id)
        )
        self._conn.commit()

    def set_result(self, task_id: int, result: str, status: TaskStatus) -> None:
        self._conn.execute(
            "UPDATE tasks SET result = ?, status = ? WHERE id = ?",
            (result, status.value, task_id),
        )
        self._conn.commit()

    def clear_reminder(self, task_id: int) -> None:
        self._conn.execute(
            "UPDATE tasks SET remind_at = NULL WHERE id = ?", (task_id,)
        )
        self._conn.commit()

    def set_reminder(self, task_id: int, remind_at: datetime | None) -> None:
        self._conn.execute(
            "UPDATE tasks SET remind_at = ? WHERE id = ?",
            (_to_iso(remind_at), task_id),
        )
        self._conn.commit()

    # -- reads -------------------------------------------------------------

    def get_task(self, task_id: int, chat_id: int | None = None) -> Task | None:
        if chat_id is None:
            row = self._conn.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT * FROM tasks WHERE id = ? AND chat_id = ?",
                (task_id, chat_id),
            ).fetchone()
        return _row_to_task(row) if row else None

    def list_open(self, chat_id: int) -> list[Task]:
        rows = self._conn.execute(
            """
            SELECT * FROM tasks
            WHERE chat_id = ? AND status IN ('pending', 'in_progress')
            ORDER BY created_at ASC
            """,
            (chat_id,),
        ).fetchall()
        return [_row_to_task(r) for r in rows]

    def list_open_personal(self, chat_id: int) -> list[Task]:
        return [t for t in self.list_open(chat_id) if t.category == TaskCategory.PERSONAL]

    def pending_reminders(self) -> list[Task]:
        """Pending personal tasks that still have a reminder time set."""
        rows = self._conn.execute(
            """
            SELECT * FROM tasks
            WHERE status = 'pending' AND remind_at IS NOT NULL
            ORDER BY remind_at ASC
            """
        ).fetchall()
        return [_row_to_task(r) for r in rows]

    def chat_ids_with_open_tasks(self) -> list[int]:
        rows = self._conn.execute(
            """
            SELECT DISTINCT chat_id FROM tasks
            WHERE status IN ('pending', 'in_progress')
            """
        ).fetchall()
        return [r["chat_id"] for r in rows]
