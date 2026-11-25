"""Pytest suite for the Telegram Approver Console (no network, real approval/audit/executor)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

try:
    from backend.app.approval.service import create_request, get_request, submit_voice_verification
    from backend.app.audit.service import list_events, verify_chain
    from backend.app.bot.approver_console import (
        ApproverConsole,
        CallbackSigner,
        build_approver_keyboard,
        format_approver_card,
        format_requester_status,
    )
    from backend.app.core.database import get_db, init_db
except ModuleNotFoundError:
    from app.approval.service import create_request, get_request, submit_voice_verification  # type: ignore[no-redef]
    from app.audit.service import list_events, verify_chain  # type: ignore[no-redef]
    from app.bot.approver_console import (  # type: ignore[no-redef]
        ApproverConsole,
        CallbackSigner,
        build_approver_keyboard,
        format_approver_card,
        format_requester_status,
    )
    from app.core.database import get_db, init_db  # type: ignore[no-redef]


class _FakeBot:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.edited: list[dict[str, Any]] = []
        self._next_id = 100

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


class _FakeQuery:
    def __init__(self, *, data: str, chat_id: int, message_id: int) -> None:
        self.data = data
        self.message = SimpleNamespace(chat_id=chat_id, message_id=message_id)
        self.answers: list[tuple[str | None, bool]] = []

    async def answer(self, text: str | None = None, show_alert: bool = False) -> None:
        self.answers.append((text, show_alert))


def _make_verified_request(db_path: Path, voice_payload: dict[str, Any], ttl_seconds: int = 120) -> dict[str, Any]:
    init_db(db_path)
    with get_db(db_path) as conn:
        req = create_request(
            conn,
            employee_id="emp_rahul",
            action_summary="Deploy hotfix 142 to production",
            target_resource="prod-checkout-api",
            justification="SEV-1 payment outage mitigation",
        )
        return submit_voice_verification(
            conn,
            request_id=req["request_id"],
            audio_path="dummy.wav",
            approval_ttl_seconds=ttl_seconds,
            voice_verifier=lambda _path, _emp: voice_payload,
        )


def test_callback_signer_rejects_forgery_and_fits_64_bytes() -> None:
    signer = CallbackSigner(secret_key=b"test-signing-key-32-bytes-minimum")
    token = signer.sign(
        op="a",
        request_id="bg_1234567890ab",
        approval_id="apr_abcdef123456",
        value="42",
        chat_id=777,
    )
    assert len(token.encode("utf-8")) <= 64
    assert signer.verify(token, approval_id="apr_abcdef123456", chat_id=777) == ("a", "bg_1234567890ab", "42")

    # Tampered number, chat_id, approval_id, or request_id must fail.
    assert signer.verify(token.replace(":42:", ":99:"), approval_id="apr_abcdef123456", chat_id=777) is None
    assert signer.verify(token, approval_id="apr_abcdef123456", chat_id=888) is None
    assert signer.verify(token, approval_id="apr_other0000000", chat_id=777) is None
    assert signer.verify(token.replace("bg_1234567890ab", "bg_000000000000"), approval_id="apr_abcdef123456", chat_id=777) is None


def test_pass_flow_enforces_rules_and_sends_simulated_receipt(tmp_path: Path) -> None:
    db_path = tmp_path / "gate.db"
    req = _make_verified_request(
        db_path,
        {
            "aletheia": {"verdict": "real", "score": 0.08, "duration_seconds": 5.4, "operation_id": "op_a1"},
            "eidos": {"decision": "match", "similarity": 0.89, "duration_seconds": 5.4, "operation_id": "op_e1"},
            "risk": "PASS",
            "reason": "Voice matched enrolled speaker. Score is evidence, not proof.",
        },
    )
    signer = CallbackSigner(secret_key=b"test-signing-key-32-bytes-minimum")
    console = ApproverConsole(db_path=db_path, signer=signer)
    bot = _FakeBot()

    # Register same-person requester as chat 10, authorised approver as chat 20, requester status as chat 30.
    console.approver_chats[10] = "emp_rahul"
    console.approver_chats[20] = "emp_priya"
    console.requester_chats["emp_rahul"] = {30}

    asyncio.run(console.dispatch_pending_cards(bot))

    # Requester chat 30 gets plain status ONLY (zero scores!).
    req_msgs = [m for m in bot.sent if m["chat_id"] == 30]
    assert len(req_msgs) == 1
    assert "0.08" not in req_msgs[0]["text"]
    assert "0.89" not in req_msgs[0]["text"]
    assert "aletheia" not in req_msgs[0]["text"].lower()
    assert "eidos" not in req_msgs[0]["text"].lower()

    # Approver chat 20 gets full evidence card + 3 number buttons + Reject.
    apr_msg = next(m for m in bot.sent if m["chat_id"] == 20)
    assert "0.08" in apr_msg["text"]
    assert "0.89" in apr_msg["text"]
    assert "This is evidence, not proof." in apr_msg["text"]
    assert "%" not in apr_msg["text"]
    kb = apr_msg["reply_markup"]
    assert len(kb.inline_keyboard) == 2
    assert len(kb.inline_keyboard[0]) == 3
    assert kb.inline_keyboard[1][0].text == "Reject"

    matching = req["active_approval"]["matching_number"]
    wrong = next(opt for opt in req["active_approval"]["number_options"] if opt != matching)

    # 1. Same-person / requester role attempt in chat 10 rejected by existing approval.service.
    same_person_cb = signer.sign(
        op="a",
        request_id=req["request_id"],
        approval_id=req["active_approval"]["approval_id"],
        value=matching,
        chat_id=10,
    )
    q_same = _FakeQuery(data=same_person_cb, chat_id=10, message_id=101)
    asyncio.run(
        console.handle_callback_query(
            SimpleNamespace(callback_query=q_same),
            SimpleNamespace(bot=bot),
        )
    )
    assert q_same.answers and q_same.answers[0][1] is True
    assert "cannot approve" in (q_same.answers[0][0] or "").lower()

    # 2. Wrong matching number in chat 20 rejected by approval.service.
    wrong_cb = signer.sign(
        op="a",
        request_id=req["request_id"],
        approval_id=req["active_approval"]["approval_id"],
        value=wrong,
        chat_id=20,
    )
    q_wrong = _FakeQuery(data=wrong_cb, chat_id=20, message_id=apr_msg["message_id"])
    asyncio.run(
        console.handle_callback_query(
            SimpleNamespace(callback_query=q_wrong),
            SimpleNamespace(bot=bot),
        )
    )
    assert "did not match" in (q_wrong.answers[0][0] or "").lower()

    # 3. Valid approver + matching number approves, edits card, disables buttons, sends [SIMULATED] receipt.
    ok_cb = signer.sign(
        op="a",
        request_id=req["request_id"],
        approval_id=req["active_approval"]["approval_id"],
        value=matching,
        chat_id=20,
    )
    q_ok = _FakeQuery(data=ok_cb, chat_id=20, message_id=apr_msg["message_id"])
    asyncio.run(
        console.handle_callback_query(
            SimpleNamespace(callback_query=q_ok),
            SimpleNamespace(bot=bot),
        )
    )
    assert bot.edited[-1]["reply_markup"] is None
    assert "APPROVED & EXECUTED (SIMULATED)" in bot.edited[-1]["text"]
    receipt_msgs = [m for m in bot.sent if m["chat_id"] == 20 and "[SIMULATED]" in m["text"]]
    assert len(receipt_msgs) == 1

    with get_db(db_path) as conn:
        assert get_request(conn, req["request_id"])["status"] == "EXECUTED"
        assert verify_chain(conn)["valid"] is True


def test_borderline_shows_warning_and_executes_after_approval(tmp_path: Path) -> None:
    db_path = tmp_path / "gate.db"
    req = _make_verified_request(
        db_path,
        {
            "aletheia": {"verdict": "real", "score": 0.44, "duration_seconds": 5.0, "operation_id": "op_b1"},
            "eidos": None,
            "risk": "BORDERLINE",
            "reason": "Eidos unavailable; failed closed. Call back on a known number.",
        },
    )
    card = format_approver_card(req)
    assert "WARNING: BORDERLINE" in card
    assert "number you already know" in card.lower()

    signer = CallbackSigner(secret_key=b"test-signing-key-32-bytes-minimum")
    console = ApproverConsole(db_path=db_path, signer=signer)
    console.approver_chats[20] = "emp_priya"
    bot = _FakeBot()

    cb = signer.sign(
        op="a",
        request_id=req["request_id"],
        approval_id=req["active_approval"]["approval_id"],
        value=req["active_approval"]["matching_number"],
        chat_id=20,
    )
    q = _FakeQuery(data=cb, chat_id=20, message_id=200)
    asyncio.run(console.handle_callback_query(SimpleNamespace(callback_query=q), SimpleNamespace(bot=bot)))
    assert any("[SIMULATED]" in m["text"] for m in bot.sent if m["chat_id"] == 20)


def test_fail_has_no_approve_buttons_and_cannot_be_overridden(tmp_path: Path) -> None:
    db_path = tmp_path / "gate.db"
    req = _make_verified_request(
        db_path,
        {
            "aletheia": {"verdict": "fake", "score": 0.94, "duration_seconds": 5.0, "operation_id": "op_f1"},
            "eidos": {"decision": "no_match", "similarity": 0.21, "duration_seconds": 5.0, "operation_id": "op_f2"},
            "risk": "FAIL",
            "reason": "Synthetic voice detected. Score is evidence, not proof.",
        },
    )
    signer = CallbackSigner(secret_key=b"test-signing-key-32-bytes-minimum")
    kb = build_approver_keyboard(req, chat_id=20, signer=signer)
    assert kb is not None
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    assert labels == ["Block request", "Call back on known number"]

    console = ApproverConsole(db_path=db_path, signer=signer)
    console.approver_chats[20] = "emp_priya"
    bot = _FakeBot()

    # Clicking "Call back on known number" replies with plain instructions.
    cb_call = kb.inline_keyboard[0][1].callback_data
    q_call = _FakeQuery(data=cb_call, chat_id=20, message_id=300)
    asyncio.run(console.handle_callback_query(SimpleNamespace(callback_query=q_call), SimpleNamespace(bot=bot)))
    assert any("number you already know" in m["text"].lower() for m in bot.sent)

    # Clicking "Block request" confirms block and disables buttons.
    cb_block = kb.inline_keyboard[0][0].callback_data
    q_block = _FakeQuery(data=cb_block, chat_id=20, message_id=300)
    asyncio.run(console.handle_callback_query(SimpleNamespace(callback_query=q_block), SimpleNamespace(bot=bot)))
    assert bot.edited[-1]["reply_markup"] is None
    assert "BLOCKED" in bot.edited[-1]["text"]

    # Even a crafted approve callback for a FAIL request cannot override it.
    crafted_approve = signer.sign(op="a", request_id=req["request_id"], approval_id=None, value="42", chat_id=20)
    q_forge = _FakeQuery(data=crafted_approve, chat_id=20, message_id=300)
    asyncio.run(console.handle_callback_query(SimpleNamespace(callback_query=q_forge), SimpleNamespace(bot=bot)))
    assert q_forge.answers[0][1] is True
    assert "cannot be overridden" in (q_forge.answers[0][0] or "").lower()

    with get_db(db_path) as conn:
        events = list_events(conn, request_id=req["request_id"])
        assert any(e["event_type"] == "FAIL_BLOCK_CONFIRMED" for e in events)
        assert get_request(conn, req["request_id"])["status"] == "BLOCKED"


def test_expired_window_edits_card_and_disables_buttons(tmp_path: Path) -> None:
    db_path = tmp_path / "gate.db"
    req = _make_verified_request(
        db_path,
        {
            "aletheia": {"verdict": "real", "score": 0.11, "duration_seconds": 5.0, "operation_id": "op_x1"},
            "eidos": {"decision": "match", "similarity": 0.86, "duration_seconds": 5.0, "operation_id": "op_x2"},
            "risk": "PASS",
            "reason": "Voice matched. Score is evidence, not proof.",
        },
    )
    # Force expiry in DB.
    past = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    with get_db(db_path) as conn:
        conn.execute(
            "UPDATE approval_records SET expires_at = ? WHERE approval_id = ?",
            (past, req["active_approval"]["approval_id"]),
        )

    signer = CallbackSigner(secret_key=b"test-signing-key-32-bytes-minimum")
    console = ApproverConsole(db_path=db_path, signer=signer)
    console.approver_chats[20] = "emp_priya"
    bot = _FakeBot()

    cb = signer.sign(
        op="a",
        request_id=req["request_id"],
        approval_id=req["active_approval"]["approval_id"],
        value=req["active_approval"]["matching_number"],
        chat_id=20,
    )
    q = _FakeQuery(data=cb, chat_id=20, message_id=400)
    asyncio.run(console.handle_callback_query(SimpleNamespace(callback_query=q), SimpleNamespace(bot=bot)))
    assert "expired" in (q.answers[0][0] or "").lower()
    assert bot.edited[-1]["reply_markup"] is None
    assert "Expired" in bot.edited[-1]["text"]


def test_requester_status_never_leaks_scores(tmp_path: Path) -> None:
    db_path = tmp_path / "gate.db"
    req = _make_verified_request(
        db_path,
        {
            "aletheia": {"verdict": "fake", "score": 0.97, "duration_seconds": 6.0, "operation_id": "op_s1"},
            "eidos": {"decision": "no_match", "similarity": 0.19, "duration_seconds": 6.0, "operation_id": "op_s2"},
            "risk": "FAIL",
            "reason": "Synthetic voice signs detected.",
        },
    )
    plain = format_requester_status(req)
    for forbidden in ("0.97", "0.19", "aletheia", "eidos", "fake", "no_match", "similarity"):
        assert forbidden not in plain.lower()
