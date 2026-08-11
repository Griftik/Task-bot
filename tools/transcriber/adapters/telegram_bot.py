#!/usr/bin/env python3
"""Telegram-адаптер над TranscriberAgent.

Кидаешь боту голосовое/аудио/видео → .md-расшифровка в ответ + копия в
TRANSCRIPTS_DIR (+опционально git push — оттуда ИИ-помощник забирает контекст).

ENV: BOT_TOKEN, ALLOWED_USER_ID, WHISPER_MODEL=large-v3,
     TRANSCRIPTS_DIR=transcripts, GIT_PUSH=0|1
Запуск: python -m adapters.telegram_bot  (из tools/transcriber/)
"""
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import telebot  # pyTelegramBotAPI
from transcriber import TranscriberAgent

BOT_TOKEN = os.environ["BOT_TOKEN"]
ALLOWED = int(os.getenv("ALLOWED_USER_ID", "0"))
OUT_DIR = Path(os.getenv("TRANSCRIPTS_DIR", "transcripts"))
GIT_PUSH = os.getenv("GIT_PUSH", "0") == "1"
NOTE = ("Сырая расшифровка. Саммари, задачи и привязку к проектам делает "
        "ИИ-помощник при заборе.")

OUT_DIR.mkdir(parents=True, exist_ok=True)
agent = TranscriberAgent(model=os.getenv("WHISPER_MODEL", "large-v3"))
bot = telebot.TeleBot(BOT_TOKEN)


def save(tr) -> Path:
    safe = re.sub(r"[^\w\-а-яА-ЯёЁ ]", "", tr.title).strip().replace(" ", "-")[:60]
    out = OUT_DIR / f"{time.strftime('%Y-%m-%d-%H%M')}-{safe or 'запись'}.md"
    out.write_text(tr.to_markdown(note=NOTE), encoding="utf-8")
    if GIT_PUSH:
        subprocess.run(["git", "add", str(out)], check=False)
        subprocess.run(["git", "commit", "-q", "-m", f"transcript: {out.name}"], check=False)
        subprocess.run(["git", "push", "-q"], check=False)
    return out


def handle(message, file_id: str, title: str):
    if ALLOWED and message.from_user.id != ALLOWED:
        return
    bot.reply_to(message, "Принял, расшифровываю…")
    info = bot.get_file(file_id)
    data = bot.download_file(info.file_path)
    with tempfile.NamedTemporaryFile(suffix=Path(info.file_path).suffix or ".bin",
                                     delete=False) as f:
        f.write(data)
        tmp = f.name
    try:
        out = save(agent.transcribe(tmp, title=title))
        with open(out, "rb") as doc:
            bot.send_document(message.chat.id, doc, caption=f"Готово: {out.name}")
    except Exception as e:  # noqa: BLE001 — пользователю нужен ответ всегда
        bot.reply_to(message, f"Ошибка: {e}")
    finally:
        os.unlink(tmp)


@bot.message_handler(content_types=["voice"])
def on_voice(m):
    handle(m, m.voice.file_id, "голосовое")


@bot.message_handler(content_types=["audio"])
def on_audio(m):
    handle(m, m.audio.file_id, m.audio.file_name or "аудио")


@bot.message_handler(content_types=["video", "video_note"])
def on_video(m):
    fid = m.video.file_id if m.content_type == "video" else m.video_note.file_id
    handle(m, fid, "видео-запись")


@bot.message_handler(content_types=["document"])
def on_document(m):
    handle(m, m.document.file_id, m.document.file_name or "файл")


@bot.message_handler(commands=["start"])
def on_start(m):
    if ALLOWED and m.from_user.id != ALLOWED:
        return
    bot.reply_to(m, "Кидай голосовое, аудио или запись встречи — верну "
                    "расшифровку с таймкодами (файлы до 20 МБ).")


if __name__ == "__main__":
    print("Телеграм-адаптер запущен.")
    bot.infinity_polling(skip_pending=True)
