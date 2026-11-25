"""Pytest suite for Member A: lifecycle, 2-min approvals, number-matching, decision table, mock execution, and audit chain."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

import pytest

try:
    from backend.app.approval import decision_table
    from backend.app.approval import service as approval_service
    from backend.app.audit import service as audit_service
    from backend.app.core.database import get_db, init_db
    from backend.app.core.storage import UploadValidationError, save_audio_upload
    from backend.app.main import create_app
except ModuleNotFoundError:
    from app.approval import decision_table  # type: ignore[no-redef]
    from app.approval import service as approval_service  # type: ignore[no-redef]
    from app.audit import service as audit_service  # type: ignore[no-redef]
    from app.core.database import get_db, init_db  # type: ignore[no-redef]
    from app.core.storage import UploadValidationError, save_audio_upload  # type: ignore[no-redef]
    from app.main import create_app  # type: ignore[no-redef]


@pytest.fixture()
def isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    db_path = tmp_path / "test_voice_gate.db"
    upload_dir = tmp_path / "uploads"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("UPLOAD_DIR", str(upload_dir))
    monkeypatch.setenv("AI_MODE", "fixture")
    monkeypatch.setenv("FIXTURE_SCENARIO", "pass")
    monkeypatch.setenv("APPROVAL_TTL_SECONDS", "120")
    monkeypatch.delenv("REVIEWER_API_TOKEN", raising=False)
    init_db(db_path, seed_sample_employees=True)
    return db_path


# --- Section 6 Decision Table tests ---


def test_decision_table_all_rows() -> None:
    assert decision_table.evaluate_decision(voice_risk="PASS", approval_status="APPROVED").allowed is True
    assert decision_table.evaluate_decision(voice_risk="PASS", approval_status="PENDING").outcome == "LOCKED"
    assert decision_table.evaluate_decision(voice_risk="PASS", approval_status="REJECTED").outcome == "BLOCK"
    assert decision_table.evaluate_decision(voice_risk="BORDERLINE", approval_status="PENDING").outcome == "LOCKED"
    assert decision_table.evaluate_decision(
        voice_risk="BORDERLINE", approval_status="APPROVED", peer_review_confirmed=False
    ).outcome == "LOCKED"
    assert decision_table.evaluate_decision(
        voice_risk="BORDERLINE", approval_status="APPROVED", peer_review_confirmed=True
    ).allowed is True
    # Hard FAIL can NEVER be overridden by approval
    assert decision_table.evaluate_decision(
        voice_risk="FAIL", approval_status="APPROVED", peer_review_confirmed=True
    ).outcome == "BLOCK"
    assert decision_table.evaluate_decision(
        voice_risk="PASS", approval_status="APPROVED", approval_expired=True
    ).outcome == "REJECT"
    assert decision_table.evaluate_decision(
        voice_risk="PASS", approval_status="APPROVED", approval_reused=True
    ).outcome == "REJECT"


def test_storage_validates_extension_and_size(tmp_path: Path) -> None:
    with pytest.raises(UploadValidationError, match="Unsupported audio format"):
        save_audio_upload(
            filename="evil.exe",
            stream=BytesIO(b"MZ"),
            upload_dir=tmp_path,
            max_bytes=1024,
        )

    with pytest.raises(UploadValidationError, match="exceeds"):
        save_audio_upload(
            filename="big.wav",
            stream=BytesIO(b"0" * 2048),
            upload_dir=tmp_path,
            max_bytes=1024,
        )

    saved = save_audio_upload(
        filename="../../clip.ogg",
        stream=BytesIO(b"OggSdemo"),
        upload_dir=tmp_path,
        max_bytes=1024,
    )
    assert saved.parent == tmp_path.resolve()
    assert saved.suffix == ".ogg"
    saved.unlink()


def test_pass_number_matching_one_time_execution_and_different_person(
    isolated_env: Path, tmp_path: Path
) -> None:
    wav = tmp_path / "voice.wav"
    wav.write_bytes(b"RIFFdemo")

    with get_db(isolated_env) as conn:
        req = approval_service.create_request(
            conn,
            employee_id="emp_rahul",
            action_summary="Deploy build 142 to production",
            target_resource="prod-api",
            justification="INC-402 emergency outage fix",
        )
        assert req["status"] == "PENDING_VOICE"

        verified = approval_service.submit_voice_verification(
            conn,
            request_id=req["request_id"],
            audio_path=str(wav),
        )
        assert verified["status"] == "PENDING_APPROVAL"
        approval = verified["active_approval"]
        assert approval is not None
        assert approval["matching_number"] in approval["number_options"]
        assert len(approval["number_options"]) == 3
        assert 0 < approval["remaining_seconds"] <= 120

        # Cannot execute before approval
        with pytest.raises(approval_service.ForbiddenError):
            approval_service.execute_request(
                conn, request_id=req["request_id"], actor_id="emp_rahul"
            )

        # Different-person rule blocks requester self-approval
        with pytest.raises(approval_service.ForbiddenError):
            approval_service.decide_approval(
                conn,
                approval_id=approval["approval_id"],
                reviewer_id="emp_rahul",
                action="approve",
                matching_number=approval["matching_number"],
            )

        # Wrong number-matching code rejected
        wrong_code = next(c for c in approval["number_options"] if c != approval["matching_number"])
        with pytest.raises(approval_service.ForbiddenError, match="Number-matching"):
            approval_service.decide_approval(
                conn,
                approval_id=approval["approval_id"],
                reviewer_id="emp_priya",
                action="approve",
                matching_number=wrong_code,
            )

        # Correct number-matching approval by second authorised person succeeds
        approved = approval_service.decide_approval(
            conn,
            approval_id=approval["approval_id"],
            reviewer_id="emp_priya",
            action="approve",
            matching_number=approval["matching_number"],
            notes="Verified incident ticket INC-402.",
        )
        assert approved["status"] == "APPROVED"

        # One-time use: re-approving same approval record fails
        with pytest.raises(approval_service.ConflictError, match="one-time"):
            approval_service.decide_approval(
                conn,
                approval_id=approval["approval_id"],
                reviewer_id="emp_carol",
                action="approve",
                matching_number=approval["matching_number"],
            )

        # Simulated mock execution succeeds once
        receipt = approval_service.execute_request(
            conn, request_id=req["request_id"], actor_id="emp_rahul"
        )
        assert receipt["status"] == "EXECUTED"
        assert receipt["simulated"] is True
        assert "[SIMULATED]" in receipt["execution_receipt"]

        # One-time execution: second execution blocked
        with pytest.raises(approval_service.ConflictError, match="already been executed"):
            approval_service.execute_request(
                conn, request_id=req["request_id"], actor_id="emp_rahul"
            )

        chain = audit_service.verify_chain(conn)
        assert chain["valid"] is True
        assert chain["checked_events"] >= 5


def test_fail_hard_blocks_and_cannot_be_approved_or_executed(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    wav = tmp_path / "voice.wav"
    wav.write_bytes(b"RIFFdemo")
    monkeypatch.setenv("FIXTURE_SCENARIO", "fail")

    with get_db(isolated_env) as conn:
        req = approval_service.create_request(
            conn,
            employee_id="emp_rahul",
            action_summary="Reset root admin password",
            target_resource="iam-prod",
            justification="Locked out of admin console",
        )
        checked = approval_service.submit_voice_verification(
            conn,
            request_id=req["request_id"],
            audio_path=str(wav),
        )
        assert checked["status"] == "BLOCKED"
        assert checked["active_approval"] is None

        # Cannot issue approval record for a hard FAIL
        with pytest.raises(approval_service.ConflictError):
            approval_service.issue_approval_record(conn, request_id=req["request_id"])

        # Cannot execute a hard FAIL
        with pytest.raises(approval_service.ForbiddenError, match="cannot override"):
            approval_service.execute_request(
                conn, request_id=req["request_id"], actor_id="emp_rahul"
            )


def test_borderline_requires_peer_review_and_expiry_blocks_approval(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    wav = tmp_path / "voice.wav"
    wav.write_bytes(b"RIFFdemo")
    monkeypatch.setenv("FIXTURE_SCENARIO", "borderline")

    with get_db(isolated_env) as conn:
        req = approval_service.create_request(
            conn,
            employee_id="emp_rahul",
            action_summary="Terraform apply edge-waf",
            target_resource="prod-waf",
            justification="Emergency rate-limit rule",
        )
        checked = approval_service.submit_voice_verification(
            conn,
            request_id=req["request_id"],
            audio_path=str(wav),
        )
        assert checked["status"] == "ESCALATED_REVIEW"
        approval = checked["active_approval"]
        assert approval is not None and approval["requires_peer_review"] is True

        # Missing peer review confirmation rejected
        with pytest.raises(approval_service.ConflictError, match="peer review"):
            approval_service.decide_approval(
                conn,
                approval_id=approval["approval_id"],
                reviewer_id="emp_priya",
                action="approve",
                matching_number=approval["matching_number"],
                peer_review_confirmed=False,
            )

        # Simulate 2-minute expiry
        past = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
        conn.execute(
            "UPDATE approval_records SET expires_at = ? WHERE approval_id = ?",
            (past, approval["approval_id"]),
        )
        with pytest.raises(approval_service.ConflictError, match="expired"):
            approval_service.decide_approval(
                conn,
                approval_id=approval["approval_id"],
                reviewer_id="emp_priya",
                action="approve",
                matching_number=approval["matching_number"],
                notes="Called Rahul back on ext 4102",
                peer_review_confirmed=True,
            )

        # Reissue fresh 2-min approval and complete peer review + execution
        fresh = approval_service.issue_approval_record(conn, request_id=req["request_id"])
        approved = approval_service.decide_approval(
            conn,
            approval_id=fresh["approval_id"],
            reviewer_id="emp_carol",
            action="approve",
            matching_number=fresh["matching_number"],
            notes="Verified Rahul on ext 4102 via directory callback.",
            peer_review_confirmed=True,
        )
        assert approved["status"] == "APPROVED"

        receipt = approval_service.execute_request(
            conn, request_id=req["request_id"], actor_id="emp_rahul"
        )
        assert receipt["status"] == "EXECUTED"


def test_audit_chain_detects_tampering(isolated_env: Path) -> None:
    with get_db(isolated_env) as conn:
        approval_service.create_request(
            conn,
            employee_id="emp_rahul",
            action_summary="Emergency firewall rule",
            target_resource="edge-fw-01",
            justification="Mitigate DDoS incident",
        )
        assert audit_service.verify_chain(conn)["valid"] is True

        conn.execute("UPDATE audit_events SET actor_id = 'emp_attacker' WHERE id = 1")
        tampered = audit_service.verify_chain(conn)
        assert tampered["valid"] is False
        assert tampered["broken_at_id"] == 1


def test_fastapi_full_workflow_and_reviewer_token(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    monkeypatch.setenv("REVIEWER_API_TOKEN", "secret-reviewer-token")
    client = TestClient(create_app())

    assert client.get("/health").json() == {"status": "ok"}

    created = client.post(
        "/api/v1/requests",
        json={
            "employee_id": "emp_rahul",
            "action_summary": "Deploy build 142 to production",
            "target_resource": "prod-api",
            "justification": "Emergency outage fix for SEV-1",
        },
    )
    assert created.status_code == 201
    req_id = created.json()["request_id"]

    verified = client.post(
        f"/api/v1/requests/{req_id}/verify-voice",
        files={"file": ("voice.wav", b"RIFFdemo", "audio/wav")},
    )
    assert verified.status_code == 200
    body = verified.json()
    assert body["status"] == "PENDING_APPROVAL"
    approval_id = body["active_approval"]["approval_id"]
    matching_number = body["active_approval"]["matching_number"]

    # Approver phone queue shows pending approval
    pending = client.get("/api/v1/approvals/pending").json()
    assert any(item["approval_id"] == approval_id for item in pending)

    # Missing Bearer token rejected
    unauth = client.post(
        f"/api/v1/approvals/{approval_id}/decide",
        json={
            "reviewer_id": "emp_priya",
            "action": "approve",
            "matching_number": matching_number,
            "notes": "Confirmed SEV-1",
        },
    )
    assert unauth.status_code == 401

    # Valid Bearer token + matching number approves
    ok = client.post(
        f"/api/v1/approvals/{approval_id}/decide",
        headers={"Authorization": "Bearer secret-reviewer-token"},
        json={
            "reviewer_id": "emp_priya",
            "action": "approve",
            "matching_number": matching_number,
            "notes": "Confirmed SEV-1",
        },
    )
    assert ok.status_code == 200
    assert ok.json()["status"] == "APPROVED"

    # Simulated execution succeeds once
    exec_res = client.post(
        f"/api/v1/requests/{req_id}/execute",
        json={"actor_id": "emp_rahul"},
    )
    assert exec_res.status_code == 200
    assert exec_res.json()["status"] == "EXECUTED"

    # Audit chain valid
    chain = client.get("/api/v1/audit/verify").json()
    assert chain["valid"] is True
