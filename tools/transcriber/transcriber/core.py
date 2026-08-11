"""TranscriberAgent — переиспользуемый агент расшифровки.

Чистый модуль: только faster-whisper + ffmpeg, никаких Telegram/веб-зависимостей.
Используется адаптерами (telegram_bot, watch_folder, cli) и как модуль
в «Помощнике управленца»:

    from transcriber import TranscriberAgent
    agent = TranscriberAgent()                # large-v3, cpu/int8
    t = agent.transcribe("встреча.m4a")
    print(t.to_markdown())                    # или t.segments / t.to_json()
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path


def _hms(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass
class Transcript:
    title: str
    duration: float
    model: str
    took: float
    language: str
    segments: list[Segment] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(s.text for s in self.segments)

    def to_markdown(self, note: str | None = None) -> str:
        head = (
            f"# Расшифровка: {self.title}\n\n"
            f"Дата: {time.strftime('%Y-%m-%d %H:%M')} · длительность {_hms(self.duration)}"
            f" · модель {self.model} · обработано за {_hms(self.took)}\n\n"
        )
        if note:
            head += f"> {note}\n\n"
        body = "\n\n".join(f"`{_hms(s.start)}` {s.text}" for s in self.segments)
        return head + "---\n\n" + body

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=1)


class TranscriberAgent:
    """Ленивая загрузка модели; один экземпляр переживает много вызовов."""

    def __init__(self, model: str = "large-v3", device: str = "cpu",
                 compute_type: str = "int8"):
        self.model_name = model
        self._device = device
        self._compute_type = compute_type
        self._model = None

    def _ensure_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            self._model = WhisperModel(self.model_name, device=self._device,
                                       compute_type=self._compute_type)
        return self._model

    @staticmethod
    def _to_wav(src: str | Path) -> str:
        """Любой контейнер (ogg/m4a/mp3/mp4/webm…) → wav 16k mono."""
        dst = tempfile.mktemp(suffix=".wav")
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
             "-ac", "1", "-ar", "16000", dst],
            check=True,
        )
        return dst

    def transcribe(self, path: str | Path, title: str | None = None,
                   language: str = "ru") -> Transcript:
        model = self._ensure_model()
        wav = self._to_wav(path)
        t0 = time.time()
        try:
            raw_segments, info = model.transcribe(
                wav, language=language, vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 700},
            )
            segments = [Segment(s.start, s.end, s.text.strip())
                        for s in raw_segments if s.text.strip()]
        finally:
            Path(wav).unlink(missing_ok=True)
        return Transcript(
            title=title or Path(path).stem,
            duration=info.duration,
            model=self.model_name,
            took=time.time() - t0,
            language=language,
            segments=segments,
        )
