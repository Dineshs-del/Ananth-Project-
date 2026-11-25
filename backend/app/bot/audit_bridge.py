"""Fail-closed audit bridge and plain-language /report formatter for the Telegram bot.

Uses ONLY the existing append-only SHA-256 hash-chained audit service
(backend/app/audit/service.py) — never creates a separate audit log or table.
Never stores raw audio, tokens, or unauthorized scores in audit details.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..audit.service import list_events, record_event, verify_chain
from ..core.database import get_db, init_db

# Canonical bot audit event types.
EVENT_ENROLLMENT_CONSENT = "ENROLLMENT_CONSENT"
EVENT_ENROLLMENT_SAMPLE_ACCEPTED = "ENROLLMENT_SAMPLE_ACCEPTED"
EVENT_ENROLLMENT_SAMPLE_REJECTED = "ENROLLMENT_SAMPLE_REJECTED"
EVENT_REQUEST_CREATED = "REQUEST_CREATED"
EVENT_VOICE_CHECK_RESULT = "VOICE_CHECK_RESULT"
EVENT_APPROVAL_SHOWN = "APPROVAL_SHOWN"
EVENT_APPROVAL_DECISION = "APPROVAL_DECISION"
EVENT_BLOCK = "FAIL_BLOCK_CONFIRMED"
EVENT_EXPIRY = "APPROVAL_EXPIRED"
EVENT_LIVE_CALL_STATE = "LIVE_CALL_STATE"

FORBIDDEN_DETAIL_KEYS = frozenset(
    {
        "audio",
        "audio_bytes",
        "raw_audio",
        "wav",
        "file_bytes",
        "token",
        "api_key",
        "bot_token",
        "secret",
        "authorization",
        "score",
        "similarity",
        "aletheia",
        "eidos",
    }
)


class AuditLoggingError(RuntimeError):
    """Raised when audit logging fails; callers MUST fail closed and abort the action."""


def _sanitize_details(details: dict[str, Any]) -> dict[str, Any]:
    """Strip forbidden keys/binary values so raw audio, tokens, or scores are never logged."""
    clean: dict[str, Any] = {}
    for key, val in details.items():
        if key.lower() in FORBIDDEN_DETAIL_KEYS:
            continue
        if isinstance(val, (bytes, bytearray, memoryview)):
            continue
        clean[key] = val
    return clean


def _safe_record_on_conn(
    conn: sqlite3.Connection,
    *,
    event_type: str,
    actor_id: str,
    request_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record a single sanitized event on an open DB connection or raise AuditLoggingError."""
    try:
        return record_event(
            conn,
            event_type=event_type,
            actor_id=actor_id,
            request_id=request_id,
            details=_sanitize_details(details or {}),
        )
    except Exception as exc:
        raise AuditLoggingError(f"Audit log write failed for {event_type}.") from exc


def _record_standalone(
    *,
    db_path: Path | None,
    event_type: str,
    actor_id: str,
    request_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        init_db(db_path, seed_sample_employees=False)
        with get_db(db_path) as conn:
            return _safe_record_on_conn(
                conn,
                event_type=event_type,
                actor_id=actor_id,
                request_id=request_id,
                details=details,
            )
    except AuditLoggingError:
        raise
    except Exception as exc:
        raise AuditLoggingError(f"Audit database unavailable for {event_type}.") from exc


# --- Required bot audit event writers (all fail closed) ---


def log_enrollment_consent(
    *,
    actor_id: str,
    employee_id: str,
    channel: str = "telegram",
    conn: sqlite3.Connection | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    details = {"channel": channel, "employee_id": employee_id, "consent_confirmed": True}
    if conn is not None:
        return _safe_record_on_conn(
            conn,
            event_type=EVENT_ENROLLMENT_CONSENT,
            actor_id=actor_id,
            details=details,
        )
    return _record_standalone(
        db_path=db_path,
        event_type=EVENT_ENROLLMENT_CONSENT,
        actor_id=actor_id,
        details=details,
    )


def log_enrollment_sample(
    *,
    actor_id: str,
    employee_id: str,
    accepted: bool,
    reason: str = "",
    channel: str = "telegram",
    conn: sqlite3.Connection | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    event_type = (
        EVENT_ENROLLMENT_SAMPLE_ACCEPTED if accepted else EVENT_ENROLLMENT_SAMPLE_REJECTED
    )
    details = {
        "channel": channel,
        "employee_id": employee_id,
        "accepted": accepted,
        "reason": reason[:200],
    }
    if conn is not None:
        return _safe_record_on_conn(
            conn,
            event_type=event_type,
            actor_id=actor_id,
            details=details,
        )
    return _record_standalone(
        db_path=db_path,
        event_type=event_type,
        actor_id=actor_id,
        details=details,
    )


def log_request_created(
    *,
    actor_id: str,
    request_id: str,
    action_summary: str,
    channel: str = "telegram",
    conn: sqlite3.Connection | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    details = {"channel": channel, "action_summary": action_summary[:200]}
    if conn is not None:
        return _safe_record_on_conn(
            conn,
            event_type=EVENT_REQUEST_CREATED,
            actor_id=actor_id,
            request_id=request_id,
            details=details,
        )
    return _record_standalone(
        db_path=db_path,
        event_type=EVENT_REQUEST_CREATED,
        actor_id=actor_id,
        request_id=request_id,
        details=details,
    )


def log_voice_check_result(
    *,
    actor_id: str,
    risk: str,
    reason: str,
    chat_type: str,
    ai_mode: str,
    request_id: str | None = None,
    channel: str = "telegram",
    conn: sqlite3.Connection | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Log voice check result with risk and reason ONLY (no raw audio, tokens, or scores)."""
    details = {
        "channel": channel,
        "chat_type": chat_type,
        "ai_mode": ai_mode,
        "risk": str(risk),
        "reason": str(reason)[:300],
    }
    if conn is not None:
        return _safe_record_on_conn(
            conn,
            event_type=EVENT_VOICE_CHECK_RESULT,
            actor_id=actor_id,
            request_id=request_id,
            details=details,
        )
    return _record_standalone(
        db_path=db_path,
        event_type=EVENT_VOICE_CHECK_RESULT,
        actor_id=actor_id,
        request_id=request_id,
        details=details,
    )


def log_approval_shown(
    *,
    actor_id: str,
    request_id: str,
    approval_ref: str,
    risk: str,
    channel: str = "telegram",
    conn: sqlite3.Connection | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    details = {"channel": channel, "approval_ref": approval_ref, "risk": str(risk)}
    if conn is not None:
        return _safe_record_on_conn(
            conn,
            event_type=EVENT_APPROVAL_SHOWN,
            actor_id=actor_id,
            request_id=request_id,
            details=details,
        )
    return _record_standalone(
        db_path=db_path,
        event_type=EVENT_APPROVAL_SHOWN,
        actor_id=actor_id,
        request_id=request_id,
        details=details,
    )


def log_approval_decision(
    *,
    actor_id: str,
    request_id: str,
    approval_id: str,
    decision: str,
    channel: str = "telegram",
    conn: sqlite3.Connection | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    details = {"channel": channel, "approval_id": approval_id, "decision": decision}
    if conn is not None:
        return _safe_record_on_conn(
            conn,
            event_type=EVENT_APPROVAL_DECISION,
            actor_id=actor_id,
            request_id=request_id,
            details=details,
        )
    return _record_standalone(
        db_path=db_path,
        event_type=EVENT_APPROVAL_DECISION,
        actor_id=actor_id,
        request_id=request_id,
        details=details,
    )


def log_block(
    *,
    actor_id: str,
    request_id: str,
    channel: str = "telegram",
    conn: sqlite3.Connection | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    details = {"channel": channel, "outcome": "blocked"}
    if conn is not None:
        return _safe_record_on_conn(
            conn,
            event_type=EVENT_BLOCK,
            actor_id=actor_id,
            request_id=request_id,
            details=details,
        )
    return _record_standalone(
        db_path=db_path,
        event_type=EVENT_BLOCK,
        actor_id=actor_id,
        request_id=request_id,
        details=details,
    )


def log_expiry(
    *,
    actor_id: str,
    request_id: str,
    approval_id: str,
    channel: str = "telegram",
    conn: sqlite3.Connection | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    details = {"channel": channel, "approval_id": approval_id, "outcome": "expired"}
    if conn is not None:
        return _safe_record_on_conn(
            conn,
            event_type=EVENT_EXPIRY,
            actor_id=actor_id,
            request_id=request_id,
            details=details,
        )
    return _record_standalone(
        db_path=db_path,
        event_type=EVENT_EXPIRY,
        actor_id=actor_id,
        request_id=request_id,
        details=details,
    )


def log_live_call_state(
    *,
    actor_id: str,
    request_id: str,
    state: str,
    reason: str,
    fail_latched: bool,
    channel: str = "telegram_live_call",
    conn: sqlite3.Connection | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Record a live-call risk state update (state + reason ONLY; never raw audio or scores)."""
    details = {
        "channel": channel,
        "state": str(state),
        "reason": str(reason)[:300],
        "fail_latched": bool(fail_latched),
    }
    if conn is not None:
        return _safe_record_on_conn(
            conn,
            event_type=EVENT_LIVE_CALL_STATE,
            actor_id=actor_id,
            request_id=request_id,
            details=details,
        )
    return _record_standalone(
        db_path=db_path,
        event_type=EVENT_LIVE_CALL_STATE,
        actor_id=actor_id,
        request_id=request_id,
        details=details,
    )


# --- Plain-language /report builder using existing verify_chain & list_events ---


def _format_event_time(iso_ts: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except ValueError:
        return iso_ts


def describe_audit_event(event: dict[str, Any]) -> str:
    """Render one audit event in plain language without leaking raw scores or secrets."""
    etype = str(event.get("event_type") or "UNKNOWN")
    actor = str(event.get("actor_id") or "unknown")
    req_id = event.get("request_id")
    details = event.get("details") if isinstance(event.get("details"), dict) else {}
    ts = _format_event_time(str(event.get("created_at") or ""))
    req_suffix = f" ({req_id})" if req_id else ""

    if etype == EVENT_ENROLLMENT_CONSENT:
        summary = f"Enrollment consent recorded by {actor}"
    elif etype == EVENT_ENROLLMENT_SAMPLE_ACCEPTED:
        summary = f"Enrollment voice sample accepted for {details.get('employee_id', actor)}"
    elif etype == EVENT_ENROLLMENT_SAMPLE_REJECTED:
        summary = f"Enrollment voice sample rejected for {details.get('employee_id', actor)}"
    elif etype == "EMPLOYEE_ENROLLED":
        summary = f"Employee voice sample enrolled for {actor}"
    elif etype == "EMPLOYEE_CREATED":
        summary = f"Employee profile created for {actor}"
    elif etype == EVENT_REQUEST_CREATED:
        summary = f"Break-glass request created by {actor}{req_suffix}"
    elif etype in (EVENT_VOICE_CHECK_RESULT, "VOICE_VERIFIED", "TELEGRAM_VOICE_CHECK"):
        risk = details.get("risk", "unknown")
        summary = f"Voice check completed (risk: {risk}) by {actor}{req_suffix}"
    elif etype == EVENT_APPROVAL_SHOWN:
        summary = f"Approval card shown to {actor}{req_suffix}"
    elif etype == "APPROVAL_ISSUED":
        summary = f"2-minute approval window issued for {actor}{req_suffix}"
    elif etype == EVENT_APPROVAL_DECISION:
        summary = f"Approver {actor} recorded decision '{details.get('decision', 'unknown')}'{req_suffix}"
    elif etype == "APPROVAL_GRANTED":
        summary = f"Approval granted by {actor}{req_suffix}"
    elif etype == "APPROVAL_REJECTED":
        summary = f"Request rejected by {actor}{req_suffix}"
    elif etype == "APPROVAL_CODE_MISMATCH":
        summary = f"Wrong number-match code entered by {actor}{req_suffix}"
    elif etype in (EVENT_BLOCK, "REQUEST_BLOCKED", "APPROVAL_BLOCKED_VOICE_FAIL", "EXECUTION_BLOCKED"):
        summary = f"Request blocked by {actor}{req_suffix}"
    elif etype == EVENT_LIVE_CALL_STATE:
        state_val = details.get("state", "unknown")
        summary = f"Live-call alert updated to {state_val}{req_suffix}"
    elif etype == "LIVE_GUARD_SESSION_STARTED":
        summary = f"Live Call Guard started with consent by {actor}{req_suffix}"
    elif etype == "LIVE_GUARD_CHUNK_RESULT":
        risk = details.get("risk", "unknown")
        idx = details.get("chunk_index", "?")
        summary = f"Live Call Guard chunk #{idx} checked (risk: {risk}){req_suffix}"
    elif etype == "LIVE_GUARD_STATE_CHANGED":
        summary = (
            f"Live Call Guard state changed to {details.get('new_state', 'unknown')}{req_suffix}"
        )
    elif etype == "LIVE_GUARD_FAIL_ALERT_SENT":
        summary = f"Live Call Guard FAIL alert dispatched and request blocked{req_suffix}"
    elif etype == "LIVE_GUARD_SESSION_ENDED":
        summary = f"Live Call Guard session ended ({details.get('final_state', 'unknown')}){req_suffix}"
    elif etype == EVENT_EXPIRY:
        summary = f"Approval window expired{req_suffix}"
    elif etype == "ACTION_EXECUTED":
        summary = f"Simulated action executed by {actor}{req_suffix}"
    elif etype == "TELEGRAM_APPROVER_REGISTERED":
        summary = f"Telegram approver chat registered for {actor}"
    else:
        summary = f"{etype.replace('_', ' ').title()} by {actor}{req_suffix}"

    return f"#{event.get('id', '?')} [{ts}] {summary}"


def build_audit_report(
    conn: sqlite3.Connection,
    *,
    now: datetime | None = None,
) -> str:
    """Run existing verify_chain + list_events(limit=5) and format a plain-language report."""
    checked_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    ts_label = checked_at.strftime("%Y-%m-%d %H:%M:%S UTC")

    verification = verify_chain(conn)
    recent = list_events(conn, limit=5)

    valid = bool(verification["valid"])
    total = int(verification["checked_events"])
    broken_at = verification.get("broken_at_id")

    if valid:
        status_line = "Chain status: VALID (no tampering detected)"
    else:
        status_line = (
            f"Chain status: BROKEN — hash mismatch detected at event #{broken_at}. "
            "Do not trust modified audit entries; escalate to a security admin."
        )

    lines = [
        "BREAK-GLASS AUDIT REPORT",
        status_line,
        f"Total events in chain: {total}",
        f"Checked at: {ts_label}",
        "",
        "Last 5 events:",
    ]

    if not recent:
        lines.append("- No audit events recorded yet.")
    else:
        for ev in recent:
            lines.append(f"- {describe_audit_event(ev)}")

    return "\n".join(lines)
