"""Reminder scheduling and the optional daily focus brief, built on
python-telegram-bot's JobQueue.
"""

from __future__ import annotations

import datetime as dt
import logging

from telegram.ext import Application, ContextTypes, JobQueue

from .keyboards import reminder_buttons
from .models import Task, TaskStatus

logger = logging.getLogger(__name__)

_REMINDER_NAME = "reminder:{}"
_DAILY_BRIEF_NAME = "daily-brief"


def _reminder_name(task_id: int) -> str:
    return _REMINDER_NAME.format(task_id)


def remove_reminder(job_queue: JobQueue, task_id: int) -> None:
    for job in job_queue.get_jobs_by_name(_reminder_name(task_id)):
        job.schedule_removal()


def schedule_reminder(job_queue: JobQueue, task: Task) -> None:
    """(Re)schedule a one-off reminder for a task with a ``remind_at`` time."""
    if task.remind_at is None or task.id is None:
        return
    remove_reminder(job_queue, task.id)
    job_queue.run_once(
        _reminder_job,
        when=task.remind_at,
        chat_id=task.chat_id,
        data={"task_id": task.id},
        name=_reminder_name(task.id),
    )
    logger.info("Scheduled reminder for task %s at %s", task.id, task.remind_at)


async def _reminder_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    task_id = context.job.data["task_id"]
    chat_id = context.job.chat_id
    db = context.bot_data["db"]

    task = db.get_task(task_id, chat_id)
    if task is None or task.status is not TaskStatus.PENDING:
        return  # Already handled while the job was pending.

    db.clear_reminder(task_id)
    text = f"⏰ Напоминание: <b>{_escape(task.title)}</b>"
    if task.description:
        text += f"\n{_escape(task.description)}"
    await context.bot.send_message(
        chat_id,
        text,
        parse_mode="HTML",
        reply_markup=reminder_buttons(task_id),
    )


def schedule_startup(application: Application) -> None:
    """Re-arm reminders from the database and set up the daily brief."""
    job_queue = application.job_queue
    db = application.bot_data["db"]

    for task in db.pending_reminders():
        schedule_reminder(job_queue, task)

    config = application.bot_data["config"]
    if config.daily_brief is not None:
        hour, minute = config.daily_brief
        job_queue.run_daily(
            _daily_brief_job,
            time=dt.time(hour=hour, minute=minute, tzinfo=config.tzinfo),
            name=_DAILY_BRIEF_NAME,
        )
        logger.info("Daily brief scheduled for %02d:%02d %s", hour, minute, config.timezone_name)


async def _daily_brief_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    db = context.bot_data["db"]
    assistant = context.bot_data["assistant"]

    for chat_id in db.chat_ids_with_open_tasks():
        tasks = db.list_open(chat_id)
        if not tasks:
            continue
        try:
            brief = await assistant.daily_brief(tasks)
        except Exception:  # noqa: BLE001
            logger.exception("Daily brief failed for chat %s", chat_id)
            continue
        await context.bot.send_message(chat_id, f"☀️ <b>Доброе утро!</b>\n\n{_escape(brief)}", parse_mode="HTML")


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
