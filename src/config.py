"""Validated runtime settings and paths; process environment overrides .env."""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# Keep data beside the launch directory while debugging. An explicit value may
# be relative to that directory, absolute, or start with ~ for the user's home.
DATA_DIR = Path(os.getenv("AGENT_1_DATA_DIR") or ".agent-1").expanduser().resolve()


@dataclass(frozen=True)
class Settings:
    api_key: str = field(default="", repr=False)
    model: str = "deepseek/deepseek-v4.1-flash-1017"
    base_url: str = "https://openrouter.ai/api/v1"
    max_output_tokens: int = 16384
    max_rounds: int = 10
    stream: bool = True
    reasoning_enabled: bool = True
    max_task_tokens: int | None = None


def _positive_integer(environment, name, default):
    raw = environment.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be a positive integer.") from None
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer.")
    return value


def _boolean(environment, name, default):
    raw = environment.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"true", "1", "yes", "on"}:
        return True
    if raw in {"false", "0", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false.")


def load_settings(environment: Mapping[str, str] | None = None) -> Settings:
    """Read once at CLI startup, or inject an isolated mapping in offline tests."""
    environment = os.environ if environment is None else environment
    defaults = Settings()
    model = environment.get("OPENROUTER_MODEL", defaults.model).strip()
    if not model:
        raise ValueError("OPENROUTER_MODEL must not be empty.")
    base_url = environment.get("OPENROUTER_BASE_URL", defaults.base_url).strip()
    try:
        parsed = urlsplit(base_url)
        valid_url = parsed.scheme in {"http", "https"} and bool(parsed.hostname)
        parsed.port  # Validate an explicitly supplied port as well.
    except ValueError:
        valid_url = False
    if not valid_url:
        raise ValueError("OPENROUTER_BASE_URL must be an absolute HTTP or HTTPS URL.")
    return Settings(
        api_key=environment.get("OPENROUTER_API_KEY", "").strip(),
        model=model,
        base_url=base_url,
        max_output_tokens=_positive_integer(environment, "AGENT_1_MAX_OUTPUT_TOKENS", defaults.max_output_tokens),
        max_rounds=_positive_integer(environment, "AGENT_1_MAX_ROUNDS", defaults.max_rounds),
        stream=_boolean(environment, "AGENT_1_STREAM", defaults.stream),
        reasoning_enabled=_boolean(environment, "AGENT_1_REASONING_ENABLED", defaults.reasoning_enabled),
        max_task_tokens=_positive_integer(environment, "AGENT_1_MAX_TASK_TOKENS", None),
    )
