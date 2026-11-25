"""Pytest suite for fail-closed bot audit chain integration and approver-only /report."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

try:
    from backend.app.approval.service import create_request, get_request, submit_voice_verification
    from backend.app.audit.service import list_events, verify_chain
    from backend.app.bot import approver_console as ac_mod
    from backend.app.bot import audit_bridge
    from backend.app.bot import bot as bot_mod
    from backend.app.bot.approver_console import ApproverConsole, CallbackSigner
    from backend.app.core.database import get_db, init_db
except ModuleNotFoundError:
    from app.approval.service import create_request, get_request, submit_voice_verification  # type: ignore[no-redef]
    from app.audit.service import list_events, verify_chain  # type: ignore[no-redef]
    from app.bot import approver_console as ac_mod  # type: ignore[no-redef]
    from app.bot import audit_bridge  # type: ignore[no-redef]
    from app.bot import bot as bot_mod  # type: ignore[no-redef]
    from app.bot.approver_console import ApproverConsole, CallbackSigner  # type: ignore[no-redef]
    from app.core.database import get_db, init_db  # type: ignore[no-redef]


class _FakeBot:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.edited: list[dict[str, Any]] = []
        self._next_id = 500

    async def send_message(self, *, chat_id: int, text: str, reply_markup: Any = None) -> Any:
        self._next_id += 1
        self.sent.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup, "message_id": self._next_id})
        return SimpleNamespace(message_id=self._next_id)

    async def edit_message_text(
        self,
        *,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: Any = None,
    ) -> None:
        self.edited.append(
            {"chat_id": chat_id, "message_id": message_id, "text": text, "reply_markup": reply_markup}
        )


class _FakeMessage:
    def __init__(self) -> None:
        self.replies: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)


class _FakeQuery:
    def __init__(self, *, data: str, chat_id: int, message_id: int) -> None:
        self.data = data
        self.message = SimpleNamespace(chat_id=chat_id, message_id=message_id)
        self.answers: list[tuple[str | None, bool]] = []

    async def answer(self, text: str | None = None, show_alert: bool = False) -> None:
        self.answers.append((text, show_alert))


def test_sanitize_details_strips_path_like_keys() -> None:
    clean = audit_bridge._sanitize_details(
        {
            "channel": "telegram",
            "file": "/tmp/raw.ogg",
            "file_id": "telegram-file-id",
            "file_path": "/tmp/raw.ogg",
            "audio_path": "/tmp/raw.wav",
            "wav_path": "/tmp/normalized.wav",
            "path": "/tmp/voicegate_enroll_1",
            "tmp_path": "/tmp/voicegate_enroll_2",
            "note": "safe detail",
        }
    )
    assert clean == {"channel": "telegram", "note": "safe detail"}


def test_all_required_audit_events_chain_and_strip_sensitive_fields(tmp_path: Path) -> None:
    db_path = tmp_path / "audit.db"
    init_db(db_path)

    audit_bridge.log_enrollment_consent(actor_id="emp_rahul", employee_id="emp_rahul", db_path=db_path)
    audit_bridge.log_enrollment_sample(
        actor_id="emp_rahul", employee_id="emp_rahul", accepted=True, reason="valid sample", db_path=db_path
    )
    audit_bridge.log_enrollment_sample(
        actor_id="emp_rahul", employee_id="emp_rahul", accepted=False, reason="too short", db_path=db_path
    )
    audit_bridge.log_request_created(
        actor_id="emp_rahul", request_id="bg_demo0001", action_summary="Deploy build 142", db_path=db_path
    )
    audit_bridge.log_voice_check_result(
        actor_id="tg:100",
        risk="FAIL",
        reason="Synthetic voice signs detected. This is evidence, not proof.",
        chat_type="private",
        ai_mode="fixture",
        db_path=db_path,
    )
    audit_bridge.log_approval_shown(
        actor_id="emp_priya", request_id="bg_demo0001", approval_ref="apr_1", risk="PASS", db_path=db_path
    )
    audit_bridge.log_approval_decision(
        actor_id="emp_priya", request_id="bg_demo0001", approval_id="apr_1", decision="approve", db_path=db_path
    )
    audit_bridge.log_block(actor_id="emp_priya", request_id="bg_demo0002", db_path=db_path)
    audit_bridge.log_expiry(actor_id="emp_priya", request_id="bg_demo0003", approval_id="apr_3", db_path=db_path)

    with get_db(db_path) as conn:
        check = verify_chain(conn)
        assert check["valid"] is True
        assert check["checked_events"] == 9
        events = list_events(conn, limit=20)
        types = {e["event_type"] for e in events}
        assert {
            "ENROLLMENT_CONSENT",
            "ENROLLMENT_SAMPLE_ACCEPTED",
            "ENROLLMENT_SAMPLE_REJECTED",
            "REQUEST_CREATED",
            "VOICE_CHECK_RESULT",
            "APPROVAL_SHOWN",
            "APPROVAL_DECISION",
            "FAIL_BLOCK_CONFIRMED",
            "APPROVAL_EXPIRED",
        }.issubset(types)

        vc = next(e for e in events if e["event_type"] == "VOICE_CHECK_RESULT")
        assert vc["details"]["risk"] == "FAIL"
        assert "Synthetic voice" in vc["details"]["reason"]
        # Never store raw audio, tokens, or scores in voice check audit details.
        for forbidden in ("score", "similarity", "audio", "token", "verdict"):
            assert forbidden not in vc["details"]


def test_bot_voice_check_audit_strips_scores(tmp_path: Path) -> None:
    db_path = tmp_path / "vc.db"
    init_db(db_path)
    bot_mod._record_audit_hook(
        user_id=77,
        chat_type="private",
        analysis={
            "aletheia": {"verdict": "fake", "score": 0.93, "duration_seconds": 6.0, "operation_id": "op_1"},
            "risk": "FAIL",
            "reason": "Synthetic voice detected. This is evidence, not proof.",
        },
        ai_mode="fixture",
        db_path=db_path,
    )
    with get_db(db_path) as conn:
        events = list_events(conn, limit=5)
        assert len(events) == 1
        assert events[0]["event_type"] == "VOICE_CHECK_RESULT"
        assert "score" not in events[0]["details"]
        assert "0.93" not in str(events[0]["details"])


def test_fail_closed_when_audit_logging_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "failclosed.db"
    init_db(db_path)
    with get_db(db_path) as conn:
        req = create_request(
            conn,
            employee_id="emp_rahul",
            action_summary="Deploy hotfix 142",
            target_resource="prod-api",
            justification="SEV-1 outage mitigation",
        )
        req = submit_voice_verification(
            conn,
            request_id=req["request_id"],
            audio_path="dummy.wav",
            voice_verifier=lambda _p, _e: {
                "aletheia": {"verdict": "real", "score": 0.08, "duration_seconds": 5.0, "operation_id": "op_1"},
                "eidos": {"decision": "match", "similarity": 0.91, "duration_seconds": 5.0, "operation_id": "op_2"},
                "risk": "PASS",
                "reason": "Matched speaker.",
            },
        )

    signer = CallbackSigner(secret_key=b"test-signing-key-32-bytes-minimum")
    console = ApproverConsole(db_path=db_path, signer=signer)
    console.approver_chats[20] = "emp_priya"
    bot = _FakeBot()

    def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise audit_bridge.AuditLoggingError("disk full")

    # 1. If APPROVAL_SHOWN audit fails, card MUST NOT be sent.
    monkeypatch.setattr(ac_mod, "log_approval_shown", _boom)
    asyncio.run(console.dispatch_pending_cards(bot))
    assert [m for m in bot.sent if m["chat_id"] == 20] == []

    # 2. If APPROVAL_DECISION audit fails, approval MUST roll back and NOT execute.
    monkeypatch.setattr(ac_mod, "log_approval_decision", _boom)
    cb = signer.sign(
        op="a",
        request_id=req["request_id"],
        approval_id=req["active_approval"]["approval_id"],
        value=req["active_approval"]["matching_number"],
        chat_id=20,
    )
    q = _FakeQuery(data=cb, chat_id=20, message_id=999)
    asyncio.run(console.handle_callback_query(SimpleNamespace(callback_query=q), SimpleNamespace(bot=bot)))
    assert "audit logging failed" in (q.answers[0][0] or "").lower()
    assert bot.edited == []
    with get_db(db_path) as conn:
        assert get_request(conn, req["request_id"])["status"] == "PENDING_APPROVAL"

    # 3. If APPROVAL_EXPIRED audit fails, expire_card MUST NOT edit the card.
    monkeypatch.setattr(ac_mod, "log_expiry", _boom)
    asyncio.run(
        console.expire_card(
            bot=bot,
            chat_id=20,
            message_id=999,
            request_id=req["request_id"],
            approval_id=req["active_approval"]["approval_id"],
            record_expiry_audit=True,
        )
    )
    assert bot.edited == []


def test_report_command_is_approver_only_and_detects_tampering(tmp_path: Path) -> None:
    db_path = tmp_path / "report.db"
    init_db(db_path)
    audit_bridge.log_enrollment_consent(actor_id="emp_rahul", employee_id="emp_rahul", db_path=db_path)
    audit_bridge.log_voice_check_result(
        actor_id="tg:55",
        risk="PASS",
        reason="Real voice.",
        chat_type="private",
        ai_mode="fixture",
        db_path=db_path,
    )

    console = ApproverConsole(db_path=db_path)
    console.approver_chats[10] = "emp_rahul"  # role=requester (must be denied /report!)
    console.approver_chats[20] = "emp_priya"  # role=approver (allowed)

    # Unregistered chat denied.
    m_unreg = _FakeMessage()
    asyncio.run(
        console.handle_report_command(
            SimpleNamespace(effective_message=m_unreg, effective_chat=SimpleNamespace(id=999)),
            SimpleNamespace(),
        )
    )
    assert "access denied" in m_unreg.replies[0].lower()

    # Requester role denied.
    m_req = _FakeMessage()
    asyncio.run(
        console.handle_report_command(
            SimpleNamespace(effective_message=m_req, effective_chat=SimpleNamespace(id=10)),
            SimpleNamespace(),
        )
    )
    assert "access denied" in m_req.replies[0].lower()

    # Approver receives valid chain report with event count, timestamp, and plain-language events.
    m_apr = _FakeMessage()
    asyncio.run(
        console.handle_report_command(
            SimpleNamespace(effective_message=m_apr, effective_chat=SimpleNamespace(id=20)),
            SimpleNamespace(),
        )
    )
    report_ok = m_apr.replies[0]
    assert "VALID" in report_ok
    assert "Total events in chain: 2" in report_ok
    assert "Checked at:" in report_ok
    assert "Voice check completed (risk: PASS)" in report_ok

    # Tamper with event #1 directly in SQLite and verify /report flags BROKEN + exact event #1.
    with get_db(db_path) as conn:
        conn.execute("UPDATE audit_events SET actor_id = 'tampered_actor' WHERE id = 1")

    m_broken = _FakeMessage()
    asyncio.run(
        console.handle_report_command(
            SimpleNamespace(effective_message=m_broken, effective_chat=SimpleNamespace(id=20)),
            SimpleNamespace(),
        )
    )
    report_broken = m_broken.replies[0]
    assert "BROKEN" in report_broken
    assert "#1" in report_broken
