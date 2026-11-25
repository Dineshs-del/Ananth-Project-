"""Simulated (mock) emergency action execution for hackathon prototype."""

from __future__ import annotations

import hmac
import sqlite3
from datetime import datetime, timezone
from typing import Any

from ..audit.service import record_event
from ..core.database import utc_now_iso
from .decision_table import evaluate_decision


def _is_past(iso_ts: str) -> bool:
    dt = datetime.fromisoformat(iso_ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) > dt


def execute_simulated_action(
    conn: sqlite3.Connection,
    *,
    request_row: sqlite3.Row,
    approval_row: sqlite3.Row | None,
    actor_id: str,
) -> dict[str, Any]:
    """Validate decision table + action hash + one-time consumption, then run simulated action."""
    if request_row["status"] == "EXECUTED":
        record_event(
            conn,
            event_type="EXECUTION_REUSE_BLOCKED",
            actor_id=actor_id,
            request_id=request_row["request_id"],
            details={"reason": "Request already executed."},
        )
        raise ValueError("This break-glass request has already been executed (one-time use).")

    if approval_row is None:
        decision = evaluate_decision(
            voice_risk=request_row["voice_risk"],
            approval_status=None,
        )
        raise PermissionError(decision.reason)

    # Enforce cryptographic action-binding between request and approval record.
    if not hmac.compare_digest(str(request_row["action_hash"]), str(approval_row["action_hash"])):
        record_event(
            conn,
            event_type="EXECUTION_ACTION_MISMATCH_BLOCKED",
            actor_id=actor_id,
            request_id=request_row["request_id"],
            details={"approval_id": approval_row["approval_id"]},
        )
        raise PermissionError("Action hash mismatch: approval is not bound to this exact action.")

    reused = approval_row["status"] == "CONSUMED" or approval_row["consumed_at"] is not None
    expired = approval_row["status"] == "EXPIRED" or _is_past(approval_row["expires_at"])

    decision = evaluate_decision(
        voice_risk=request_row["voice_risk"],
        approval_status=approval_row["status"],
        approval_expired=expired,
        approval_reused=reused,
        peer_review_confirmed=bool(approval_row["peer_review_confirmed"]),
    )

    if not decision.allowed:
        if expired and approval_row["status"] in ("PENDING", "APPROVED"):
            conn.execute(
                "UPDATE approval_records SET status = 'EXPIRED' WHERE approval_id = ?",
                (approval_row["approval_id"],),
            )
        record_event(
            conn,
            event_type="EXECUTION_BLOCKED",
            actor_id=actor_id,
            request_id=request_row["request_id"],
            details={
                "approval_id": approval_row["approval_id"],
                "outcome": decision.outcome,
                "reason": decision.reason,
            },
        )
        raise PermissionError(decision.reason)

    now = utc_now_iso()
    receipt = (
        f"[SIMULATED] Executed '{request_row['action_summary']}' on "
        f"'{request_row['target_resource']}' for {request_row['employee_id']} "
        f"(approved by {approval_row['decided_by']} via {approval_row['approval_id']}). "
        "No real production infrastructure was modified."
    )

    # Atomically consume one-time approval so it can never be reused.
    updated = conn.execute(
        """
        UPDATE approval_records
        SET status = 'CONSUMED', consumed_at = ?
        WHERE approval_id = ? AND status = 'APPROVED' AND consumed_at IS NULL
        """,
        (now, approval_row["approval_id"]),
    )
    if updated.rowcount != 1:
        raise ValueError("Approval record was already consumed (one-time use enforced).")

    conn.execute(
        """
        UPDATE break_glass_requests
        SET status = 'EXECUTED',
            execution_receipt = ?,
            executed_at = ?,
            updated_at = ?
        WHERE request_id = ?
        """,
        (receipt, now, now, request_row["request_id"]),
    )

    record_event(
        conn,
        event_type="ACTION_EXECUTED",
        actor_id=actor_id,
        request_id=request_row["request_id"],
        details={
            "approval_id": approval_row["approval_id"],
            "simulated": True,
            "action_summary": request_row["action_summary"],
            "target_resource": request_row["target_resource"],
            "receipt": receipt,
        },
    )

    return {
        "request_id": request_row["request_id"],
        "approval_id": approval_row["approval_id"],
        "status": "EXECUTED",
        "simulated": True,
        "action_summary": request_row["action_summary"],
        "target_resource": request_row["target_resource"],
        "execution_receipt": receipt,
        "executed_at": now,
    }
