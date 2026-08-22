"""Text-generation backends.

The deterministic backend is the default so the complete assessment runs
without network access or API keys.  The OpenAI-compatible backend is optional
and never writes credentials to logs.
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from config import RuntimeConfig


class TextBackend(Protocol):
    name: str

    async def generate(self, *, system_prompt: str, user_prompt: str, fallback: str) -> str:
        ...


@dataclass(slots=True)
class DeterministicBackend:
    name: str = "deterministic-offline"
    model: str = "mock-research-writer-v1"
    last_retries: int = 0

    async def generate(self, *, system_prompt: str, user_prompt: str, fallback: str) -> str:
        del system_prompt, user_prompt
        self.last_retries = 0
        # A small asynchronous delay makes parallel overlap visible in run_log.json.
        await asyncio.sleep(0.03)
        return fallback


@dataclass(slots=True)
class OpenAICompatibleBackend:
    api_key: str
    base_url: str
    model: str
    timeout_seconds: int = 90
    max_retries: int = 2
    name: str = "openai-compatible"
    last_retries: int = 0

    async def generate(self, *, system_prompt: str, user_prompt: str, fallback: str) -> str:
        del fallback
        self.last_retries = 0
        for attempt in range(self.max_retries + 1):
            try:
                return await asyncio.to_thread(self._request, system_prompt, user_prompt)
            except RuntimeError:
                # ``attempt`` is zero-based, while the log records how many
                # retry transitions were actually made.  A terminal failure
                # must not count a retry beyond ``max_retries``.
                self.last_retries = min(attempt + 1, self.max_retries)
                if attempt >= self.max_retries:
                    raise
                await asyncio.sleep(0.25 * (2**attempt))
        raise RuntimeError("unreachable retry state")

    def _request(self, system_prompt: str, user_prompt: str) -> str:
        endpoint = self.base_url.rstrip("/") + "/chat/completions"
        body = json.dumps(
            {
                "model": self.model,
                "temperature": 0.2,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            endpoint,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"OpenAI-compatible request failed: {exc}") from exc
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("Unexpected response structure") from exc


def create_backend(mode: str) -> TextBackend:
    if mode == "mock":
        return DeterministicBackend()
    config = RuntimeConfig.from_env()
    api_key = config.api_key
    if mode == "auto" and not api_key:
        return DeterministicBackend()
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY, DEEPSEEK_API_KEY, or MAS_LLM_API_KEY is required for --real"
        )
    return OpenAICompatibleBackend(
        api_key=api_key,
        base_url=config.base_url,
        model=config.model,
    )
