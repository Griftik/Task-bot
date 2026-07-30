"""Runtime configuration, loaded from environment variables (and an optional
local .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:  # Loading a .env file is a convenience, not a hard dependency.
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - dotenv is listed in requirements.txt
    pass


DEFAULT_MODEL = "claude-opus-5"


class ConfigError(RuntimeError):
    """Raised when a required setting is missing or invalid."""


def _parse_allowed_ids(raw: str) -> frozenset[int]:
    ids: set[int] = set()
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            ids.add(int(chunk))
        except ValueError as exc:
            raise ConfigError(
                f"TASKBOT_ALLOWED_CHAT_IDS contains a non-numeric id: {chunk!r}"
            ) from exc
    return frozenset(ids)


def _parse_daily_brief(raw: str) -> tuple[int, int] | None:
    raw = raw.strip()
    if not raw:
        return None
    try:
        hh, mm = raw.split(":", 1)
        hour, minute = int(hh), int(mm)
    except ValueError as exc:
        raise ConfigError(
            f"TASKBOT_DAILY_BRIEF must look like HH:MM, got {raw!r}"
        ) from exc
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ConfigError(f"TASKBOT_DAILY_BRIEF is out of range: {raw!r}")
    return hour, minute


@dataclass(frozen=True)
class Config:
    telegram_token: str
    anthropic_model: str = DEFAULT_MODEL
    db_path: str = "taskbot.db"
    timezone_name: str = "UTC"
    allowed_chat_ids: frozenset[int] = field(default_factory=frozenset)
    daily_brief: tuple[int, int] | None = None

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    def is_allowed(self, chat_id: int) -> bool:
        """True if the chat may use the bot. An empty allow-list means open."""
        return not self.allowed_chat_ids or chat_id in self.allowed_chat_ids

    @classmethod
    def from_env(cls) -> "Config":
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            raise ConfigError(
                "TELEGRAM_BOT_TOKEN is not set. Create a bot with @BotFather and "
                "put its token in your environment or .env file."
            )

        # The Anthropic SDK resolves ANTHROPIC_API_KEY on its own, but we fail
        # early here with a friendly message instead of at the first API call.
        if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
            raise ConfigError(
                "ANTHROPIC_API_KEY is not set. Get one at "
                "https://console.anthropic.com/settings/keys"
            )

        timezone_name = os.environ.get("TASKBOT_TIMEZONE", "UTC").strip() or "UTC"
        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ConfigError(
                f"TASKBOT_TIMEZONE is not a valid IANA timezone: {timezone_name!r}"
            ) from exc

        return cls(
            telegram_token=token,
            anthropic_model=os.environ.get("ANTHROPIC_MODEL", "").strip()
            or DEFAULT_MODEL,
            db_path=os.environ.get("TASKBOT_DB_PATH", "").strip() or "taskbot.db",
            timezone_name=timezone_name,
            allowed_chat_ids=_parse_allowed_ids(
                os.environ.get("TASKBOT_ALLOWED_CHAT_IDS", "")
            ),
            daily_brief=_parse_daily_brief(os.environ.get("TASKBOT_DAILY_BRIEF", "")),
        )
