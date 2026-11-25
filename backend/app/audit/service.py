"""Append-only hash-chained audit log service."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from ..core.database import utc_now_iso

GENESIS_HASH = "0" * 64


def _compute_event_hash(
    *,
    prev_hash: str,
    event_type: str,
    actor_id: str,
    request_id: str | None,
    details_json: str,
    created_at: str,
) -> str:
    payload = json.dumps(
        {
            "prev_hash": prev_hash,
            "event_type": event_type,
            "actor_id": actor_id,
            "request_id": request_id,
            "details_json": details_json,
            "created_at": created_at,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def record_event(
    conn: sqlite3.Connection,
    *,
    event_type: str,
    actor_id: str,
    request_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append a tamper-evident audit event chained to the previous event hash."""
    row = conn.execute(
        "SELECT event_hash FROM audit_events ORDER BY id DESC LIMIT 1"
    ).fetchone()
    prev_hash = str(row["event_hash"]) if row is not None else GENESIS_HASH

    created_at = utc_now_iso()
    clean_details = details or {}
    details_json = json.dumps(clean_details, sort_keys=True, separators=(",", ":"))
    event_hash = _compute_event_hash(
        prev_hash=prev_hash,
        event_type=event_type,
        actor_id=actor_id,
        request_id=request_id,
        details_json=details_json,
        created_at=created_at,
    )

    cursor = conn.execute(
        """
        INSERT INTO audit_events (
            event_type, actor_id, request_id, details_json, created_at, prev_hash, event_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (event_type, actor_id, request_id, details_json, created_at, prev_hash, event_hash),
    )

    return {
        "id": int(cursor.lastrowid),
        "event_type": event_type,
        "actor_id": actor_id,
        "request_id": request_id,
        "details": clean_details,
        "created_at": created_at,
        "prev_hash": prev_hash,
        "event_hash": event_hash,
    }


def list_events(
    conn: sqlite3.Connection,
    *,
    request_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List audit events newest-first, optionally filtered by request_id."""
    safe_limit = max(1, min(limit, 500))
    if request_id is not None:
        rows = conn.execute(
            """
            SELECT id, event_type, actor_id, request_id, details_json, created_at, prev_hash, event_hash
            FROM audit_events
            WHERE request_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (request_id, safe_limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, event_type, actor_id, request_id, details_json, created_at, prev_hash, event_hash
            FROM audit_events
            ORDER BY id DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()

    events: list[dict[str, Any]] = []
    for row in rows:
        events.append(
            {
                "id": int(row["id"]),
                "event_type": row["event_type"],
                "actor_id": row["actor_id"],
                "request_id": row["request_id"],
                "details": json.loads(row["details_json"]),
                "created_at": row["created_at"],
                "prev_hash": row["prev_hash"],
                "event_hash": row["event_hash"],
            }
        )
    return events


def verify_chain(conn: sqlite3.Connection) -> dict[str, Any]:
    """Recompute the entire audit hash chain to detect tampering."""
    rows = conn.execute(
        """
        SELECT id, event_type, actor_id, request_id, details_json, created_at, prev_hash, event_hash
        FROM audit_events
        ORDER BY id ASC
        """
    ).fetchall()

    expected_prev = GENESIS_HASH
    checked = 0
    for row in rows:
        checked += 1
        if row["prev_hash"] != expected_prev:
            return {"valid": False, "checked_events": checked, "broken_at_id": int(row["id"])}

        recomputed = _compute_event_hash(
            prev_hash=row["prev_hash"],
            event_type=row["event_type"],
            actor_id=row["actor_id"],
            request_id=row["request_id"],
            details_json=row["details_json"],
            created_at=row["created_at"],
        )
        if row["event_hash"] != recomputed:
            return {"valid": False, "checked_events": checked, "broken_at_id": int(row["id"])}
        expected_prev = row["event_hash"]

    return {"valid": True, "checked_events": checked, "broken_at_id": None}
