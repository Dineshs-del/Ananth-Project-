"""Break-glass request lifecycle, action-bound 2-min approvals, and Member B AI integration."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from ..ai.challenge import generate_challenge
from ..ai.voice_check import verify_voice
from ..audit.service import record_event
from ..core.database import utc_now_iso
from .decision_table import evaluate_decision
from .executor import execute_simulated_action

APPROVER_ROLES = {"approver", "security_admin"}
TERMINAL_REQUEST_STATUSES = {"BLOCKED", "REJECTED", "EXECUTED", "EXPIRED"}
APPROVABLE_REQUEST_STATUSES = {"PENDING_APPROVAL", "ESCALATED_REVIEW"}
VALID_RISKS = {"PASS", "BORDERLINE", "FAIL"}


class ApprovalError(Exception):
    """Base workflow error."""


class NotFoundError(ApprovalError):
    """Resource was not found."""


class ConflictError(ApprovalError):
    """Action conflicts with current workflow state."""


class ForbiddenError(ApprovalError):
    """Actor lacks permission, mismatched number code, or violated different-person rule."""


def compute_action_hash(
    *,
    employee_id: str,
    action_summary: str,
    target_resource: str,
    justification: str,
) -> str:
    """Bind approvals cryptographically to the exact requested break-glass operation."""
    canonical = json.dumps(
        {
            "employee_id": employee_id,
            "action_summary": action_summary.strip(),
            "target_resource": target_resource.strip(),
            "justification": justification.strip(),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _generate_number_matching() -> tuple[str, list[str]]:
    """Generate a 2-digit matching code plus 2 distinct decoys using secrets."""
    chosen: list[str] = []
    while len(chosen) < 3:
        code = f"{secrets.randbelow(90) + 10}"
        if code not in chosen:
            chosen.append(code)
    target = chosen[0]
    options = list(chosen)
    for i in range(len(options) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        options[i], options[j] = options[j], options[i]
    return target, options


def _expires_at_iso(ttl_seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()


def _remaining_seconds(iso_timestamp: str) -> int:
    expires = datetime.fromisoformat(iso_timestamp)
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    delta = int((expires - datetime.now(timezone.utc)).total_seconds())
    return max(0, delta)


def _is_expired(iso_timestamp: str) -> bool:
    expires = datetime.fromisoformat(iso_timestamp)
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) > expires


def _row_to_employee(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "employee_id": row["employee_id"],
        "display_name": row["display_name"],
        "role": row["role"],
        "known_callback_hint": row["known_callback_hint"],
        "enrolled_samples": int(row["enrolled_samples"]),
        "created_at": row["created_at"],
    }


def _row_to_approval(row: sqlite3.Row) -> dict[str, Any]:
    status = row["status"]
    remaining = _remaining_seconds(row["expires_at"])
    if status == "PENDING" and remaining == 0:
        status = "EXPIRED"
    return {
        "approval_id": row["approval_id"],
        "request_id": row["request_id"],
        "action_hash": row["action_hash"],
        "action_summary": row["action_summary"],
        "target_resource": row["target_resource"],
        "justification": row["justification"],
        "requester_id": row["requester_id"],
        "requester_name": row["requester_name"],
        "known_callback_hint": row["known_callback_hint"],
        "voice_risk": row["voice_risk"],
        "voice_reason": row["voice_reason"],
        "matching_number": row["matching_number"],
        "number_options": json.loads(row["number_options_json"]),
        "requires_peer_review": bool(row["requires_peer_review"]),
        "status": status,
        "expires_at": row["expires_at"],
        "remaining_seconds": remaining,
        "decided_by": row["decided_by"],
        "decision_notes": row["decision_notes"],
        "peer_review_confirmed": bool(row["peer_review_confirmed"]),
        "created_at": row["created_at"],
        "decided_at": row["decided_at"],
        "consumed_at": row["consumed_at"],
    }


def _fetch_latest_approval_dict(conn: sqlite3.Connection, request_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT a.*,
               r.action_summary, r.target_resource, r.justification,
               r.employee_id AS requester_id,
               r.voice_risk, r.voice_reason,
               e.display_name AS requester_name,
               e.known_callback_hint
        FROM approval_records a
        JOIN break_glass_requests r ON r.request_id = a.request_id
        JOIN employees e ON e.employee_id = r.employee_id
        WHERE a.request_id = ?
        ORDER BY a.created_at DESC
        LIMIT 1
        """,
        (request_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_approval(row)


def _row_to_request(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
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
        "action_hash": row["action_hash"],
        "status": row["status"],
        "challenge_phrase": row["challenge_phrase"],
        "challenge_expires_at": row["challenge_expires_at"],
        "voice_attempts": int(row["voice_attempts"]),
        "voice_result": voice_result,
        "active_approval": _fetch_latest_approval_dict(conn, row["request_id"]),
        "reviewer_id": row["reviewer_id"],
        "decision_notes": row["decision_notes"],
        "execution_receipt": row["execution_receipt"],
        "executed_at": row["executed_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "decided_at": row["decided_at"],
    }


# --- Employees ---


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


# --- Requests & Voice Challenge ---


def create_request(
    conn: sqlite3.Connection,
    *,
    employee_id: str,
    action_summary: str,
    target_resource: str,
    justification: str,
    challenge_ttl_seconds: int = 300,
) -> dict[str, Any]:
    """Create an emergency request with action_hash binding and a fresh spoken challenge."""
    get_employee(conn, employee_id)

    request_id = f"bg_{uuid.uuid4().hex[:12]}"
    now = utc_now_iso()
    challenge_phrase = generate_challenge()
    challenge_expires_at = _expires_at_iso(challenge_ttl_seconds)
    action_hash = compute_action_hash(
        employee_id=employee_id,
        action_summary=action_summary,
        target_resource=target_resource,
        justification=justification,
    )

    conn.execute(
        """
        INSERT INTO break_glass_requests (
            request_id, employee_id, action_summary, target_resource, justification,
            action_hash, status, challenge_phrase, challenge_expires_at,
            voice_attempts, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'PENDING_VOICE', ?, ?, 0, ?, ?)
        """,
        (
            request_id,
            employee_id,
            action_summary.strip(),
            target_resource.strip(),
            justification.strip(),
            action_hash,
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
            "action_hash": action_hash,
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
    return _row_to_request(conn, row)


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
    return [_row_to_request(conn, row) for row in rows]


def refresh_challenge(
    conn: sqlite3.Connection,
    *,
    request_id: str,
    challenge_ttl_seconds: int = 300,
) -> dict[str, Any]:
    """Rotate the spoken challenge phrase for an active non-terminal request."""
    req = get_request(conn, request_id)
    if req["status"] in TERMINAL_REQUEST_STATUSES or req["status"] == "APPROVED":
        raise ConflictError(
            f"Cannot refresh challenge for request in status '{req['status']}'."
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


# --- Action-Bound Approval Records (2-minute expiry, number matching, one-time use) ---


def _expire_stale_approvals(conn: sqlite3.Connection, request_id: str | None = None) -> None:
    now_iso = utc_now_iso()
    if request_id is not None:
        conn.execute(
            """
            UPDATE approval_records
            SET status = 'EXPIRED'
            WHERE request_id = ? AND status = 'PENDING' AND expires_at < ?
            """,
            (request_id, now_iso),
        )
    else:
        conn.execute(
            """
            UPDATE approval_records
            SET status = 'EXPIRED'
            WHERE status = 'PENDING' AND expires_at < ?
            """,
            (now_iso,),
        )


def _create_approval_record(
    conn: sqlite3.Connection,
    *,
    request_id: str,
    action_hash: str,
    actor_id: str,
    requires_peer_review: bool,
    approval_ttl_seconds: int,
) -> dict[str, Any]:
    # Supersede any prior pending approval records for this request.
    conn.execute(
        "UPDATE approval_records SET status = 'EXPIRED' WHERE request_id = ? AND status = 'PENDING'",
        (request_id,),
    )

    approval_id = f"apr_{uuid.uuid4().hex[:12]}"
    matching_number, options = _generate_number_matching()
    now = utc_now_iso()
    expires_at = _expires_at_iso(approval_ttl_seconds)

    conn.execute(
        """
        INSERT INTO approval_records (
            approval_id, request_id, action_hash, matching_number, number_options_json,
            requires_peer_review, status, expires_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'PENDING', ?, ?)
        """,
        (
            approval_id,
            request_id,
            action_hash,
            matching_number,
            json.dumps(options),
            1 if requires_peer_review else 0,
            expires_at,
            now,
        ),
    )

    record_event(
        conn,
        event_type="APPROVAL_ISSUED",
        actor_id=actor_id,
        request_id=request_id,
        details={
            "approval_id": approval_id,
            "action_hash": action_hash,
            "requires_peer_review": requires_peer_review,
            "expires_at": expires_at,
        },
    )
    return get_approval(conn, approval_id)


def issue_approval_record(
    conn: sqlite3.Connection,
    *,
    request_id: str,
    approval_ttl_seconds: int = 120,
) -> dict[str, Any]:
    """Generate a fresh 2-minute approval record (e.g. after expiry) if eligible."""
    req = get_request(conn, request_id)
    if req["status"] not in APPROVABLE_REQUEST_STATUSES:
        raise ConflictError(
            f"Cannot issue approval record while request is in status '{req['status']}'."
        )
    if req["voice_result"] is None or req["voice_result"]["risk"] == "FAIL":
        raise ConflictError("Hard voice-verification failure cannot receive an approval record.")

    return _create_approval_record(
        conn,
        request_id=request_id,
        action_hash=req["action_hash"],
        actor_id=req["employee_id"],
        requires_peer_review=(req["voice_result"]["risk"] == "BORDERLINE"),
        approval_ttl_seconds=approval_ttl_seconds,
    )


def get_approval(conn: sqlite3.Connection, approval_id: str) -> dict[str, Any]:
    _expire_stale_approvals(conn)
    row = conn.execute(
        """
        SELECT a.*,
               r.action_summary, r.target_resource, r.justification,
               r.employee_id AS requester_id,
               r.voice_risk, r.voice_reason,
               e.display_name AS requester_name,
               e.known_callback_hint
        FROM approval_records a
        JOIN break_glass_requests r ON r.request_id = a.request_id
        JOIN employees e ON e.employee_id = r.employee_id
        WHERE a.approval_id = ?
        """,
        (approval_id,),
    ).fetchone()
    if row is None:
        raise NotFoundError(f"Approval record '{approval_id}' not found.")
    return _row_to_approval(row)


def list_pending_approvals(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return active non-expired approval records for the separate approver phone page."""
    _expire_stale_approvals(conn)
    rows = conn.execute(
        """
        SELECT a.*,
               r.action_summary, r.target_resource, r.justification,
               r.employee_id AS requester_id,
               r.voice_risk, r.voice_reason,
               e.display_name AS requester_name,
               e.known_callback_hint
        FROM approval_records a
        JOIN break_glass_requests r ON r.request_id = a.request_id
        JOIN employees e ON e.employee_id = r.employee_id
        WHERE a.status = 'PENDING'
        ORDER BY a.created_at DESC
        """
    ).fetchall()
    return [_row_to_approval(row) for row in rows]


# --- Voice Verification Integration (Member B contract) ---


def _normalize_ai_output(raw: Any) -> dict[str, Any]:
    """Fail-closed validation of Member B's verify_voice output."""
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


def submit_voice_verification(
    conn: sqlite3.Connection,
    *,
    request_id: str,
    audio_path: str,
    approval_ttl_seconds: int = 120,
    voice_verifier: Callable[[str, str], dict[str, Any]] = verify_voice,
) -> dict[str, Any]:
    """Call Member B's verify_voice, apply policy, and issue a 2-min approval record if eligible."""
    req = get_request(conn, request_id)
    if req["status"] in TERMINAL_REQUEST_STATUSES or req["status"] == "APPROVED":
        raise ConflictError(
            f"Cannot verify voice for request in status '{req['status']}'."
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
    risk = result["risk"]

    if risk == "PASS":
        next_status = "PENDING_APPROVAL"
    elif risk == "BORDERLINE":
        next_status = "ESCALATED_REVIEW"
    else:
        # Section 6: hard voice failure blocks the request; no approval record is issued.
        next_status = "BLOCKED"

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
            risk,
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
            "risk": risk,
            "next_status": next_status,
            "aletheia": result["aletheia"],
            "eidos": result["eidos"],
            "reason": result["reason"],
        },
    )

    if risk == "FAIL":
        conn.execute(
            "UPDATE approval_records SET status = 'EXPIRED' WHERE request_id = ? AND status = 'PENDING'",
            (request_id,),
        )
    else:
        _create_approval_record(
            conn,
            request_id=request_id,
            action_hash=req["action_hash"],
            actor_id=req["employee_id"],
            requires_peer_review=(risk == "BORDERLINE"),
            approval_ttl_seconds=approval_ttl_seconds,
        )

    return get_request(conn, request_id)


# --- Second-Device Number-Matching Decision & Mock Execution ---


def decide_approval(
    conn: sqlite3.Connection,
    *,
    approval_id: str,
    reviewer_id: str,
    action: str,
    matching_number: str | None = None,
    notes: str = "",
    peer_review_confirmed: bool = False,
) -> dict[str, Any]:
    """Approve or reject an action-bound 2-minute approval record on the approver phone."""
    row = conn.execute(
        "SELECT * FROM approval_records WHERE approval_id = ?",
        (approval_id,),
    ).fetchone()
    if row is None:
        raise NotFoundError(f"Approval record '{approval_id}' not found.")

    req = get_request(conn, row["request_id"])
    reviewer = get_employee(conn, reviewer_id)

    # 1. Section 6: Hard voice FAIL can NEVER be overridden by second-device approval.
    if req["voice_result"] and req["voice_result"]["risk"] == "FAIL":
        decision = evaluate_decision(voice_risk="FAIL", approval_status=row["status"])
        record_event(
            conn,
            event_type="APPROVAL_BLOCKED_VOICE_FAIL",
            actor_id=reviewer_id,
            request_id=req["request_id"],
            details={"approval_id": approval_id, "reason": decision.reason},
        )
        raise ConflictError(decision.reason)

    # 2. One-time use check.
    if row["status"] in ("APPROVED", "REJECTED", "CONSUMED") or row["consumed_at"] is not None:
        record_event(
            conn,
            event_type="APPROVAL_REUSE_BLOCKED",
            actor_id=reviewer_id,
            request_id=req["request_id"],
            details={"approval_id": approval_id, "current_status": row["status"]},
        )
        raise ConflictError("This approval record has already been used (one-time use enforced).")

    # 3. 2-minute expiry check.
    if row["status"] == "EXPIRED" or _is_expired(row["expires_at"]):
        conn.execute(
            "UPDATE approval_records SET status = 'EXPIRED' WHERE approval_id = ?",
            (approval_id,),
        )
        record_event(
            conn,
            event_type="APPROVAL_EXPIRED",
            actor_id=reviewer_id,
            request_id=req["request_id"],
            details={"approval_id": approval_id, "expires_at": row["expires_at"]},
        )
        raise ConflictError(
            "Approval code expired after 2 minutes. Generate a new approval record if still needed."
        )

    # 4. Authorised approver role & different-person rule.
    if reviewer["role"] not in APPROVER_ROLES:
        raise ForbiddenError(
            f"Employee '{reviewer_id}' has role '{reviewer['role']}' and cannot approve break-glass actions."
        )
    if reviewer["employee_id"] == req["employee_id"]:
        raise ForbiddenError(
            "Different-person rule violation: the requester cannot approve or reject their own request."
        )

    # 5. Action-binding integrity check.
    if not hmac.compare_digest(str(row["action_hash"]), str(req["action_hash"])):
        raise ConflictError("Action hash mismatch: approval record does not match the requested action.")

    clean_notes = notes.strip()
    now = utc_now_iso()

    if action == "approve":
        if not matching_number or not hmac.compare_digest(
            matching_number.strip(), str(row["matching_number"])
        ):
            record_event(
                conn,
                event_type="APPROVAL_CODE_MISMATCH",
                actor_id=reviewer_id,
                request_id=req["request_id"],
                details={"approval_id": approval_id},
            )
            raise ForbiddenError(
                "Number-matching code did not match. Verify the code shown on the requester screen."
            )

        if bool(row["requires_peer_review"]):
            if not peer_review_confirmed or len(clean_notes) < 5:
                raise ConflictError(
                    "Borderline voice result requires explicit peer review confirmation "
                    "(set peer_review_confirmed=true and record verification notes)."
                )

        conn.execute(
            """
            UPDATE approval_records
            SET status = 'APPROVED',
                decided_by = ?,
                decision_notes = ?,
                peer_review_confirmed = ?,
                decided_at = ?
            WHERE approval_id = ? AND status = 'PENDING'
            """,
            (
                reviewer["employee_id"],
                clean_notes,
                1 if peer_review_confirmed else 0,
                now,
                approval_id,
            ),
        )
        conn.execute(
            """
            UPDATE break_glass_requests
            SET status = 'APPROVED',
                reviewer_id = ?,
                decision_notes = ?,
                updated_at = ?,
                decided_at = ?
            WHERE request_id = ?
            """,
            (reviewer["employee_id"], clean_notes, now, now, req["request_id"]),
        )
        record_event(
            conn,
            event_type="APPROVAL_GRANTED",
            actor_id=reviewer["employee_id"],
            request_id=req["request_id"],
            details={
                "approval_id": approval_id,
                "voice_risk": req["voice_result"]["risk"] if req["voice_result"] else None,
                "peer_review_confirmed": peer_review_confirmed,
                "notes": clean_notes,
            },
        )
    elif action == "reject":
        conn.execute(
            """
            UPDATE approval_records
            SET status = 'REJECTED',
                decided_by = ?,
                decision_notes = ?,
                decided_at = ?
            WHERE approval_id = ? AND status = 'PENDING'
            """,
            (reviewer["employee_id"], clean_notes, now, approval_id),
        )
        conn.execute(
            """
            UPDATE break_glass_requests
            SET status = 'REJECTED',
                reviewer_id = ?,
                decision_notes = ?,
                updated_at = ?,
                decided_at = ?
            WHERE request_id = ?
            """,
            (reviewer["employee_id"], clean_notes, now, now, req["request_id"]),
        )
        record_event(
            conn,
            event_type="APPROVAL_REJECTED",
            actor_id=reviewer["employee_id"],
            request_id=req["request_id"],
            details={"approval_id": approval_id, "notes": clean_notes},
        )
    else:
        raise ApprovalError(f"Unsupported action '{action}'.")

    return get_request(conn, req["request_id"])


def block_request(
    conn: sqlite3.Connection,
    *,
    request_id: str,
    reviewer_id: str,
    reason: str = "",
) -> dict[str, Any]:
    """Mark a break-glass request BLOCKED by an authorised different-person approver.

    Expires any pending or unconsumed approvals and latches voice_risk='FAIL' so the
    blocked request can never be overridden by a subsequent approval.
    """
    req = get_request(conn, request_id)
    reviewer = get_employee(conn, reviewer_id)

    if reviewer["role"] not in APPROVER_ROLES:
        raise ForbiddenError(
            f"Employee '{reviewer_id}' has role '{reviewer['role']}' and cannot block break-glass requests."
        )
    if reviewer["employee_id"] == req["employee_id"]:
        raise ForbiddenError(
            "Different-person rule violation: the requester cannot block their own request."
        )
    if req["status"] == "EXECUTED":
        raise ConflictError("Cannot block a break-glass request that has already been executed.")

    now = utc_now_iso()
    clean_reason = reason.strip() or "Blocked by approver following live-call FAIL alert."

    # Invalidate any open approvals so execution is impossible.
    conn.execute(
        """
        UPDATE approval_records
        SET status = 'EXPIRED'
        WHERE request_id = ? AND status IN ('PENDING', 'APPROVED')
        """,
        (request_id,),
    )
    conn.execute(
        """
        UPDATE break_glass_requests
        SET status = 'BLOCKED',
            voice_risk = 'FAIL',
            voice_reason = COALESCE(NULLIF(voice_reason, ''), ?),
            reviewer_id = ?,
            decision_notes = ?,
            updated_at = ?,
            decided_at = ?
        WHERE request_id = ?
        """,
        (clean_reason, reviewer["employee_id"], clean_reason, now, now, request_id),
    )

    record_event(
        conn,
        event_type="REQUEST_BLOCKED",
        actor_id=reviewer["employee_id"],
        request_id=request_id,
        details={"reason": clean_reason[:200], "outcome": "blocked"},
    )
    return get_request(conn, request_id)


def execute_request(
    conn: sqlite3.Connection,
    *,
    request_id: str,
    actor_id: str,
) -> dict[str, Any]:
    """Run simulated mock execution after verifying the decision table and consuming approval once."""
    get_employee(conn, actor_id)
    req_row = conn.execute(
        "SELECT * FROM break_glass_requests WHERE request_id = ?",
        (request_id,),
    ).fetchone()
    if req_row is None:
        raise NotFoundError(f"Break-glass request '{request_id}' not found.")

    approval_row = conn.execute(
        """
        SELECT * FROM approval_records
        WHERE request_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (request_id,),
    ).fetchone()

    try:
        return execute_simulated_action(
            conn,
            request_row=req_row,
            approval_row=approval_row,
            actor_id=actor_id,
        )
    except ValueError as exc:
        raise ConflictError(str(exc)) from exc
    except PermissionError as exc:
        raise ForbiddenError(str(exc)) from exc
