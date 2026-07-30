"""Entry point: wire config, storage, Claude and Telegram together."""

from __future__ import annotations

import logging
import sys

from telegram import BotCommand
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from . import handlers
from .claude import ClaudeAssistant
from .config import Config, ConfigError
from .db import Database
from .scheduler import schedule_startup

logger = logging.getLogger(__name__)

_COMMANDS = [
    BotCommand("tasks", "Открытые задачи"),
    BotCommand("today", "Что на сегодня"),
    BotCommand("focus", "С чего начать прямо сейчас"),
    BotCommand("help", "Помощь"),
]


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    # The HTTP layer is chatty at INFO; keep it quiet.
    logging.getLogger("httpx").setLevel(logging.WARNING)


async def _post_init(application: Application) -> None:
    await application.bot.set_my_commands(_COMMANDS)
    schedule_startup(application)
    config: Config = application.bot_data["config"]
    if not config.allowed_chat_ids:
        logger.warning(
            "TASKBOT_ALLOWED_CHAT_IDS is empty — the bot will reply to anyone. "
            "Set it to your chat id (send /whoami) to keep the bot private."
        )
    logger.info("Task-bot is up. Model: %s", config.anthropic_model)


def build_application(config: Config) -> Application:
    application = (
        Application.builder().token(config.telegram_token).post_init(_post_init).build()
    )
    application.bot_data.update(
        {
            "config": config,
            "db": Database(config.db_path),
            "assistant": ClaudeAssistant(config.anthropic_model),
        }
    )

    application.add_handler(CommandHandler("start", handlers.start))
    application.add_handler(CommandHandler("help", handlers.help_command))
    application.add_handler(CommandHandler("whoami", handlers.whoami))
    application.add_handler(CommandHandler("tasks", handlers.list_tasks))
    application.add_handler(CommandHandler("today", handlers.today))
    application.add_handler(CommandHandler("focus", handlers.focus))
    application.add_handler(CommandHandler("result", handlers.show_result))
    application.add_handler(CommandHandler("done", handlers.done_command))
    application.add_handler(CommandHandler("cancel", handlers.cancel_command))
    application.add_handler(CallbackQueryHandler(handlers.on_button))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.on_message)
    )
    application.add_error_handler(handlers.on_error)
    return application


def main() -> None:
    _setup_logging()
    try:
        config = Config.from_env()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    application = build_application(config)
    application.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
