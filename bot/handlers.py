"""Telegram command, message and button handlers."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, ContextTypes

from . import keyboards
from .claude import ClaudeAssistant
from .config import Config
from .db import Database
from .models import Priority, Task, TaskCategory, TaskStatus, utcnow
from .scheduler import remove_reminder, schedule_reminder

logger = logging.getLogger(__name__)

# Telegram rejects messages longer than 4096 characters.
_MAX_MESSAGE = 4000

WELCOME = (
    "👋 Привет! Я — твоя внешняя память для задач.\n\n"
    "Просто напиши, что нужно сделать — можно вперемешку и в свободной форме. "
    "Я разберу это на отдельные задачи и:\n"
    "🤖 то, что можно сделать без тебя (найти, написать, посчитать, сравнить), "
    "возьму на себя и пришлю результат;\n"
    "📌 то, что за тобой (позвонить, сходить, решить), запомню и напомню.\n\n"
    "Команды:\n"
    "/tasks — открытые задачи\n"
    "/today — что на сегодня\n"
    "/focus — на чём сфокусироваться прямо сейчас\n"
    "/help — подробнее"
)

HELP = (
    "<b>Как я работаю</b>\n\n"
    "Пиши задачи обычным текстом — по одной или списком. Я сам решу, что сделать "
    "за тебя, а что оставить тебе и напомнить.\n\n"
    "Если укажешь время («завтра в 9», «через 2 часа», «в пятницу»), я поставлю "
    "напоминание.\n\n"
    "<b>Команды</b>\n"
    "/tasks — список открытых задач с кнопками\n"
    "/today — задачи на сегодня и просроченные\n"
    "/focus — выбрать одну задачу, с которой начать\n"
    "/result &lt;id&gt; — показать результат AI-задачи\n"
    "/done &lt;id&gt; — отметить выполненной\n"
    "/cancel &lt;id&gt; — убрать задачу\n"
    "/whoami — узнать свой chat id"
)


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def _db(context: ContextTypes.DEFAULT_TYPE) -> Database:
    return context.bot_data["db"]


def _assistant(context: ContextTypes.DEFAULT_TYPE) -> ClaudeAssistant:
    return context.bot_data["assistant"]


def _config(context: ContextTypes.DEFAULT_TYPE) -> Config:
    return context.bot_data["config"]


def _authorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat = update.effective_chat
    return chat is not None and _config(context).is_allowed(chat.id)


def _fmt_local(when: datetime, config: Config) -> str:
    return when.astimezone(config.tzinfo).strftime("%d.%m %H:%M")


async def _send_long(bot, chat_id: int, text: str) -> None:
    """Send text, splitting into Telegram-sized chunks on line boundaries."""
    while text:
        if len(text) <= _MAX_MESSAGE:
            await bot.send_message(chat_id, text)
            return
        cut = text.rfind("\n", 0, _MAX_MESSAGE)
        if cut <= 0:
            cut = _MAX_MESSAGE
        await bot.send_message(chat_id, text[:cut])
        text = text[cut:].lstrip("\n")


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update, context):
        return await _deny(update)
    await update.message.reply_text(WELCOME)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update, context):
        return await _deny(update)
    await update.message.reply_text(HELP, parse_mode="HTML")


async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    await update.message.reply_text(
        f"Твой chat id: <code>{chat.id}</code>\n"
        "Добавь его в TASKBOT_ALLOWED_CHAT_IDS, чтобы бот отвечал только тебе.",
        parse_mode="HTML",
    )


async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update, context):
        return await _deny(update)
    chat_id = update.effective_chat.id
    tasks = _db(context).list_open(chat_id)
    if not tasks:
        await update.message.reply_text("Открытых задач нет 🎉 Напиши новую — разберу.")
        return

    config = _config(context)
    ai = [t for t in tasks if t.category is TaskCategory.AI]
    personal = [t for t in tasks if t.category is TaskCategory.PERSONAL]

    if ai:
        await update.message.reply_text("🤖 <b>Делаю за тебя</b>", parse_mode="HTML")
        for task in ai:
            await update.message.reply_text(
                _task_line(task, config), reply_markup=keyboards.task_buttons(task)
            )
    if personal:
        await update.message.reply_text("📌 <b>За тобой</b>", parse_mode="HTML")
        for task in personal:
            await update.message.reply_text(
                _task_line(task, config), reply_markup=keyboards.task_buttons(task)
            )


async def today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update, context):
        return await _deny(update)
    chat_id = update.effective_chat.id
    config = _config(context)
    now_local = datetime.now(config.tzinfo)
    end_of_day = now_local.replace(hour=23, minute=59, second=59, microsecond=0)

    tasks = _db(context).list_open(chat_id)
    due = [
        t
        for t in tasks
        if t.category is TaskCategory.PERSONAL
        and t.remind_at is not None
        and t.remind_at.astimezone(config.tzinfo) <= end_of_day
    ]
    in_progress = [t for t in tasks if t.status is TaskStatus.IN_PROGRESS]

    if not due and not in_progress:
        await update.message.reply_text(
            "На сегодня ничего со сроком. Напиши /focus — подскажу, с чего начать."
        )
        return

    parts = []
    if due:
        parts.append("📌 <b>Сегодня / просрочено</b>")
        parts += [_task_line(t, config) for t in due]
    if in_progress:
        parts.append("\n🤖 <b>В работе</b>")
        parts += [_task_line(t, config) for t in in_progress]
    await update.message.reply_text("\n".join(parts), parse_mode="HTML")


async def focus(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update, context):
        return await _deny(update)
    chat_id = update.effective_chat.id
    tasks = _db(context).list_open_personal(chat_id)
    if not tasks:
        await update.message.reply_text(
            "Задач за тобой нет — можно выдохнуть 🙂 (AI-задачи я доделаю сам.)"
        )
        return
    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    advice = await _assistant(context).pick_focus(tasks)
    await update.message.reply_text(f"🎯 {advice}")


async def show_result(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update, context):
        return await _deny(update)
    task = _task_from_args(update, context)
    if task is None:
        await update.message.reply_text("Укажи номер: /result 12")
        return
    if task.category is not TaskCategory.AI or not task.result:
        await update.message.reply_text("У этой задачи пока нет результата.")
        return
    await _send_long(context.bot, update.effective_chat.id, f"📄 {task.title}\n\n{task.result}")


async def done_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update, context):
        return await _deny(update)
    task = _task_from_args(update, context)
    if task is None:
        await update.message.reply_text("Укажи номер: /done 12")
        return
    _mark_done(context, task)
    await update.message.reply_text(f"✅ Готово: {task.title}")


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update, context):
        return await _deny(update)
    task = _task_from_args(update, context)
    if task is None:
        await update.message.reply_text("Укажи номер: /cancel 12")
        return
    remove_reminder(context.application.job_queue, task.id)
    _db(context).set_status(task.id, TaskStatus.CANCELLED)
    await update.message.reply_text(f"🚫 Убрал: {task.title}")


# --------------------------------------------------------------------------
# free-form message: the main "brain-dump" entry point
# --------------------------------------------------------------------------

async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update, context):
        return await _deny(update)
    if not update.message or not update.message.text:
        return

    chat_id = update.effective_chat.id
    text = update.message.text
    config = _config(context)
    db = _db(context)

    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    drafts = await _assistant(context).extract_tasks(text, datetime.now(config.tzinfo))
    if not drafts:
        await update.message.reply_text("Не нашёл тут задач. Что нужно сделать?")
        return

    ai_created: list[Task] = []
    personal_created: list[Task] = []
    for draft in drafts:
        is_ai = draft.category is TaskCategory.AI
        task = Task(
            id=None,
            chat_id=chat_id,
            title=draft.title,
            description=draft.description,
            category=draft.category,
            status=TaskStatus.IN_PROGRESS if is_ai else TaskStatus.PENDING,
            priority=draft.priority,
            created_at=utcnow(),
            remind_at=draft.remind_at,
            source_text=text,
        )
        db.add_task(task)
        if is_ai:
            ai_created.append(task)
            context.application.create_task(_run_ai_task(context.application, task))
        else:
            personal_created.append(task)
            if task.remind_at is not None:
                schedule_reminder(context.application.job_queue, task)

    await update.message.reply_text(_confirmation(ai_created, personal_created, config))


def _confirmation(ai: list[Task], personal: list[Task], config: Config) -> str:
    lines: list[str] = []
    if ai:
        lines.append("🤖 Беру на себя, пришлю результат:")
        lines += [f"  • {t.title}" for t in ai]
    if personal:
        if lines:
            lines.append("")
        lines.append("📌 За тобой:")
        for t in personal:
            suffix = f"  ⏰ {_fmt_local(t.remind_at, config)}" if t.remind_at else ""
            lines.append(f"  • #{t.id} {t.priority.emoji} {t.title}{suffix}")
    if personal:
        lines.append("\n/focus — с чего начать · /tasks — все задачи")
    return "\n".join(lines)


async def _run_ai_task(application: Application, task: Task) -> None:
    """Background worker: do an AI task and deliver the result."""
    db: Database = application.bot_data["db"]
    assistant: ClaudeAssistant = application.bot_data["assistant"]
    try:
        result = await assistant.execute_task(task)
        db.set_result(task.id, result, TaskStatus.DONE)
        header = f"✅ Готово: {task.title}"
        await _send_long(application.bot, task.chat_id, f"{header}\n\n{result}")
    except Exception:  # noqa: BLE001
        logger.exception("AI task %s failed", task.id)
        db.set_status(task.id, TaskStatus.FAILED)
        await application.bot.send_message(
            task.chat_id,
            f"⚠️ Не смог выполнить сам: {task.title}\n"
            f"Оставил её за тобой — /tasks покажет.",
        )


# --------------------------------------------------------------------------
# inline buttons
# --------------------------------------------------------------------------

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not _authorized(update, context):
        return

    parsed = keyboards.parse_callback(query.data or "")
    if parsed is None:
        return
    action, task_id = parsed

    db = _db(context)
    task = db.get_task(task_id, query.message.chat.id)
    if task is None:
        await query.edit_message_text("Задача не найдена.")
        return

    if action == keyboards.DONE:
        _mark_done(context, task)
        note = f"✅ Готово: {task.title}"
    elif action == keyboards.CANCEL:
        remove_reminder(context.application.job_queue, task.id)
        db.set_status(task.id, TaskStatus.CANCELLED)
        note = f"🚫 Убрал: {task.title}"
    elif action == keyboards.SNOOZE:
        new_time = utcnow() + timedelta(hours=1)
        db.set_reminder(task.id, new_time)
        task.remind_at = new_time
        schedule_reminder(context.application.job_queue, task)
        note = f"😴 Напомню через час: {task.title}"
    elif action == keyboards.RUN:
        db.set_status(task.id, TaskStatus.IN_PROGRESS)
        context.application.create_task(_run_ai_task(context.application, task))
        note = f"🤖 Делаю снова: {task.title}"
    else:
        return

    try:
        await query.edit_message_reply_markup(None)
    except Exception:  # noqa: BLE001 - message may be unchanged/too old
        pass
    await context.bot.send_message(query.message.chat.id, note)


# --------------------------------------------------------------------------
# shared task actions
# --------------------------------------------------------------------------

def _mark_done(context: ContextTypes.DEFAULT_TYPE, task: Task) -> None:
    remove_reminder(context.application.job_queue, task.id)
    _db(context).set_status(task.id, TaskStatus.DONE)


def _task_from_args(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Task | None:
    if not context.args or not context.args[0].lstrip("#").isdigit():
        return None
    task_id = int(context.args[0].lstrip("#"))
    return _db(context).get_task(task_id, update.effective_chat.id)


def _task_line(task: Task, config: Config) -> str:
    when = f"  ⏰ {_fmt_local(task.remind_at, config)}" if task.remind_at else ""
    detail = f"\n    {task.description}" if task.description else ""
    return f"{task.status_emoji()} #{task.id} {task.priority.emoji} {task.title}{when}{detail}"


async def _deny(update: Update) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(
            "Этот бот приватный. Если он твой — добавь свой chat id в "
            "TASKBOT_ALLOWED_CHAT_IDS (узнать: /whoami)."
        )


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled error", exc_info=context.error)
