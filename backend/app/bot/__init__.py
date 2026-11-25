"""Telegram bot package for Break-Glass Voice Gate."""

from __future__ import annotations

from typing import Any

__all__ = [
    "ApproverConsole",
    "AuditLoggingError",
    "CallbackSigner",
    "LiveCallAlertManager",
    "TelegramEnrollmentFlow",
    "analyze_audio",
    "build_audit_report",
    "format_approver_card",
    "format_requester_status",
    "format_telegram_reply",
    "notify_live_state",
]


def __getattr__(name: str) -> Any:
    if name in ("analyze_audio", "format_telegram_reply"):
        from . import analyze

        return getattr(analyze, name)
    if name in ("ApproverConsole", "CallbackSigner", "format_approver_card", "format_requester_status"):
        from . import approver_console

        return getattr(approver_console, name)
    if name in ("AuditLoggingError", "build_audit_report"):
        from . import audit_bridge

        return getattr(audit_bridge, name)
    if name == "TelegramEnrollmentFlow":
        from . import enrollment_flow

        return getattr(enrollment_flow, name)
    if name in ("LiveCallAlertManager", "notify_live_state"):
        from . import live_call_alerts

        return getattr(live_call_alerts, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
