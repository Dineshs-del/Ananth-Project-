"""Break-glass approval state machine, dual-control checks, and Member B AI integration."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from ..ai.challenge import generate_challenge
from ..ai.voice_check import verify_voice
from ..audit.service import record_event
from ..core.database import utc_now_iso

APPROVER_ROLES = {"approver", "security_admin"}
TERMINAL_STATUSES = {"APPROVED", "REJECTED", "EXPIRED"}
CALLBACK_REQUIRED_STATUSES = {"ESCALATED_REVIEW", "HIGH_RISK_HOLD"}
VALID_RISKS = {"PASS", "BORDERLINE", "FAIL"}


class ApprovalError(Exception):
    """Base workflow error."""


class NotFoundError(ApprovalError):
    """Resource was not found."""


class ConflictError(ApprovalError):
    """Action conflicts with current workflow state."""


class ForbiddenError(ApprovalError):
    """Actor lacks permission or violated dual-control rules."""


def _row_to_employee(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "employee_id": row["employee_id"],
        "display_name": row["display_name"],
        "role": row["role"],
        "known_callback_hint": row["known_callback_hint"],
        "enrolled_samples": int(row["enrolled_samples"]),
        "created_at": row["created_at"],
    }


def _row_to_request(row: sqlite3.Row) -> dict[str, Any]:
    voice_result: dict[str, Any] | None = None
    if row["voice_risk"] is not None:
        voice_result = {
            "aletheia": json.loads(row["aletheia_json"]) if row["aletheia_json"] else None,
            "eidos": json.loads(row["eidos_json"]) if row["eidos_json"] else None,
            "risk": row["voice_risk"],
            "reason": row["voice_reason"] or "",
        }
    return {
        "request_id": row["request_id"],
        "employee_id": row["employee_id"],
        "employee_name": row["display_name"] if "display_name" in row.keys() else None,
        "known_callback_hint": (
            row["known_callback_hint"] if "known_callback_hint" in row.keys() else ""
        ),
        "action_summary": row["action_summary"],
        "target_resource": row["target_resource"],
        "justification": row["justification"],
        "status": row["status"],
        "challenge_phrase": row["challenge_phrase"],
        "challenge_expires_at": row["challenge_expires_at"],
        "voice_attempts": int(row["voice_attempts"]),
        "voice_result": voice_result,
        "reviewer_id": row["reviewer_id"],
        "decision_notes": row["decision_notes"],
        "callback_verified": bool(row["callback_verified"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "decided_at": row["decided_at"],
    }


def _expires_at_iso(ttl_seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()


def _is_expired(iso_timestamp: str) -> bool:
    expires = datetime.fromisoformat(iso_timestamp)
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) > expires


def create_employee(
    conn: sqlite3.Connection,
    *,
    employee_id: str,
    display_name: str,
    role: str,
    known_callback_hint: str = "",
) -> dict[str, Any]:
    existing = conn.execute(
        "SELECT employee_id FROM employees WHERE employee_id = ?",
        (employee_id,),
    ).fetchone()
    if existing is not None:
        raise ConflictError(f"Employee '{employee_id}' already exists.")

    now = utc_now_iso()
    conn.execute(
        """
        INSERT INTO employees (
            employee_id, display_name, role, known_callback_hint, enrolled_samples, created_at
        ) VALUES (?, ?, ?, ?, 0, ?)
        """,
        (employee_id, display_name.strip(), role, known_callback_hint.strip(), now),
    )
    record_event(
        conn,
        event_type="EMPLOYEE_CREATED",
        actor_id=employee_id,
        details={"role": role, "display_name": display_name.strip()},
    )
    return get_employee(conn, employee_id)


def get_employee(conn: sqlite3.Connection, employee_id: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT employee_id, display_name, role, known_callback_hint, enrolled_samples, created_at
        FROM employees
        WHERE employee_id = ?
        """,
        (employee_id,),
    ).fetchone()
    if row is None:
        raise NotFoundError(f"Employee '{employee_id}' not found.")
    return _row_to_employee(row)


def list_employees(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT employee_id, display_name, role, known_callback_hint, enrolled_samples, created_at
        FROM employees
        ORDER BY employee_id ASC
        """
    ).fetchall()
    return [_row_to_employee(row) for row in rows]


def record_enrollment_count(
    conn: sqlite3.Connection,
    *,
    employee_id: str,
    added_samples: int,
    cap_reached: bool,
) -> dict[str, Any]:
    get_employee(conn, employee_id)
    if added_samples > 0:
        conn.execute(
            """
            UPDATE employees
            SET enrolled_samples = MIN(5, enrolled_samples + ?)
            WHERE employee_id = ?
            """,
            (added_samples, employee_id),
        )
    record_event(
        conn,
        event_type="EMPLOYEE_ENROLLED",
        actor_id=employee_id,
        details={"added_samples": added_samples, "cap_reached": cap_reached},
    )
    return get_employee(conn, employee_id)


def create_request(
    conn: sqlite3.Connection,
    *,
    employee_id: str,
    action_summary: str,
    target_resource: str,
    justification: str,
    challenge_ttl_seconds: int = 300,
) -> dict[str, Any]:
    """Create a break-glass access request and issue a fresh spoken challenge."""
    get_employee(conn, employee_id)

    request_id = f"bg_{uuid.uuid4().hex[:12]}"
    now = utc_now_iso()
    challenge_phrase = generate_challenge()
    challenge_expires_at = _expires_at_iso(challenge_ttl_seconds)

    conn.execute(
        """
        INSERT INTO break_glass_requests (
            request_id, employee_id, action_summary, target_resource, justification,
            status, challenge_phrase, challenge_expires_at,
            voice_attempts, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 'PENDING_VOICE', ?, ?, 0, ?, ?)
        """,
        (
            request_id,
            employee_id,
            action_summary.strip(),
            target_resource.strip(),
            justification.strip(),
            challenge_phrase,
            challenge_expires_at,
            now,
            now,
        ),
    )

    record_event(
        conn,
        event_type="REQUEST_CREATED",
        actor_id=employee_id,
        request_id=request_id,
        details={
            "action_summary": action_summary.strip(),
            "target_resource": target_resource.strip(),
            "challenge_expires_at": challenge_expires_at,
        },
    )
    return get_request(conn, request_id)


def get_request(conn: sqlite3.Connection, request_id: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT r.*, e.display_name, e.known_callback_hint
        FROM break_glass_requests r
        JOIN employees e ON e.employee_id = r.employee_id
        WHERE r.request_id = ?
        """,
        (request_id,),
    ).fetchone()
    if row is None:
        raise NotFoundError(f"Break-glass request '{request_id}' not found.")
    return _row_to_request(row)


def list_requests(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    employee_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    safe_limit = max(1, min(limit, 500))
    clauses: list[str] = []
    params: list[Any] = []
    if status:
        clauses.append("r.status = ?")
        params.append(status)
    if employee_id:
        clauses.append("r.employee_id = ?")
        params.append(employee_id)

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(safe_limit)

    rows = conn.execute(
        f"""
        SELECT r.*, e.display_name, e.known_callback_hint
        FROM break_glass_requests r
        JOIN employees e ON e.employee_id = r.employee_id
        {where_sql}
        ORDER BY r.created_at DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [_row_to_request(row) for row in rows]


def refresh_challenge(
    conn: sqlite3.Connection,
    *,
    request_id: str,
    challenge_ttl_seconds: int = 300,
) -> dict[str, Any]:
    """Rotate the spoken challenge phrase for an active request (e.g. re-record)."""
    req = get_request(conn, request_id)
    if req["status"] in TERMINAL_STATUSES:
        raise ConflictError(
            f"Cannot refresh challenge for request in terminal status '{req['status']}'."
        )

    now = utc_now_iso()
    new_phrase = generate_challenge()
    new_expires = _expires_at_iso(challenge_ttl_seconds)

    conn.execute(
        """
        UPDATE break_glass_requests
        SET challenge_phrase = ?, challenge_expires_at = ?, updated_at = ?
        WHERE request_id = ?
        """,
        (new_phrase, new_expires, now, request_id),
    )

    record_event(
        conn,
        event_type="CHALLENGE_REFRESHED",
        actor_id=req["employee_id"],
        request_id=request_id,
        details={"challenge_expires_at": new_expires},
    )
    return get_request(conn, request_id)


def _normalize_ai_output(raw: Any) -> dict[str, Any]:
    """Fail-closed defensive validation of Member B's verify_voice output."""
    if not isinstance(raw, dict):
        return {
            "aletheia": None,
            "eidos": None,
            "risk": "BORDERLINE",
            "reason": (
                "Voice verification returned an unexpected result and failed closed. "
                "A score is evidence, not proof. "
                "Next step: ask the requester to re-record, or call back on a number you already know."
            ),
        }

    risk = raw.get("risk")
    reason = raw.get("reason")
    if risk not in VALID_RISKS or not isinstance(reason, str) or not reason.strip():
        return {
            "aletheia": raw.get("aletheia") if isinstance(raw.get("aletheia"), dict) else None,
            "eidos": raw.get("eidos") if isinstance(raw.get("eidos"), dict) else None,
            "risk": "BORDERLINE",
            "reason": (
                "Voice verification returned an incomplete result and failed closed. "
                "A score is evidence, not proof. "
                "Next step: ask the requester to re-record, or call back on a number you already know."
            ),
        }

    return {
        "aletheia": raw.get("aletheia"),
        "eidos": raw.get("eidos"),
        "risk": risk,
        "reason": reason,
    }


def _status_for_risk(risk: str) -> str:
    if risk == "PASS":
        return "PENDING_APPROVAL"
    if risk == "FAIL":
        return "HIGH_RISK_HOLD"
    return "ESCALATED_REVIEW"


def submit_voice_verification(
    conn: sqlite3.Connection,
    *,
    request_id: str,
    audio_path: str,
    voice_verifier: Callable[[str, str], dict[str, Any]] = verify_voice,
) -> dict[str, Any]:
    """Run Member B's verify_voice on uploaded audio, update state, and audit evidence."""
    req = get_request(conn, request_id)
    if req["status"] in TERMINAL_STATUSES:
        raise ConflictError(
            f"Cannot verify voice for request in terminal status '{req['status']}'."
        )

    if _is_expired(req["challenge_expires_at"]):
        raise ConflictError(
            "Spoken challenge has expired. Request a fresh challenge phrase and re-record."
        )

    try:
        raw_result = voice_verifier(audio_path, req["employee_id"])
    except Exception:
        raw_result = None

    result = _normalize_ai_output(raw_result)
    next_status = _status_for_risk(result["risk"])
    now = utc_now_iso()

    conn.execute(
        """
        UPDATE break_glass_requests
        SET status = ?,
            voice_attempts = voice_attempts + 1,
            voice_risk = ?,
            voice_reason = ?,
            aletheia_json = ?,
            eidos_json = ?,
            updated_at = ?
        WHERE request_id = ?
        """,
        (
            next_status,
            result["risk"],
            result["reason"],
            json.dumps(result["aletheia"]) if result["aletheia"] is not None else None,
            json.dumps(result["eidos"]) if result["eidos"] is not None else None,
            now,
            request_id,
        ),
    )

    record_event(
        conn,
        event_type="VOICE_VERIFIED",
        actor_id=req["employee_id"],
        request_id=request_id,
        details={
            "risk": result["risk"],
            "next_status": next_status,
            "aletheia": result["aletheia"],
            "eidos": result["eidos"],
            "reason": result["reason"],
        },
    )
    return get_request(conn, request_id)


def decide_request(
    conn: sqlite3.Connection,
    *,
    request_id: str,
    reviewer_id: str,
    action: str,
    notes: str = "",
    callback_verified: bool = False,
) -> dict[str, Any]:
    """Approve or reject a break-glass request with dual-control and callback safeguards."""
    req = get_request(conn, request_id)
    reviewer = get_employee(conn, reviewer_id)

    if req["status"] in TERMINAL_STATUSES:
        raise ConflictError(
            f"Break-glass request '{request_id}' is already finalized ({req['status']})."
        )

    if reviewer["role"] not in APPROVER_ROLES:
        raise ForbiddenError(
            f"Employee '{reviewer_id}' has role '{reviewer['role']}' and is not authorized to review break-glass requests."
        )

    # Separation of duties: requester can never approve or reject their own request.
    if reviewer["employee_id"] == req["employee_id"]:
        raise ForbiddenError(
            "Dual-control violation: a requester cannot decide their own break-glass request."
        )

    clean_notes = notes.strip()

    if action == "approve":
        if req["status"] == "PENDING_VOICE":
            raise ConflictError(
                "Cannot approve before voice verification has been completed."
            )
        if req["status"] in CALLBACK_REQUIRED_STATUSES:
            if not callback_verified or len(clean_notes) < 5:
                raise ConflictError(
                    "Borderline or high-risk voice checks require out-of-band callback verification "
                    "on a known number (set callback_verified=true and document callback details in notes)."
                )
        final_status = "APPROVED"
        event_type = "REQUEST_APPROVED"
    elif action == "reject":
        final_status = "REJECTED"
        event_type = "REQUEST_REJECTED"
    else:
        raise ApprovalError(f"Unsupported decision action '{action}'.")

    now = utc_now_iso()
    conn.execute(
        """
        UPDATE break_glass_requests
        SET status = ?,
            reviewer_id = ?,
            decision_notes = ?,
            callback_verified = ?,
            updated_at = ?,
            decided_at = ?
        WHERE request_id = ?
        """,
        (
            final_status,
            reviewer["employee_id"],
            clean_notes,
            1 if callback_verified else 0,
            now,
            now,
            request_id,
        ),
    )

    record_event(
        conn,
        event_type=event_type,
        actor_id=reviewer["employee_id"],
        request_id=request_id,
        details={
            "previous_status": req["status"],
            "final_status": final_status,
            "voice_risk": req["voice_result"]["risk"] if req["voice_result"] else None,
            "callback_verified": callback_verified,
            "notes": clean_notes,
        },
    )
    return get_request(conn, request_id)
