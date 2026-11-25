"""Telegram bot package for Break-Glass Voice Gate (Aletheia voice-note checker)."""

from __future__ import annotations

from typing import Any

__all__ = ["analyze_audio", "format_telegram_reply"]


def __getattr__(name: str) -> Any:
    if name in ("analyze_audio", "format_telegram_reply"):
        from . import analyze

        return getattr(analyze, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
