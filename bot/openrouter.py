"""OpenRouter backend: the same assistant, reached through OpenRouter's
OpenAI-compatible chat-completions API instead of the Anthropic API.

Only the three model calls differ; task parsing, fallbacks and rendering are
inherited from ClaudeAssistant.
"""

from __future__ import annotations

import logging
import os

import httpx

from .claude import ClaudeAssistant
from .prompts import EXECUTE_SYSTEM, EXTRACT_SCHEMA, EXTRACT_SYSTEM

logger = logging.getLogger(__name__)

API_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterAssistant(ClaudeAssistant):
    def __init__(self, model: str) -> None:  # noqa: D107 - no Anthropic client here
        key = os.environ.get("OPENROUTER_API_KEY", "").strip()
        self._model = model
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(180.0, connect=15.0),
            headers={
                "Authorization": f"Bearer {key}",
                # Optional attribution headers OpenRouter shows in its dashboard.
                "X-Title": "Task-bot",
            },
        )

    async def _chat(self, system: str, user: str, *, max_tokens: int, **extra) -> str:
        payload = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            **extra,
        }
        response = await self._http.post(API_URL, json=payload)
        if response.status_code >= 400:
            # Body carries OpenRouter's error message; never log the request headers.
            raise RuntimeError(
                f"OpenRouter {response.status_code}: {response.text[:500]}"
            )
        data = response.json()
        if "error" in data:
            raise RuntimeError(f"OpenRouter error: {data['error']}")
        content = data["choices"][0]["message"].get("content") or ""
        return content.strip()

    async def _extract_json(self, prompt: str) -> str:
        return await self._chat(
            EXTRACT_SYSTEM,
            prompt,
            max_tokens=2048,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "tasks", "strict": True, "schema": EXTRACT_SCHEMA},
            },
        )

    async def _run_execution(self, prompt: str, *, use_web: bool) -> str:
        extra = {"plugins": [{"id": "web", "max_results": 5}]} if use_web else {}
        text = await self._chat(EXECUTE_SYSTEM, prompt, max_tokens=4096, **extra)
        return text or "(no output)"

    async def _advise(self, system: str, rendered_tasks: str) -> str:
        return await self._chat(system, rendered_tasks, max_tokens=1024)
