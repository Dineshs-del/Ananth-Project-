"""Environment configuration for the AI voice module."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[3]


def load_project_dotenv() -> None:
    """Load .env from repo root, backend root, and CWD with UTF-8 BOM tolerance."""
    for candidate in (REPO_ROOT / ".env", BACKEND_ROOT / ".env", Path.cwd() / ".env"):
        if candidate.is_file():
            load_dotenv(dotenv_path=candidate, encoding="utf-8-sig", override=False)


load_project_dotenv()

AIMode = Literal["fixture", "live"]
DEFAULT_BASE_URL = "https://platform.detectif.ai"


class ConfigError(ValueError):
    """Raised when required AI configuration is missing or invalid."""


@dataclass(frozen=True)
class AIConfig:
    ai_mode: AIMode
    detectif_api_key: str | None
    detectif_base_url: str
    fixture_scenario: str


def load_config(*, require_live: bool = False) -> AIConfig:
    """Load AI settings from environment variables.

    Never includes secret values in error messages.
    """
    raw_mode = os.getenv("AI_MODE", "fixture").strip().lower()
    if raw_mode not in ("fixture", "live"):
        raise ConfigError(f"AI_MODE must be 'fixture' or 'live', got '{raw_mode}'.")

    mode: AIMode = "live" if require_live else raw_mode  # type: ignore[assignment]
    api_key = os.getenv("DETECTIF_API_KEY", "").strip() or None
    base_url = os.getenv("DETECTIF_BASE_URL", DEFAULT_BASE_URL).strip().rstrip("/")
    scenario = os.getenv("FIXTURE_SCENARIO", "pass").strip().lower() or "pass"

    if mode == "live" and not api_key:
        raise ConfigError(
            "DETECTIF_API_KEY is required when AI_MODE=live. Set it in your local .env file."
        )

    if not base_url.startswith(("http://", "https://")):
        raise ConfigError("DETECTIF_BASE_URL must start with http:// or https://.")

    return AIConfig(
        ai_mode=mode,
        detectif_api_key=api_key,
        detectif_base_url=base_url,
        fixture_scenario=scenario,
    )
