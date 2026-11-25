"""Unit tests for Live Call Guard service and localhost Telegram internal bridge."""

from __future__ import annotations

import asyncio
import io
import json
import wave
from pathlib import Path
from typing import Any

import pytest

from app.approval.service import (
    ConflictError,
    create_request,
    get_request,
    issue_approval_record,
    submit_voice_verification,
)
from app.audit.service import list_events, verify_chain
from app.bot.audio import AudioConversionError
from app.bot.internal_bridge import BotInternalBridge
from app.bot.live_call_alerts import (
    LiveCallAlertManager,
    LiveCallCallbackSigner,
    register_live_call_manager,
)
from app.core.config import CoreConfig
from app.core.database import get_db, init_db
from app.core.storage import UploadValidationError
from app.live.service import LiveGuardAuditError, LiveGuardError, LiveGuardService


def _make_wav_bytes(seconds: float = 1.0, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


def _fake_wav_converter(
    input_path: Path,
    output_path: Path,
    *,
    min_duration: float = 0.5,
    max_duration: float = 30.0,
) -> float:
    output_path.write_bytes(_make_wav_bytes(2.0))
    return 2.0


def _test_config(tmp_path: Path, *, max_chunks: int = 12, max_chunk_bytes: int = 1024 * 1024) -> CoreConfig:
    return CoreConfig(
        database_path=tmp_path / "live_guard.db",
        upload_dir=tmp_path / "uploads",
        cors_origins=("http://localhost:3000",),
        challenge_ttl_seconds=300,
        approval_ttl_seconds=120,
        max_upload_bytes=5 * 1024 * 1024,
        reviewer_api_token=None,
        live_max_chunks=max_chunks,
        live_max_chunk_bytes=max_chunk_bytes,
        live_fixture_scenario=None,
        bot_internal_port=8765,
        bot_internal_token=None,
    )


def _seed_request(db_path: Path) -> str:
    init_db(db_path)
    with get_db(db_path) as conn:
        req = create_request(
            conn,
            employee_id="emp_rahul",
            action_summary="Failover production payments cluster",
            target_resource="payments-db-primary",
            justification="Emergency primary failover",
        )
        # Issue a pending approval first so we can verify FAIL blocks and expires it.
        submit_voice_verification(
            conn,
            request_id=req["request_id"],
            audio_path="unused.wav",
            voice_verifier=lambda _p, _e: {
                "aletheia": {"verdict": "real", "score": 0.1},
                "eidos": {"decision": "match", "similarity": 0.9},
                "risk": "PASS",
                "reason": "Initial voice pass.",
            },
        )
        return req["request_id"]


def test_state_machine_pass_streak_borderline_reset_fail_latch_and_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_MODE", "live")
    monkeypatch.setenv("DETECTIF_API_KEY", "test-placeholder-never-used")
    cfg = _test_config(tmp_path, max_chunks=12)
    req_id = _seed_request(cfg.database_path)

    outcomes = iter(
        [
            {"aletheia": None, "eidos": None, "risk": "PASS", "reason": "Chunk 1 clean."},
            {"aletheia": None, "eidos": None, "risk": "PASS", "reason": "Chunk 2 clean."},
            {"aletheia": None, "eidos": None, "risk": "BORDERLINE", "reason": "Chunk 3 noisy."},
            {"aletheia": None, "eidos": None, "risk": "PASS", "reason": "Chunk 4 clean again."},
            {"aletheia": None, "eidos": None, "risk": "FAIL", "reason": "Chunk 5 synthetic speech."},
            {"aletheia": None, "eidos": None, "risk": "PASS", "reason": "Chunk 6 cannot clear latch."},
        ]
    )
    tg_calls: list[tuple[str, str]] = []

    def _verifier(_path: str, _emp: str) -> dict[str, Any]:
        return next(outcomes)

    def _tg(rid: str, state: str, _res: dict[str, Any] | None, _cfg: CoreConfig) -> bool:
        tg_calls.append((rid, state))
        return True

    svc = LiveGuardService(
        db_path=cfg.database_path,
        config_loader=lambda: cfg,
        voice_verifier=_verifier,
        wav_converter=_fake_wav_converter,
        telegram_notifier=_tg,
    )

    # Consent required.
    with pytest.raises(LiveGuardError):
        svc.start_session(request_id=req_id, employee_id="emp_rahul", consent_confirmed=False)

    s0 = svc.start_session(request_id=req_id, employee_id="emp_rahul", consent_confirmed=True)
    assert s0["state"] == "MONITORING"
    assert s0["active"] is True
    assert tg_calls == [(req_id, "MONITORING")]

    # Only ONE active session per request allowed.
    with pytest.raises(ConflictError):
        svc.start_session(request_id=req_id, employee_id="emp_rahul", consent_confirmed=True)

    # Chunk 1 PASS -> still MONITORING (needs 2 consecutive PASS).
    c1 = svc.process_chunk(
        request_id=req_id,
        employee_id="emp_rahul",
        filename="c1.webm",
        content_type="audio/webm",
        stream=io.BytesIO(b"fake-webm-1"),
    )
    assert c1["session"]["state"] == "MONITORING"
    assert c1["session"]["pass_streak"] == 1
    assert len(tg_calls) == 1  # No Telegram spam when state didn't change

    # Chunk 2 PASS -> transitions to PASS!
    c2 = svc.process_chunk(
        request_id=req_id,
        employee_id="emp_rahul",
        filename="c2.webm",
        content_type="audio/webm",
        stream=io.BytesIO(b"fake-webm-2"),
    )
    assert c2["session"]["state"] == "PASS"
    assert c2["session"]["pass_streak"] == 2
    assert tg_calls[-1] == (req_id, "PASS")

    # Chunk 3 BORDERLINE -> resets streak and sets BORDERLINE.
    c3 = svc.process_chunk(
        request_id=req_id,
        employee_id="emp_rahul",
        filename="c3.webm",
        content_type="audio/webm",
        stream=io.BytesIO(b"fake-webm-3"),
    )
    assert c3["session"]["state"] == "BORDERLINE"
    assert c3["session"]["pass_streak"] == 0
    assert tg_calls[-1] == (req_id, "BORDERLINE")

    # Chunk 4 PASS -> streak 1, still BORDERLINE (needs 2 consecutive PASS).
    c4 = svc.process_chunk(
        request_id=req_id,
        employee_id="emp_rahul",
        filename="c4.webm",
        content_type="audio/webm",
        stream=io.BytesIO(b"fake-webm-4"),
    )
    assert c4["session"]["state"] == "BORDERLINE"
    assert c4["session"]["pass_streak"] == 1

    # Chunk 5 FAIL -> latches FAIL, blocks break-glass request, notifies Telegram!
    c5 = svc.process_chunk(
        request_id=req_id,
        employee_id="emp_rahul",
        filename="c5.webm",
        content_type="audio/webm",
        stream=io.BytesIO(b"fake-webm-5"),
    )
    assert c5["session"]["state"] == "FAIL"
    assert c5["session"]["fail_latched"] is True
    assert c5["session"]["request_status"] == "BLOCKED"
    assert tg_calls[-1] == (req_id, "FAIL")

    # Hard FAIL blocked the request permanently; cannot issue a new approval.
    with get_db(cfg.database_path) as conn:
        req_after = get_request(conn, req_id)
        assert req_after["status"] == "BLOCKED"
        assert req_after["voice_result"]["risk"] == "FAIL"
        assert req_after["active_approval"]["status"] == "EXPIRED"
        with pytest.raises(ConflictError):
            issue_approval_record(conn, request_id=req_id)

    # Chunk 6 PASS -> FAIL stays latched!
    c6 = svc.process_chunk(
        request_id=req_id,
        employee_id="emp_rahul",
        filename="c6.webm",
        content_type="audio/webm",
        stream=io.BytesIO(b"fake-webm-6"),
    )
    assert c6["session"]["state"] == "FAIL"
    assert c6["session"]["fail_latched"] is True
    # No second FAIL notification sent.
    assert tg_calls.count((req_id, "FAIL")) == 1

    # Stop session -> subsequent chunks are ignored cleanly.
    stopped = svc.stop_session(request_id=req_id)
    assert stopped["active"] is False
    ignored = svc.process_chunk(
        request_id=req_id,
        employee_id="emp_rahul",
        filename="c7.webm",
        content_type="audio/webm",
        stream=io.BytesIO(b"fake-webm-7"),
    )
    assert ignored["ignored"] is True

    # Temporary upload directory must be completely empty (all audio deleted in finally).
    assert list(cfg.upload_dir.iterdir()) == []

    # Audit trail must be valid and contain all required events without scores or raw audio.
    with get_db(cfg.database_path) as conn:
        assert verify_chain(conn)["valid"] is True
        events = list_events(conn, request_id=req_id, limit=100)
        types = {e["event_type"] for e in events}
        assert {
            "LIVE_GUARD_SESSION_STARTED",
            "LIVE_GUARD_CHUNK_RESULT",
            "LIVE_GUARD_STATE_CHANGED",
            "LIVE_GUARD_FAIL_ALERT_SENT",
            "LIVE_GUARD_SESSION_ENDED",
        }.issubset(types)
        chunk_events = [e for e in events if e["event_type"] == "LIVE_GUARD_CHUNK_RESULT"]
        assert len(chunk_events) == 6
        for ev in chunk_events:
            assert set(ev["details"].keys()) == {"chunk_index", "risk", "reason"}


def test_chunk_cap_errors_non_audio_rejection_and_fixture_fail_demo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_MODE", "fixture")
    monkeypatch.setenv("FIXTURE_SCENARIO", "pass")
    cfg = _test_config(tmp_path, max_chunks=2, max_chunk_bytes=256)
    req_id = _seed_request(cfg.database_path)

    svc = LiveGuardService(
        db_path=cfg.database_path,
        config_loader=lambda: cfg,
        wav_converter=_fake_wav_converter,
        telegram_notifier=lambda *a: False,
    )
    svc.start_session(request_id=req_id, employee_id="emp_rahul", consent_confirmed=True)

    # Non-audio MIME type rejected.
    with pytest.raises(UploadValidationError):
        svc.process_chunk(
            request_id=req_id,
            employee_id="emp_rahul",
            filename="bad.webm",
            content_type="text/plain",
            stream=io.BytesIO(b"not audio"),
        )

    # Non-audio extension rejected.
    with pytest.raises(UploadValidationError):
        svc.process_chunk(
            request_id=req_id,
            employee_id="emp_rahul",
            filename="evil.exe",
            content_type="audio/webm",
            stream=io.BytesIO(b"1234"),
        )

    # Oversized chunk rejected.
    with pytest.raises(UploadValidationError):
        svc.process_chunk(
            request_id=req_id,
            employee_id="emp_rahul",
            filename="big.webm",
            content_type="audio/webm",
            stream=io.BytesIO(b"0" * 1024),
        )

    # Chunk 1: ffmpeg error counts as BORDERLINE (fail closed), never PASS, and cleans up files.
    def _failing_ffmpeg(*_a: Any, **_k: Any) -> float:
        raise AudioConversionError("Audio converter (ffmpeg) is not installed on the server.")

    svc_err = LiveGuardService(
        db_path=cfg.database_path,
        config_loader=lambda: cfg,
        wav_converter=_failing_ffmpeg,
        telegram_notifier=lambda *a: False,
    )
    svc_err._sessions = svc._sessions
    c1 = svc_err.process_chunk(
        request_id=req_id,
        employee_id="emp_rahul",
        filename="c1.webm",
        content_type="audio/webm",
        stream=io.BytesIO(b"small"),
    )
    assert c1["chunk"]["risk"] == "BORDERLINE"
    assert c1["chunk"]["error"] is True
    assert c1["session"]["state"] == "BORDERLINE"

    # Chunk 2: fixture_scenario="fail" works in AI_MODE=fixture without network calls & hits cap=2!
    c2 = svc.process_chunk(
        request_id=req_id,
        employee_id="emp_rahul",
        filename="c2.webm",
        content_type="audio/webm",
        stream=io.BytesIO(b"small"),
        fixture_scenario="fail",
    )
    assert c2["chunk"]["risk"] == "FAIL"
    assert c2["session"]["state"] == "FAIL"
    assert c2["session"]["active"] is False  # Cap of 2 chunks reached!

    # Chunk 3 after cap is ignored.
    c3 = svc.process_chunk(
        request_id=req_id,
        employee_id="emp_rahul",
        filename="c3.webm",
        content_type="audio/webm",
        stream=io.BytesIO(b"small"),
    )
    assert c3["ignored"] is True
    assert list(cfg.upload_dir.iterdir()) == []


def test_audit_failure_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _test_config(tmp_path)
    req_id = _seed_request(cfg.database_path)

    svc = LiveGuardService(
        db_path=cfg.database_path,
        config_loader=lambda: cfg,
        wav_converter=_fake_wav_converter,
        telegram_notifier=lambda *a: False,
    )

    monkeypatch.setattr(
        "app.live.service.record_event",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("disk full")),
    )
    with pytest.raises(LiveGuardAuditError):
        svc.start_session(request_id=req_id, employee_id="emp_rahul", consent_confirmed=True)


@pytest.mark.asyncio
async def test_bot_internal_bridge_auth_and_dispatch(tmp_path: Path) -> None:
    db_path = tmp_path / "bridge.db"
    req_id = _seed_request(db_path)

    sent: list[dict[str, Any]] = []

    class _Bot:
        async def send_message(self, *, chat_id: int, text: str, reply_markup: Any = None) -> Any:
            sent.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})
            return type("Msg", (), {"message_id": len(sent)})()

        async def edit_message_text(self, **_kw: Any) -> None:
            pass

    manager = LiveCallAlertManager(
        approver_chats_ref={555: "emp_priya"},
        db_path=db_path,
        bot=_Bot(),
        signer=LiveCallCallbackSigner(b"bridge-test-key"),
        min_edit_interval=0.0,
    )
    register_live_call_manager(manager)

    bridge = BotInternalBridge(host="127.0.0.1", port=0, token="secret-bridge-token")
    started = await bridge.start()
    assert started is True

    async def _raw_post(auth_header: str | None, body_obj: dict[str, Any]) -> int:
        reader, writer = await asyncio.open_connection("127.0.0.1", bridge.port)
        body = json.dumps(body_obj).encode("utf-8")
        hdrs = [
            "POST /internal/live-state HTTP/1.1",
            "Host: 127.0.0.1",
            "Content-Type: application/json",
            f"Content-Length: {len(body)}",
        ]
        if auth_header is not None:
            hdrs.append(f"Authorization: {auth_header}")
        raw = ("\r\n".join(hdrs) + "\r\n\r\n").encode("ascii") + body
        writer.write(raw)
        await writer.drain()
        resp = await reader.read()
        writer.close()
        await writer.wait_closed()
        status_line = resp.split(b"\r\n", 1)[0].decode("ascii")
        return int(status_line.split(" ")[1])

    try:
        assert await _raw_post(None, {"request_id": req_id, "state": "MONITORING"}) == 401
        assert await _raw_post("Bearer wrong", {"request_id": req_id, "state": "MONITORING"}) == 403
        assert (
            await _raw_post(
                "Bearer secret-bridge-token",
                {"request_id": req_id, "state": "FAIL", "last_result": {"reason": "Deepfake alert"}},
            )
            == 200
        )
        # Expect 1 status message + 1 separate FAIL alert message with 2 buttons.
        assert len(sent) == 2
        assert sent[1]["reply_markup"] is not None
    finally:
        await bridge.stop()
        await manager.stop()
