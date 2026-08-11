# Transcriber — агент расшифровки (модуль + адаптеры)

Переиспользуемый агент расшифровки встреч и диктофона (класс Plaud:
whisper **large-v3** + LLM-пост-обработка при заборе).

```
transcriber/           ← ЯДРО-АГЕНТ: чистый модуль, только faster-whisper + ffmpeg
  core.py                TranscriberAgent.transcribe(file) → Transcript
adapters/              ← тонкие обёртки вокруг ядра
  telegram_bot.py        Telegram: кинул файл → получил .md (до 20 МБ)
  watch_folder.py        папка на сервере: для длинных Zoom-записей
  cli.py                 python -m adapters.cli запись.m4a
```

## Как модуль (для «Помощника управленца» и любых продуктов)

```python
from transcriber import TranscriberAgent

agent = TranscriberAgent()          # large-v3, cpu/int8; модель грузится лениво
t = agent.transcribe("встреча.m4a") # → Transcript
t.segments                          # [(start, end, text), …]
t.to_markdown()                     # расшифровка с таймкодами
t.to_json()                         # для пайплайнов
```

Ядро не тянет Telegram-зависимостей — в чужой продукт уезжает только
`transcriber/` + `pip install faster-whisper` (+ ffmpeg в системе).

## Деплой адаптеров на VPS (где Miru — ffmpeg уже есть)

```bash
cd Task-bot/tools/transcriber
pip install faster-whisper pyTelegramBotAPI

# Telegram-бот (новый токен у @BotFather! старый скомпрометирован)
BOT_TOKEN=xxx ALLOWED_USER_ID=291136301 GIT_PUSH=1 \
  nohup python3 -m adapters.telegram_bot > tg.log 2>&1 &

# Папка для длинных записей (опционально)
nohup python3 -m adapters.watch_folder > watch.log 2>&1 &
```

Слабый CPU → `WHISPER_MODEL=medium` (быстрее ×2–3, качество чуть ниже).

## Забор контекста
Адаптеры кладут .md в `transcripts/` и (при `GIT_PUSH=1`) пушат в git.
ИИ-помощник при утреннем ритуале видит новые расшифровки → саммари, задачи,
привязка к проектам → разбор в vault.

## v2 (по команде)
Диаризация «кто говорит» (pyannote, нужен HF-токен на сервере) · приём ссылок
на файлы в Telegram · авто-язык.
