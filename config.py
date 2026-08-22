"""Environment-only configuration; credentials are never persisted to logs."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: Path | None = None) -> None:
    """Load a minimal .env file without adding a runtime dependency."""

    env_path = path or (Path(__file__).resolve().parent / ".env")
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    api_key: str
    base_url: str
    model: str

    @classmethod
    def from_env(cls) -> "RuntimeConfig":
        # Choose one provider namespace as a unit so a DeepSeek key cannot be
        # accidentally combined with an OpenAI URL from the example file.
        if key := os.getenv("MAS_LLM_API_KEY"):
            return cls(
                api_key=key,
                base_url=os.getenv("MAS_LLM_BASE_URL", "https://api.openai.com/v1"),
                model=os.getenv("MAS_LLM_MODEL", "gpt-4o-mini"),
            )
        if key := os.getenv("OPENAI_API_KEY"):
            return cls(
                api_key=key,
                base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            )
        if key := os.getenv("DEEPSEEK_API_KEY"):
            return cls(
                api_key=key,
                base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
                model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
            )
        return cls(api_key="", base_url="https://api.openai.com/v1", model="gpt-4o-mini")
