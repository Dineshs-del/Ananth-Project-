"""Unit tests for Telegram live-call monitoring alerts (backend/app/bot/live_call_alerts.py)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from telegram.error import BadRequest, RetryAfter

from app.approval.service import (
    ConflictError,
    create_request,
    get_request,
    issue_approval_record,
    submit_voice_verification,
)
from app.audit.service import list_events, verify_chain
from app.bot.live_call_alerts import (
    LiveCallAlertManager,
    LiveCallCallbackSigner,
    notify_live_state,
    register_live_call_manager,
)
from app.core.database import get_db, init_db


class _FakeBot:
    def __init__(self) -> None:
        self._next_id = 100
        self.sent: list[dict[str, Any]] = []
        self.edited: list[dict[str, Any]] = []
        self.edit_errors: list[Exception] = []

    async def send_message(
        self,
        *,
        chat_id: int,
        text: str,
        reply_markup: Any = None,
    ) -> SimpleNamespace:
        self._next_id += 1
        msg = SimpleNamespace(message_id=self._next_id, chat_id=chat_id, text=text)
        self.sent.append(
            {
                "message_id": self._next_id,
                "chat_id": chat_id,
                "text": text,
                "reply_markup": reply_markup,
            }
        )
        return msg

    async def edit_message_text(
        self,
        *,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: Any = None,
    ) -> None:
        if self.edit_errors:
            raise self.edit_errors.pop(0)
        self.edited.append(
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "reply_markup": reply_markup,
            }
        )


class _FakeCallbackQuery:
    def __init__(self, *, data: str, chat_id: int, message_id: int) -> None:
        self.data = data
        self.message = SimpleNamespace(chat_id=chat_id, message_id=message_id)
        self.answers: list[tuple[str | None, bool]] = []

    async def answer(self, text: str | None = None, show_alert: bool = False) -> None:
        self.answers.append((text, show_alert))


def _seed_request(db_path: Path) -> str:
    init_db(db_path)
    with get_db(db_path) as conn:
        req = create_request(
            conn,
            employee_id="emp_rahul",
            action_summary="Rotate prod billing DB credentials",
            target_resource="prod-billing-primary",
            justification="Emergency credential exposure containment",
        )
        return req["request_id"]


@pytest.mark.asyncio
async def test_single_status_message_edits_in_place_and_latches_fail(tmp_path: Path) -> None:
    db_path = tmp_path / "live_call.db"
    req_id = _seed_request(db_path)
    bot = _FakeBot()
    approvers = {9001: "emp_priya"}
    manager = LiveCallAlertManager(
        approver_chats_ref=approvers,
        db_path=db_path,
        bot=bot,
        signer=LiveCallCallbackSigner(b"live-test-key"),
        min_edit_interval=0.0,
    )
    register_live_call_manager(manager)
    try:
        # 1. Start live call -> sends ONE status message.
        r1 = await notify_live_state(
            req_id,
            "MONITORING",
            {
                "reason": "Live call started.",
                "raw_audio": b"SECRET_RAW_PCM_BYTES",
                "audio_path": "/tmp/should_never_show.wav",
            },
        )
        assert r1["ok"] is True
        assert r1["effective_state"] == "MONITORING"
        assert len(bot.sent) == 1
        assert len(bot.edited) == 0
        status_msg_id = bot.sent[0]["message_id"]
        assert "MONITORING" in bot.sent[0]["text"]
        assert "Next step:" in bot.sent[0]["text"]
        assert "This is evidence, not proof." in bot.sent[0]["text"]
        assert "SECRET_RAW_PCM_BYTES" not in bot.sent[0]["text"]
        assert "should_never_show" not in bot.sent[0]["text"]

        # 2. Update to PASS -> edits SAME message, does NOT send a new message.
        r2 = await notify_live_state(
            req_id,
            "PASS",
            {
                "risk": "PASS",
                "reason": "Voice window passed checks.",
                "aletheia": {"verdict": "genuine", "score": 0.91},
                "eidos": {"decision": "match", "similarity": 0.87},
            },
        )
        assert r2["effective_state"] == "PASS"
        assert len(bot.sent) == 1
        assert len(bot.edited) == 1
        assert bot.edited[-1]["message_id"] == status_msg_id
        assert "PASS" in bot.edited[-1]["text"]
        assert "Next step:" in bot.edited[-1]["text"]

        # 3. Update to BORDERLINE -> edits SAME message, still NO new messages.
        r3 = await notify_live_state(
            req_id,
            "BORDERLINE",
            {"risk": "BORDERLINE", "reason": "Noisy line; borderline confidence."},
        )
        assert r3["effective_state"] == "BORDERLINE"
        assert len(bot.sent) == 1
        assert len(bot.edited) == 2
        assert "call the requester back on a number you already know" in bot.edited[-1]["text"].lower()

        # 4. First FAIL -> edits status message AND sends ONE separate alert with 2 buttons.
        r4 = await notify_live_state(
            req_id,
            "FAIL",
            {"risk": "FAIL", "reason": "Synthetic voice indicators detected during call."},
        )
        assert r4["effective_state"] == "FAIL"
        assert r4["fail_latched"] is True
        assert r4["fail_alerts_sent"] == 1
        assert len(bot.sent) == 2  # 1 status + 1 FAIL alert
        alert = bot.sent[1]
        assert "ALERT: LIVE-CALL VOICE CHECK FAILED" in alert["text"]
        assert "Next step:" in alert["text"]
        buttons = [btn for row in alert["reply_markup"].inline_keyboard for btn in row]
        labels = [btn.text for btn in buttons]
        assert labels == ["Block", "Call back on known number"]
        assert all(len(btn.callback_data.encode("utf-8")) <= 64 for btn in buttons)

        # 5. Later PASS or repeated FAIL MUST stay latched FAIL and NEVER send a 2nd alert.
        r5 = await notify_live_state(
            req_id,
            "PASS",
            {"risk": "PASS", "reason": "Later clean window should not clear latch."},
        )
        r6 = await notify_live_state(
            req_id,
            "FAIL",
            {"risk": "FAIL", "reason": "Second fail window."},
        )
        assert r5["effective_state"] == "FAIL"
        assert r5["fail_latched"] is True
        assert r5["fail_alerts_sent"] == 0
        assert r6["fail_alerts_sent"] == 0
        assert len(bot.sent) == 2  # Still only ONE separate FAIL alert!
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_fail_alert_block_and_callback_buttons_enforce_approval_service(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "live_block.db"
    req_id = _seed_request(db_path)

    # Simulate an existing PASS approval window before live-call FAIL triggers.
    with get_db(db_path) as conn:
        submit_voice_verification(
            conn,
            request_id=req_id,
            audio_path="unused.wav",
            voice_verifier=lambda _p, _e: {
                "aletheia": {"verdict": "genuine", "score": 0.9},
                "eidos": {"decision": "match", "similarity": 0.88},
                "risk": "PASS",
                "reason": "Initial check passed.",
            },
        )

    bot = _FakeBot()
    approvers = {9001: "emp_priya", 9002: "emp_rahul"}  # emp_rahul is requester (self-block must fail)
    signer = LiveCallCallbackSigner(b"live-block-key")
    manager = LiveCallAlertManager(
        approver_chats_ref=approvers,
        db_path=db_path,
        bot=bot,
        signer=signer,
        min_edit_interval=0.0,
    )

    await manager.notify_live_state(
        req_id,
        "FAIL",
        {"risk": "FAIL", "reason": "Voice clone detected mid-call."},
    )

    # Locate alerts for chat 9001 (Priya) and chat 9002 (Rahul).
    alerts = [m for m in bot.sent if m["reply_markup"] is not None]
    priya_alert = next(m for m in alerts if m["chat_id"] == 9001)
    rahul_alert = next(m for m in alerts if m["chat_id"] == 9002)

    priya_buttons = priya_alert["reply_markup"].inline_keyboard[0]
    rahul_buttons = rahul_alert["reply_markup"].inline_keyboard[0]
    block_cb_priya = priya_buttons[0].callback_data
    callback_cb_priya = priya_buttons[1].callback_data
    block_cb_rahul = rahul_buttons[0].callback_data

    ctx = SimpleNamespace(bot=bot)

    # 1. Forged signature rejected.
    q_forged = _FakeCallbackQuery(
        data=f"lb:{req_id}:-:0000000000000000",
        chat_id=9001,
        message_id=priya_alert["message_id"],
    )
    await manager.handle_callback_query(SimpleNamespace(callback_query=q_forged), ctx)
    assert q_forged.answers[-1][1] is True
    assert "rejected" in (q_forged.answers[-1][0] or "").lower()

    # 2. "Call back on known number" replies with plain instructions + directory hint.
    q_cb = _FakeCallbackQuery(
        data=callback_cb_priya,
        chat_id=9001,
        message_id=priya_alert["message_id"],
    )
    await manager.handle_callback_query(SimpleNamespace(callback_query=q_cb), ctx)
    assert "call the requester back on a number you already know" in bot.sent[-1]["text"].lower()
    assert "4102" in bot.sent[-1]["text"]

    # 3. Requester (emp_rahul) cannot block their own request (role + different-person rule).
    q_self = _FakeCallbackQuery(
        data=block_cb_rahul,
        chat_id=9002,
        message_id=rahul_alert["message_id"],
    )
    await manager.handle_callback_query(SimpleNamespace(callback_query=q_self), ctx)
    assert q_self.answers[-1][1] is True
    with get_db(db_path) as conn:
        assert get_request(conn, req_id)["status"] != "BLOCKED"

    # 4. Authorised different-person approver (emp_priya) taps Block -> marks BLOCKED + FAIL.
    q_block = _FakeCallbackQuery(
        data=block_cb_priya,
        chat_id=9001,
        message_id=priya_alert["message_id"],
    )
    await manager.handle_callback_query(SimpleNamespace(callback_query=q_block), ctx)
    assert "blocked" in (q_block.answers[-1][0] or "").lower()

    with get_db(db_path) as conn:
        updated = get_request(conn, req_id)
        assert updated["status"] == "BLOCKED"
        assert updated["voice_result"]["risk"] == "FAIL"
        assert updated["active_approval"]["status"] == "EXPIRED"
        # Hard FAIL cannot receive a new approval record.
        with pytest.raises(ConflictError):
            issue_approval_record(conn, request_id=req_id)

        # Audit hash chain remains valid and contains no raw audio.
        chain = verify_chain(conn)
        assert chain["valid"] is True
        types = {ev["event_type"] for ev in list_events(conn, request_id=req_id, limit=50)}
        assert "LIVE_CALL_STATE" in types
        assert "REQUEST_BLOCKED" in types
        assert "FAIL_BLOCK_CONFIRMED" in types

    await manager.stop()


@pytest.mark.asyncio
async def test_handles_message_not_modified_and_retry_after_gracefully(tmp_path: Path) -> None:
    db_path = tmp_path / "live_ratelimit.db"
    req_id = _seed_request(db_path)
    bot = _FakeBot()
    approvers = {9001: "emp_priya"}
    manager = LiveCallAlertManager(
        approver_chats_ref=approvers,
        db_path=db_path,
        bot=bot,
        signer=LiveCallCallbackSigner(b"live-rl-key"),
        min_edit_interval=0.0,
    )

    await manager.notify_live_state(req_id, "MONITORING", {"reason": "Starting live monitor."})
    assert len(bot.sent) == 1

    # 1. "Message is not modified" error is ignored gracefully without sending a new message.
    bot.edit_errors.append(BadRequest("Message is not modified: specified new message content is identical"))
    await manager.notify_live_state(req_id, "PASS", {"reason": "Window 1 ok."})
    assert len(bot.sent) == 1

    # 2. RetryAfter defers the edit and flushes the latest text once the cooldown passes.
    bot.edit_errors.append(RetryAfter(0.05))
    await manager.notify_live_state(
        req_id,
        "BORDERLINE",
        {"reason": "Window 2 borderline under rate limit."},
    )
    assert len(bot.edited) == 0
    await asyncio.sleep(0.12)
    assert len(bot.edited) == 1
    assert "BORDERLINE" in bot.edited[-1]["text"]
    assert len(bot.sent) == 1

    # 3. Unknown state fails closed to BORDERLINE, never PASS.
    res = await manager.notify_live_state(req_id, "WEIRD_UNKNOWN", {"reason": "Unexpected state."})
    assert res["effective_state"] == "BORDERLINE"

    await manager.stop()
