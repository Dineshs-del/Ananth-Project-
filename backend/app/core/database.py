"""Parameterized SQLite persistence layer for Break-Glass Voice Gate."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .config import load_core_config

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS employees (
    employee_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('requester', 'approver', 'security_admin')),
    known_callback_hint TEXT NOT NULL DEFAULT '',
    enrolled_samples INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS break_glass_requests (
    request_id TEXT PRIMARY KEY,
    employee_id TEXT NOT NULL,
    action_summary TEXT NOT NULL,
    target_resource TEXT NOT NULL,
    justification TEXT NOT NULL,
    action_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN (
            'PENDING_VOICE',
            'PENDING_APPROVAL',
            'ESCALATED_REVIEW',
            'BLOCKED',
            'APPROVED',
            'REJECTED',
            'EXECUTED',
            'EXPIRED'
        )
    ),
    challenge_phrase TEXT NOT NULL,
    challenge_expires_at TEXT NOT NULL,
    voice_attempts INTEGER NOT NULL DEFAULT 0,
    voice_risk TEXT CHECK (voice_risk IN ('PASS', 'BORDERLINE', 'FAIL')),
    voice_reason TEXT,
    aletheia_json TEXT,
    eidos_json TEXT,
    reviewer_id TEXT,
    decision_notes TEXT,
    execution_receipt TEXT,
    executed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    decided_at TEXT,
    FOREIGN KEY (employee_id) REFERENCES employees(employee_id),
    FOREIGN KEY (reviewer_id) REFERENCES employees(employee_id)
);

CREATE INDEX IF NOT EXISTS idx_requests_status ON break_glass_requests(status);
CREATE INDEX IF NOT EXISTS idx_requests_employee ON break_glass_requests(employee_id);

CREATE TABLE IF NOT EXISTS approval_records (
    approval_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    action_hash TEXT NOT NULL,
    matching_number TEXT NOT NULL,
    number_options_json TEXT NOT NULL,
    requires_peer_review INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK (
        status IN ('PENDING', 'APPROVED', 'REJECTED', 'EXPIRED', 'CONSUMED')
    ),
    expires_at TEXT NOT NULL,
    decided_by TEXT,
    decision_notes TEXT,
    peer_review_confirmed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    decided_at TEXT,
    consumed_at TEXT,
    FOREIGN KEY (request_id) REFERENCES break_glass_requests(request_id),
    FOREIGN KEY (decided_by) REFERENCES employees(employee_id)
);

CREATE INDEX IF NOT EXISTS idx_approvals_request ON approval_records(request_id);
CREATE INDEX IF NOT EXISTS idx_approvals_status ON approval_records(status);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    request_id TEXT,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    event_hash TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_request ON audit_events(request_id);
"""

SAMPLE_EMPLOYEES = (
    ("emp_rahul", "Rahul — On-call Engineer", "requester", "Corp Directory Ext. 4102", 2),
    ("emp_priya", "Priya — Production Admin", "approver", "On-Call Directory Ext. 4208", 2),
    ("emp_carol", "Carol — Security Lead", "security_admin", "SOC Desk Ext. 4900", 2),
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def open_connection(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or load_core_config().database_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


@contextmanager
def get_db(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = open_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Path | None = None, *, seed_sample_employees: bool = True) -> None:
    """Create tables if needed and optionally seed demo employees."""
    with get_db(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        if seed_sample_employees:
            now = utc_now_iso()
            for emp_id, name, role, hint, samples in SAMPLE_EMPLOYEES:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO employees (
                        employee_id, display_name, role, known_callback_hint, enrolled_samples, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (emp_id, name, role, hint, samples, now),
                )
