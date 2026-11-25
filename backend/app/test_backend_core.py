"""Pytest suite for Member A backend core, approval workflow, audit chain, and FastAPI routes."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest

try:
    from backend.app.approval import service as approval_service
    from backend.app.audit import service as audit_service
    from backend.app.core.database import get_db, init_db
    from backend.app.core.storage import UploadValidationError, save_audio_upload
    from backend.app.main import create_app
except ModuleNotFoundError:
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
    monkeypatch.delenv("REVIEWER_API_TOKEN", raising=False)
    init_db(db_path, seed_sample_employees=True)
    return db_path


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


def test_end_to_end_pass_workflow_and_dual_control(
    isolated_env: Path, tmp_path: Path
) -> None:
    wav = tmp_path / "voice.wav"
    wav.write_bytes(b"RIFFdemo")

    with get_db(isolated_env) as conn:
        req = approval_service.create_request(
            conn,
            employee_id="emp_alice",
            action_summary="Restart prod billing DB",
            target_resource="prod-db-01",
            justification="INC-402 checkout outage",
        )
        assert req["status"] == "PENDING_VOICE"
        assert len(req["challenge_phrase"].split()) >= 15

        # Cannot approve before voice check
        with pytest.raises(approval_service.ConflictError, match="before voice verification"):
            approval_service.decide_request(
                conn,
                request_id=req["request_id"],
                reviewer_id="emp_bob",
                action="approve",
            )

        # Voice check in fixture pass mode -> PENDING_APPROVAL
        verified = approval_service.submit_voice_verification(
            conn,
            request_id=req["request_id"],
            audio_path=str(wav),
        )
        assert verified["status"] == "PENDING_APPROVAL"
        assert verified["voice_result"]["risk"] == "PASS"

        # Dual control: requester cannot approve own request
        with pytest.raises(approval_service.ForbiddenError):
            approval_service.decide_request(
                conn,
                request_id=req["request_id"],
                reviewer_id="emp_alice",
                action="approve",
            )

        # Authorized reviewer approves
        approved = approval_service.decide_request(
            conn,
            request_id=req["request_id"],
            reviewer_id="emp_bob",
            action="approve",
            notes="Verified incident ticket INC-402.",
        )
        assert approved["status"] == "APPROVED"
        assert approved["reviewer_id"] == "emp_bob"

        chain = audit_service.verify_chain(conn)
        assert chain["valid"] is True
        assert chain["checked_events"] >= 3


def test_fail_and_borderline_require_callback_before_approval(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    wav = tmp_path / "voice.wav"
    wav.write_bytes(b"RIFFdemo")
    monkeypatch.setenv("FIXTURE_SCENARIO", "fail")

    with get_db(isolated_env) as conn:
        req = approval_service.create_request(
            conn,
            employee_id="emp_alice",
            action_summary="Rotate KMS master key",
            target_resource="kms-prod",
            justification="Emergency key rotation",
        )
        checked = approval_service.submit_voice_verification(
            conn,
            request_id=req["request_id"],
            audio_path=str(wav),
        )
        assert checked["status"] == "HIGH_RISK_HOLD"
        assert checked["voice_result"]["risk"] == "FAIL"

        # Blocked without callback_verified + notes
        with pytest.raises(approval_service.ConflictError, match="callback verification"):
            approval_service.decide_request(
                conn,
                request_id=req["request_id"],
                reviewer_id="emp_bob",
                action="approve",
                notes="Looks ok",
                callback_verified=False,
            )

        # Allowed once known-number callback is documented
        approved = approval_service.decide_request(
            conn,
            request_id=req["request_id"],
            reviewer_id="emp_bob",
            action="approve",
            notes="Called Alice back on Corp Directory Ext. 4102 and confirmed.",
            callback_verified=True,
        )
        assert approved["status"] == "APPROVED"
        assert approved["callback_verified"] is True


def test_audit_chain_detects_tampering(isolated_env: Path) -> None:
    with get_db(isolated_env) as conn:
        approval_service.create_request(
            conn,
            employee_id="emp_alice",
            action_summary="Emergency firewall rule",
            target_resource="edge-fw-01",
            justification="Mitigate DDoS incident",
        )
        assert audit_service.verify_chain(conn)["valid"] is True

        # Simulate DB tampering
        conn.execute("UPDATE audit_events SET actor_id = 'emp_attacker' WHERE id = 1")
        tampered = audit_service.verify_chain(conn)
        assert tampered["valid"] is False
        assert tampered["broken_at_id"] == 1


def test_fastapi_endpoints_and_reviewer_token(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    monkeypatch.setenv("REVIEWER_API_TOKEN", "secret-reviewer-token")
    client = TestClient(create_app())

    assert client.get("/health").json() == {"status": "ok"}
    assert len(client.get("/api/v1/employees").json()) >= 3

    created = client.post(
        "/api/v1/requests",
        json={
            "employee_id": "emp_alice",
            "action_summary": "Grant temporary admin",
            "target_resource": "prod-k8s",
            "justification": "Emergency pod recovery for SEV-1",
        },
    )
    assert created.status_code == 201
    req_id = created.json()["request_id"]

    verified = client.post(
        f"/api/v1/requests/{req_id}/verify-voice",
        files={"file": ("voice.wav", b"RIFFdemo", "audio/wav")},
    )
    assert verified.status_code == 200
    assert verified.json()["status"] == "PENDING_APPROVAL"
    assert set(verified.json()["voice_result"].keys()) == {
        "aletheia",
        "eidos",
        "risk",
        "reason",
    }

    # Missing Bearer token rejected
    unauth = client.post(
        f"/api/v1/requests/{req_id}/decision",
        json={"reviewer_id": "emp_bob", "action": "approve", "notes": "ok"},
    )
    assert unauth.status_code == 401

    # Valid Bearer token succeeds
    ok = client.post(
        f"/api/v1/requests/{req_id}/decision",
        headers={"Authorization": "Bearer secret-reviewer-token"},
        json={"reviewer_id": "emp_bob", "action": "approve", "notes": "Confirmed SEV-1"},
    )
    assert ok.status_code == 200
    assert ok.json()["status"] == "APPROVED"

    chain = client.get("/api/v1/audit/verify")
    assert chain.status_code == 200
    assert chain.json()["valid"] is True
