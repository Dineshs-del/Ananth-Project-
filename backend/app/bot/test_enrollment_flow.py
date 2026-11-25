"""Pytest suite for Telegram voice enrollment flow (no network, real DB & audit chain)."""

from __future__ import annotations

import asyncio
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

try:
    from backend.app.approval.service import get_employee
    from backend.app.audit.service import list_events, verify_chain
    from backend.app.bot import audio as audio_mod
    from backend.app.bot.enrollment_flow import (
        ENROLL_MAX_DURATION_SECONDS,
        ENROLL_MIN_DURATION_SECONDS,
        EnrollmentConsentSigner,
        TelegramEnrollmentFlow,
        parse_env_user_links,
    )
    from backend.app.core.database import get_db, init_db
except ModuleNotFoundError:
    from app.approval.service import get_employee  # type: ignore[no-redef]
    from app.audit.service import list_events, verify_chain  # type: ignore[no-redef]
    from app.bot import audio as audio_mod  # type: ignore[no-redef]
    from app.bot.enrollment_flow import (  # type: ignore[no-redef]
        ENROLL_MAX_DURATION_SECONDS,
        ENROLL_MIN_DURATION_SECONDS,
        EnrollmentConsentSigner,
        TelegramEnrollmentFlow,
        parse_env_user_links,
    )
    from app.core.database import get_db, init_db  # type: ignore[no-redef]


def _write_wav(path: Path, seconds: float, rate: int = 16000) -> None:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x00" * int(seconds * rate))


class _FakeMessage:
    def __init__(self, *, voice: Any = None, audio: Any = None, fail_delete: bool = False) -> None:
        self.voice = voice
        self.audio = audio
        self.fail_delete = fail_delete
        self.replies: list[tuple[str, Any]] = []
        self.deleted = False

    async def reply_text(self, text: str, reply_markup: Any = None) -> None:
        self.replies.append((text, reply_markup))

    async def delete(self) -> None:
        if self.fail_delete:
            raise RuntimeError("no delete permission")
        self.deleted = True


class _FakeQuery:
    def __init__(self, *, data: str, user_id: int, chat_id: int) -> None:
        self.data = data
        self.from_user = SimpleNamespace(id=user_id)
        self.message = SimpleNamespace(chat_id=chat_id, message_id=77)
        self.answers: list[tuple[str | None, bool]] = []
        self.edited: list[tuple[str, Any]] = []

    async def answer(self, text: str | None = None, show_alert: bool = False) -> None:
        self.answers.append((text, show_alert))

    async def edit_message_text(self, text: str, reply_markup: Any = None) -> None:
        self.edited.append((text, reply_markup))


class _FakeBot:
    async def get_file(self, file_id: str) -> Any:
        return SimpleNamespace(file_id=file_id)


def test_admin_link_only_security_admin_and_blocks_unlinked_enroll(tmp_path: Path) -> None:
    db_path = tmp_path / "enroll.db"
    init_db(db_path)

    approver_chats = {10: "emp_priya", 20: "emp_carol"}  # priya=approver, carol=security_admin
    flow = TelegramEnrollmentFlow(db_path=db_path, approver_chats_ref=approver_chats, user_links={})

    # Non-admin (approver role) cannot create links.
    m_deny = _FakeMessage()
    asyncio.run(
        flow.handle_link_command(
            SimpleNamespace(effective_message=m_deny, effective_chat=SimpleNamespace(id=10)),
            SimpleNamespace(args=["555", "emp_rahul"]),
        )
    )
    assert "access denied" in m_deny.replies[0][0].lower()
    assert 555 not in flow.user_links

    # Unlinked user cannot run /enroll (cannot enroll as someone else).
    m_unlinked = _FakeMessage()
    asyncio.run(
        flow.handle_enroll_command(
            SimpleNamespace(
                effective_message=m_unlinked,
                effective_user=SimpleNamespace(id=555),
                effective_chat=SimpleNamespace(id=555, type="private"),
            ),
            SimpleNamespace(args=["emp_priya"]),
        )
    )
    assert "not linked" in m_unlinked.replies[0][0].lower()

    # security_admin (emp_carol) links user 555 -> emp_rahul.
    m_ok = _FakeMessage()
    asyncio.run(
        flow.handle_link_command(
            SimpleNamespace(effective_message=m_ok, effective_chat=SimpleNamespace(id=20)),
            SimpleNamespace(args=["555", "emp_rahul"]),
        )
    )
    assert flow.user_links[555] == "emp_rahul"
    assert parse_env_user_links("111:emp_rahul, 222:emp_priya") == {111: "emp_rahul", 222: "emp_priya"}


def test_full_enrollment_consent_duration_cap_cleanup_and_audit(tmp_path: Path) -> None:
    db_path = tmp_path / "enroll_full.db"
    init_db(db_path)
    # Reset emp_rahul enrolled_samples to 0 for clean progress assertions.
    with get_db(db_path) as conn:
        conn.execute("UPDATE employees SET enrolled_samples = 0 WHERE employee_id = 'emp_rahul'")

    enrolled_calls: list[tuple[str, list[Path]]] = []
    temp_paths_seen: list[Path] = []

    def _fake_enroll(emp_id: str, paths: list[Path], *, display_name: str | None = None) -> dict[str, Any]:
        enrolled_calls.append((emp_id, list(paths)))
        temp_paths_seen.extend(paths)
        return {
            "employee_id": emp_id,
            "enrolled": [{"file": str(paths[0]), "operation_id": "op_enr_1", "request_id": "req_1"}],
            "skipped": [],
            "cap_reached": False,
            "message": "ok",
        }

    async def _fake_prepare_wav(
        _tg_file: Any,
        *,
        temp_dir: Path,
        declared_size: int | None = None,
        declared_duration: int | float | None = None,
        min_duration: float = ENROLL_MIN_DURATION_SECONDS,
        max_duration: float = ENROLL_MAX_DURATION_SECONDS,
    ) -> tuple[Path, float]:
        assert min_duration == 15.0 and max_duration == 60.0
        dur = float(declared_duration or 20.0)
        audio_mod.validate_duration(dur, min_duration=min_duration, max_duration=max_duration)
        wav = temp_dir / "normalized_16k_mono.wav"
        _write_wav(wav, min(dur, 1.0))  # tiny wav for test speed
        return wav, dur

    signer = EnrollmentConsentSigner(secret_key=b"test-enroll-key-32-bytes-minimum")
    flow = TelegramEnrollmentFlow(
        db_path=db_path,
        signer=signer,
        user_links={555: "emp_rahul"},
        enroll_fn=_fake_enroll,
        prepare_wav_fn=_fake_prepare_wav,
    )

    # 1. /enroll displays consent card with 'I consent' button.
    m_start = _FakeMessage()
    asyncio.run(
        flow.handle_enroll_command(
            SimpleNamespace(
                effective_message=m_start,
                effective_user=SimpleNamespace(id=555),
                effective_chat=SimpleNamespace(id=555, type="private"),
            ),
            SimpleNamespace(args=[]),
        )
    )
    text, kb = m_start.replies[0]
    assert "VOICE ENROLLMENT CONSENT" in text
    consent_btn = kb.inline_keyboard[0][0]
    assert consent_btn.text == "I consent"

    # 2. Sending voice note BEFORE consent is rejected (no API call) & message deleted.
    m_early = _FakeMessage(voice=SimpleNamespace(file_id="f0", file_size=1000, duration=20))
    asyncio.run(
        flow.handle_enrollment_audio(
            SimpleNamespace(effective_message=m_early, effective_user=SimpleNamespace(id=555)),
            SimpleNamespace(bot=_FakeBot()),
        )
    )
    assert "I consent" in m_early.replies[0][0]
    assert m_early.deleted is True
    assert enrolled_calls == []

    # 3. Another user cannot press user 555's consent button.
    q_forged = _FakeQuery(data=consent_btn.callback_data, user_id=999, chat_id=555)
    asyncio.run(flow.handle_consent_callback(SimpleNamespace(callback_query=q_forged), SimpleNamespace()))
    assert q_forged.answers[0][1] is True

    # 4. User 555 taps 'I consent' -> recorded in audit chain.
    q_ok = _FakeQuery(data=consent_btn.callback_data, user_id=555, chat_id=555)
    asyncio.run(flow.handle_consent_callback(SimpleNamespace(callback_query=q_ok), SimpleNamespace()))
    assert q_ok.answers[0][0] == "Consent recorded."

    # 5. Too-short clip (10s < 15s) rejected BEFORE calling enrollment API & audited.
    m_short = _FakeMessage(voice=SimpleNamespace(file_id="f1", file_size=1000, duration=10))
    asyncio.run(
        flow.handle_enrollment_audio(
            SimpleNamespace(effective_message=m_short, effective_user=SimpleNamespace(id=555)),
            SimpleNamespace(bot=_FakeBot()),
        )
    )
    assert "too short" in m_short.replies[0][0].lower()
    assert enrolled_calls == []
    assert m_short.deleted is True

    # 6. Too-long clip (65s > 60s) rejected BEFORE calling enrollment API.
    m_long = _FakeMessage(voice=SimpleNamespace(file_id="f2", file_size=1000, duration=65))
    asyncio.run(
        flow.handle_enrollment_audio(
            SimpleNamespace(effective_message=m_long, effective_user=SimpleNamespace(id=555)),
            SimpleNamespace(bot=_FakeBot()),
        )
    )
    assert "too long" in m_long.replies[0][0].lower()
    assert enrolled_calls == []

    # 7. Valid 20s clip accepted (even if chat delete fails gracefully), temp WAV deleted, progress shown.
    m_good = _FakeMessage(
        voice=SimpleNamespace(file_id="f3", file_size=2000, duration=20),
        fail_delete=True,
    )
    asyncio.run(
        flow.handle_enrollment_audio(
            SimpleNamespace(effective_message=m_good, effective_user=SimpleNamespace(id=555)),
            SimpleNamespace(bot=_FakeBot()),
        )
    )
    reply_good = m_good.replies[0][0]
    assert "Sample 1 of 3 recommended (maximum 5)." in reply_good
    assert "score" not in reply_good.lower()
    assert "embedding" not in reply_good.lower()
    assert len(enrolled_calls) == 1
    assert all(not p.exists() for p in temp_paths_seen)  # temp audio deleted immediately!

    # 8. Reaching max 5 samples rejects extra samples without calling API.
    with get_db(db_path) as conn:
        conn.execute("UPDATE employees SET enrolled_samples = 5 WHERE employee_id = 'emp_rahul'")
    m_extra = _FakeMessage(voice=SimpleNamespace(file_id="f4", file_size=2000, duration=20))
    asyncio.run(
        flow.handle_enrollment_audio(
            SimpleNamespace(effective_message=m_extra, effective_user=SimpleNamespace(id=555)),
            SimpleNamespace(bot=_FakeBot()),
        )
    )
    assert "maximum of 5" in m_extra.replies[0][0].lower()
    assert len(enrolled_calls) == 1  # No extra API call!

    with get_db(db_path) as conn:
        assert get_employee(conn, "emp_rahul")["enrolled_samples"] == 5
        assert verify_chain(conn)["valid"] is True
        event_types = {e["event_type"] for e in list_events(conn, limit=20)}
        assert {
            "ENROLLMENT_CONSENT",
            "ENROLLMENT_SAMPLE_ACCEPTED",
            "ENROLLMENT_SAMPLE_REJECTED",
        }.issubset(event_types)
