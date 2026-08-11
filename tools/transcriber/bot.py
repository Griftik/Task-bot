#!/usr/bin/env python3
"""Расшифровщик встреч и диктофона — Telegram-бот для VPS.

Кидаешь боту голосовое / аудиофайл / видео (Zoom-запись) — получаешь:
  1) расшифровку с таймкодами (.md файлом в ответ),
  2) копию в git-репозитории (transcripts/), откуда ИИ-помощник забирает
     контекст: саммари, задачи, привязку к проектам.

Стек: faster-whisper large-v3 (int8, CPU) + ffmpeg. Качество класса Plaud
достигается связкой: whisper-large на сервере + LLM-пост-обработка в
ИИ-помощнике.

ENV:
  BOT_TOKEN        — токен бота от @BotFather (НОВЫЙ, не скомпрометированный)
  ALLOWED_USER_ID  — твой Telegram ID (бот отвечает только тебе)
  WHISPER_MODEL    — large-v3 (по умолчанию) | medium | small
  TRANSCRIPTS_DIR  — куда класть .md (по умолчанию ./transcripts)
  GIT_PUSH         — 1 = после каждой расшифровки git commit+push (default 0)
"""
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

import telebot  # pyTelegramBotAPI
from faster_whisper import WhisperModel

BOT_TOKEN = os.environ["BOT_TOKEN"]
ALLOWED = int(os.getenv("ALLOWED_USER_ID", "0"))
MODEL_NAME = os.getenv("WHISPER_MODEL", "large-v3")
OUT_DIR = Path(os.getenv("TRANSCRIPTS_DIR", "transcripts"))
GIT_PUSH = os.getenv("GIT_PUSH", "0") == "1"

OUT_DIR.mkdir(parents=True, exist_ok=True)

print(f"Загружаю модель {MODEL_NAME} (первый запуск скачает её с HuggingFace)…")
model = WhisperModel(MODEL_NAME, device="cpu", compute_type="int8")
print("Модель готова.")

bot = telebot.TeleBot(BOT_TOKEN)


def hms(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def to_wav(src: str) -> str:
    """Любой вход (ogg/mp3/m4a/mp4/webm…) → wav 16k mono для whisper."""
    dst = src + ".wav"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", src,
         "-ac", "1", "-ar", "16000", dst],
        check=True,
    )
    return dst


def transcribe(path: str, title: str) -> Path:
    wav = to_wav(path)
    t0 = time.time()
    segments, info = model.transcribe(
        wav, language="ru", vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 700},
    )
    lines = []
    for seg in segments:
        text = seg.text.strip()
        if text:
            lines.append(f"`{hms(seg.start)}` {text}")
    took = time.time() - t0

    safe = re.sub(r"[^\w\-а-яА-ЯёЁ ]", "", title).strip().replace(" ", "-")[:60]
    stamp = time.strftime("%Y-%m-%d-%H%M")
    out = OUT_DIR / f"{stamp}-{safe or 'запись'}.md"
    header = (
        f"# Расшифровка: {title}\n\n"
        f"Дата: {time.strftime('%Y-%m-%d %H:%M')} · длительность {hms(info.duration)}"
        f" · модель {MODEL_NAME} · обработано за {hms(took)}\n\n"
        "> Сырая расшифровка. Саммари, задачи и привязку к проектам делает"
        " ИИ-помощник при заборе.\n\n---\n\n"
    )
    out.write_text(header + "\n\n".join(lines), encoding="utf-8")

    if GIT_PUSH:
        subprocess.run(["git", "add", str(out)], check=False)
        subprocess.run(["git", "commit", "-q", "-m", f"transcript: {out.name}"], check=False)
        subprocess.run(["git", "push", "-q"], check=False)
    return out


def handle_file(message, file_id: str, title: str):
    if ALLOWED and message.from_user.id != ALLOWED:
        return
    bot.reply_to(message, "Принял, расшифровываю…")
    info = bot.get_file(file_id)
    data = bot.download_file(info.file_path)
    suffix = Path(info.file_path).suffix or ".bin"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(data)
        tmp = f.name
    try:
        out = transcribe(tmp, title)
        with open(out, "rb") as doc:
            bot.send_document(message.chat.id, doc,
                              caption=f"Готово: {out.name}")
    except Exception as e:  # noqa: BLE001 — пользователю нужен ответ всегда
        bot.reply_to(message, f"Ошибка: {e}")
    finally:
        os.unlink(tmp)


@bot.message_handler(content_types=["voice"])
def on_voice(m):
    handle_file(m, m.voice.file_id, "голосовое")


@bot.message_handler(content_types=["audio"])
def on_audio(m):
    handle_file(m, m.audio.file_id, m.audio.file_name or "аудио")


@bot.message_handler(content_types=["video", "video_note"])
def on_video(m):
    fid = m.video.file_id if m.content_type == "video" else m.video_note.file_id
    handle_file(m, fid, "видео-запись")


@bot.message_handler(content_types=["document"])
def on_document(m):
    handle_file(m, m.document.file_id, m.document.file_name or "файл")


@bot.message_handler(commands=["start"])
def on_start(m):
    if ALLOWED and m.from_user.id != ALLOWED:
        return
    bot.reply_to(m, "Кидай голосовое, аудио или запись встречи — верну "
                    "расшифровку с таймкодами. Файлы до 20 МБ — напрямую; "
                    "больше — через ссылку на файл (в разработке).")


if __name__ == "__main__":
    print("Бот запущен.")
    bot.infinity_polling(skip_pending=True)
