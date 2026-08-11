#!/usr/bin/env python3
"""Папка-адаптер: следит за IN_DIR, расшифровывает всё новое в OUT_DIR.

Для длинных Zoom-записей (>20 МБ, мимо лимита Bot API): закинул файл в папку
на сервере (scp/синк) — получил .md рядом.

ENV: IN_DIR=incoming, TRANSCRIPTS_DIR=transcripts, WHISPER_MODEL=large-v3,
     POLL_SECONDS=30, GIT_PUSH=0|1
Запуск: python -m adapters.watch_folder
"""
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from transcriber import TranscriberAgent

IN_DIR = Path(os.getenv("IN_DIR", "incoming"))
OUT_DIR = Path(os.getenv("TRANSCRIPTS_DIR", "transcripts"))
POLL = int(os.getenv("POLL_SECONDS", "30"))
GIT_PUSH = os.getenv("GIT_PUSH", "0") == "1"
AUDIO_EXT = {".ogg", ".oga", ".mp3", ".m4a", ".wav", ".flac", ".mp4", ".mkv", ".webm", ".mov"}

IN_DIR.mkdir(parents=True, exist_ok=True)
OUT_DIR.mkdir(parents=True, exist_ok=True)
agent = TranscriberAgent(model=os.getenv("WHISPER_MODEL", "large-v3"))

print(f"Смотрю {IN_DIR}/ каждые {POLL} c.")
while True:
    for f in sorted(IN_DIR.iterdir()):
        if f.suffix.lower() not in AUDIO_EXT or f.stat().st_size == 0:
            continue
        marker = f.with_suffix(f.suffix + ".done")
        if marker.exists():
            continue
        # ждём, пока файл дозальётся (размер стабилен 2 цикла)
        size = f.stat().st_size
        time.sleep(5)
        if f.stat().st_size != size:
            continue
        print("Расшифровываю", f.name)
        try:
            tr = agent.transcribe(f, title=f.stem)
            safe = re.sub(r"[^\w\-а-яА-ЯёЁ ]", "", tr.title).strip().replace(" ", "-")[:60]
            out = OUT_DIR / f"{time.strftime('%Y-%m-%d-%H%M')}-{safe or 'запись'}.md"
            out.write_text(tr.to_markdown(
                note="Сырая расшифровка. Пост-обработка — у ИИ-помощника."),
                encoding="utf-8")
            marker.touch()
            print("Готово:", out.name)
            if GIT_PUSH:
                subprocess.run(["git", "add", str(out)], check=False)
                subprocess.run(["git", "commit", "-q", "-m", f"transcript: {out.name}"], check=False)
                subprocess.run(["git", "push", "-q"], check=False)
        except Exception as e:  # noqa: BLE001 — конвейер не должен падать
            print("Ошибка на", f.name, "—", e)
            marker.write_text(f"error: {e}")
    time.sleep(POLL)
