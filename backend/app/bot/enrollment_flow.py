"""Telegram voice enrollment workflow for Break-Glass Voice Gate.

Enforces Detectif Eidos enrollment rules:
- 15 to 60 seconds per sample (at least 10 seconds of speech)
- Maximum 5 samples per speaker (3 recommended)
- Admin-created link between Telegram user_id and employee_id (prevents impersonation)
- Explicit 'I consent' button press audited fail-closed before accepting any audio
- Reuses backend/app/ai/enrollment.py and backend/app/bot/audio.py (ffmpeg -> 16 kHz mono WAV)
- Deletes temporary audio immediately and attempts to delete the user's chat voice message
- Never stores or logs raw audio, and never displays scores or embeddings
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import secrets
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

from ..ai.enrollment import MAX_ENROLLMENT_SAMPLES, EnrollmentError, enroll_employee
from ..approval.service import ApprovalError, get_employee, record_enrollment_count
from ..audit.service import record_event
from ..core.database import get_db, init_db
from .audio import (
    AudioConversionError,
    AudioDurationError,
    AudioSizeError,
    download_and_prepare_wav,
)
from .audit_bridge import (
    AuditLoggingError,
    log_enrollment_consent,
    log_enrollment_sample,
)

logger = logging.getLogger("voicegate.bot.enroll")

# Documented Detectif Eidos enrollment rules (single source of truth).
ENROLL_MIN_DURATION_SECONDS: float = 15.0
ENROLL_MAX_DURATION_SECONDS: float = 60.0
ENROLL_MIN_SPEECH_SECONDS: float = 10.0
ENROLL_RECOMMENDED_SAMPLES: int = 3
ENROLL_MAX_SAMPLES: int = MAX_ENROLLMENT_SAMPLES  # 5

ADMIN_LINK_ROLE = "security_admin"
MAX_TELEGRAM_CALLBACK_BYTES = 64

CONSENT_TEXT = (
    "VOICE ENROLLMENT CONSENT\n\n"
    "Your voice sample will be sent to DetectifAI (Eidos) solely to create your "
    "break-glass speaker verification profile.\n"
    "- Temporary audio files are deleted immediately after upload and are never stored by this bot.\n"
    "- Each sample must be 15 to 60 seconds long with at least 10 seconds of clear speech.\n"
    "- 3 samples are recommended (maximum 5 per speaker).\n\n"
    "Tap 'I consent' below to confirm and begin enrollment."
)


@dataclass
class _EnrollSession:
    employee_id: str
    display_name: str
    consented: bool = False


class EnrollmentConsentSigner:
    """HMAC-SHA256 signer for consent/cancel buttons bound to Telegram user_id & chat_id."""

    def __init__(self, secret_key: bytes | None = None) -> None:
        if secret_key is not None:
            self._key = secret_key
        else:
            env_secret = os.getenv("BOT_CALLBACK_SECRET", "").strip()
            self._key = env_secret.encode("utf-8") if env_secret else secrets.token_bytes(32)

    def _digest(self, *, op: str, user_id: int, employee_id: str, chat_id: int) -> str:
        msg = f"{op}|{user_id}|{employee_id}|{chat_id}".encode("utf-8")
        return hmac.new(self._key, msg, hashlib.sha256).hexdigest()[:16]

    def sign(self, *, op: str, user_id: int, employee_id: str, chat_id: int) -> str:
        if op not in ("ec", "ex"):
            raise ValueError(f"Unsupported enrollment callback op: {op}")
        sig = self._digest(op=op, user_id=user_id, employee_id=employee_id, chat_id=chat_id)
        payload = f"{op}:{user_id}:{sig}"
        if len(payload.encode("utf-8")) > MAX_TELEGRAM_CALLBACK_BYTES:
            raise ValueError("Enrollment callback payload exceeded Telegram 64-byte limit.")
        return payload

    def verify(
        self,
        payload: str,
        *,
        expected_user_id: int,
        employee_id: str,
        chat_id: int,
    ) -> str | None:
        if not isinstance(payload, str):
            return None
        parts = payload.split(":")
        if len(parts) != 3:
            return None
        op, raw_uid, provided_sig = parts
        if op not in ("ec", "ex") or len(provided_sig) != 16:
            return None
        try:
            uid = int(raw_uid)
        except ValueError:
            return None
        if uid != expected_user_id:
            return None
        expected_sig = self._digest(
            op=op,
            user_id=uid,
            employee_id=employee_id,
            chat_id=chat_id,
        )
        if not hmac.compare_digest(provided_sig, expected_sig):
            return None
        return op


def parse_env_user_links(raw: str | None = None) -> dict[int, str]:
    """Parse admin-configured TELEGRAM_USER_LINKS='tg_id:employee_id,...' into {int: str}."""
    text = (os.getenv("TELEGRAM_USER_LINKS", "") if raw is None else raw).strip()
    links: dict[int, str] = {}
    if not text:
        return links
    for item in text.split(","):
        pair = item.strip()
        if not pair or ":" not in pair:
            continue
        uid_str, emp_id = pair.split(":", 1)
        emp_id = emp_id.strip()
        if not emp_id:
            continue
        try:
            links[int(uid_str.strip())] = emp_id
        except ValueError:
            logger.warning("Ignoring malformed TELEGRAM_USER_LINKS entry.")
    return links


def format_enrollment_progress(enrolled_count: int) -> str:
    """Format progress line without exposing any scores or embeddings."""
    return (
        f"Sample {enrolled_count} of {ENROLL_RECOMMENDED_SAMPLES} recommended "
        f"(maximum {ENROLL_MAX_SAMPLES})."
    )


async def _try_delete_message(message: Message | None) -> None:
    """Attempt to delete the user's voice message from chat, handling failure gracefully."""
    if message is None:
        return
    try:
        await message.delete()
    except (BadRequest, TelegramError):
        logger.debug("Could not delete user voice message from Telegram chat (continuing safely).")
    except Exception:
        logger.debug("Skipping chat message deletion.")


class TelegramEnrollmentFlow:
    """Manages admin-linked /enroll consent, 15-60s validation, WAV conversion, and enrollment."""

    def __init__(
        self,
        *,
        db_path: Path | None = None,
        signer: EnrollmentConsentSigner | None = None,
        approver_chats_ref: dict[int, str] | None = None,
        user_links: dict[int, str] | None = None,
        enroll_fn: Callable[..., dict[str, Any]] = enroll_employee,
        prepare_wav_fn: Callable[..., Any] = download_and_prepare_wav,
    ) -> None:
        self._db_path = db_path
        self._signer = signer or EnrollmentConsentSigner()
        self._approver_chats = approver_chats_ref if approver_chats_ref is not None else {}
        self.user_links: dict[int, str] = (
            dict(user_links) if user_links is not None else parse_env_user_links()
        )
        self._sessions: dict[int, _EnrollSession] = {}
        self._enroll_fn = enroll_fn
        self._prepare_wav_fn = prepare_wav_fn

    def has_active_session(self, user_id: int) -> bool:
        return user_id in self._sessions

    async def handle_link_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Admin-only /link <telegram_user_id> <employee_id> to bind a Telegram account to an employee."""
        msg = update.effective_message
        chat = update.effective_chat
        if msg is None or chat is None:
            return

        admin_emp_id = self._approver_chats.get(chat.id)
        if not admin_emp_id:
            await msg.reply_text(
                "Access denied: only a registered security_admin chat can link Telegram accounts. "
                "Register with /approver <security_admin_employee_id> first."
            )
            return

        args = context.args or []
        if len(args) != 2:
            await msg.reply_text("Usage: /link <telegram_user_id> <employee_id>")
            return

        try:
            target_tg_user_id = int(args[0].strip())
        except ValueError:
            await msg.reply_text("telegram_user_id must be a numeric Telegram user ID.")
            return
        target_emp_id = args[1].strip()

        def _verify_admin_and_audit() -> dict[str, Any]:
            init_db(self._db_path)
            with get_db(self._db_path) as conn:
                admin_emp = get_employee(conn, admin_emp_id)
                if admin_emp["role"] != ADMIN_LINK_ROLE:
                    raise ApprovalError(
                        f"Access denied: '{admin_emp_id}' has role '{admin_emp['role']}'. "
                        f"Only '{ADMIN_LINK_ROLE}' can create enrollment links."
                    )
                target_emp = get_employee(conn, target_emp_id)
                try:
                    record_event(
                        conn,
                        event_type="TELEGRAM_EMPLOYEE_LINKED",
                        actor_id=admin_emp_id,
                        request_id=None,
                        details={
                            "channel": "telegram",
                            "target_employee_id": target_emp_id,
                            "telegram_user_id": target_tg_user_id,
                        },
                    )
                except Exception as exc:
                    raise AuditLoggingError("Failed to audit Telegram employee link.") from exc
                return target_emp

        try:
            target_emp = await asyncio.to_thread(_verify_admin_and_audit)
        except ApprovalError as exc:
            await msg.reply_text(str(exc))
            return
        except AuditLoggingError:
            await msg.reply_text("Audit logging failed; account link was not created.")
            return

        self.user_links[target_tg_user_id] = target_emp_id
        await msg.reply_text(
            f"Linked Telegram user {target_tg_user_id} to {target_emp['display_name']} ({target_emp_id}). "
            "That user can now run /enroll in DM."
        )

    async def handle_enroll_command(
        self,
        update: Update,
        _context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Start DM enrollment strictly for the employee linked to the sender's Telegram user ID."""
        msg = update.effective_message
        user = update.effective_user
        chat = update.effective_chat
        if msg is None or user is None or chat is None:
            return

        if str(chat.type) != "private":
            await msg.reply_text("Please run /enroll in a private direct message (DM) with the bot.")
            return

        # Strict impersonation guard: ONLY look up the admin-created link for sender's user.id.
        employee_id = self.user_links.get(user.id)
        if not employee_id:
            await msg.reply_text(
                "Your Telegram account is not linked to an employee profile.\n"
                f"Ask a security admin to link your Telegram user ID ({user.id}) before enrolling."
            )
            return

        def _load_emp() -> dict[str, Any]:
            init_db(self._db_path)
            with get_db(self._db_path) as conn:
                return get_employee(conn, employee_id)

        try:
            emp = await asyncio.to_thread(_load_emp)
        except ApprovalError as exc:
            await msg.reply_text(str(exc))
            return

        if int(emp["enrolled_samples"]) >= ENROLL_MAX_SAMPLES:
            await msg.reply_text(
                f"{emp['display_name']} ({employee_id}) already has {ENROLL_MAX_SAMPLES} of "
                f"{ENROLL_MAX_SAMPLES} maximum enrollment samples. Extra samples cannot be added."
            )
            return

        self._sessions[user.id] = _EnrollSession(
            employee_id=employee_id,
            display_name=emp["display_name"],
            consented=False,
        )

        consent_cb = self._signer.sign(
            op="ec",
            user_id=user.id,
            employee_id=employee_id,
            chat_id=chat.id,
        )
        cancel_cb = self._signer.sign(
            op="ex",
            user_id=user.id,
            employee_id=employee_id,
            chat_id=chat.id,
        )
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("I consent", callback_data=consent_cb),
                    InlineKeyboardButton("Cancel", callback_data=cancel_cb),
                ]
            ]
        )

        await msg.reply_text(
            f"Enrolling: {emp['display_name']} ({employee_id})\n"
            f"Current progress: {emp['enrolled_samples']} of {ENROLL_RECOMMENDED_SAMPLES} "
            f"recommended (maximum {ENROLL_MAX_SAMPLES}).\n\n"
            f"{CONSENT_TEXT}",
            reply_markup=keyboard,
        )

    async def handle_done_command(
        self,
        update: Update,
        _context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Exit enrollment mode cleanly."""
        msg = update.effective_message
        user = update.effective_user
        if msg is None or user is None:
            return
        removed = self._sessions.pop(user.id, None)
        if removed is None:
            await msg.reply_text("You are not currently in voice enrollment mode.")
            return
        await msg.reply_text(
            f"Voice enrollment session ended for {removed.display_name} ({removed.employee_id})."
        )

    async def handle_consent_callback(
        self,
        update: Update,
        _context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Verify signed 'I consent' / 'Cancel' button press and audit consent fail-closed."""
        query = update.callback_query
        if query is None or not query.data:
            return
        from_user = query.from_user
        msg = query.message
        if from_user is None or msg is None:
            await query.answer("Invalid consent context.", show_alert=True)
            return

        session = self._sessions.get(from_user.id)
        if session is None:
            await query.answer("No active enrollment session. Run /enroll first.", show_alert=True)
            return

        op = self._signer.verify(
            query.data,
            expected_user_id=from_user.id,
            employee_id=session.employee_id,
            chat_id=msg.chat_id,
        )
        if op is None:
            await query.answer("Invalid or forged consent callback.", show_alert=True)
            return

        if op == "ex":
            self._sessions.pop(from_user.id, None)
            await query.answer("Enrollment cancelled.")
            try:
                await query.edit_message_text("Voice enrollment cancelled.", reply_markup=None)
            except TelegramError:
                pass
            return

        # Fail closed: record consent in existing audit chain BEFORE accepting any audio.
        try:
            await asyncio.to_thread(
                log_enrollment_consent,
                actor_id=session.employee_id,
                employee_id=session.employee_id,
                db_path=self._db_path,
            )
        except AuditLoggingError:
            await query.answer(
                "Audit logging failed; consent could not be recorded and enrollment cannot proceed.",
                show_alert=True,
            )
            return

        session.consented = True
        await query.answer("Consent recorded.")

        def _current_count() -> int:
            with get_db(self._db_path) as conn:
                return int(get_employee(conn, session.employee_id)["enrolled_samples"])

        count = await asyncio.to_thread(_current_count)
        next_num = count + 1
        try:
            await query.edit_message_text(
                f"Consent recorded for {session.display_name} ({session.employee_id}).\n\n"
                f"Please send voice note #{next_num} now:\n"
                f"- Length: {int(ENROLL_MIN_DURATION_SECONDS)} to {int(ENROLL_MAX_DURATION_SECONDS)} seconds\n"
                f"- Include at least {int(ENROLL_MIN_SPEECH_SECONDS)} seconds of clear speech\n"
                f"- Current progress: {count} of {ENROLL_RECOMMENDED_SAMPLES} recommended "
                f"(maximum {ENROLL_MAX_SAMPLES})\n"
                "- Send /done when finished.",
                reply_markup=None,
            )
        except TelegramError:
            pass

    async def handle_enrollment_audio(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Process a DM voice/audio sample during an active enrollment session."""
        msg = update.effective_message
        user = update.effective_user
        if msg is None or user is None:
            return

        session = self._sessions.get(user.id)
        if session is None:
            return

        # Consent gate: refuse any audio before explicit 'I consent' button press.
        if not session.consented:
            await _try_delete_message(msg)
            await msg.reply_text(
                "Please tap the 'I consent' button above before sending any voice sample."
            )
            return

        media = msg.voice or msg.audio
        if media is None:
            return

        employee_id = session.employee_id

        # 1. Check 5-sample cap BEFORE touching the API.
        def _load_emp() -> dict[str, Any]:
            init_db(self._db_path)
            with get_db(self._db_path) as conn:
                return get_employee(conn, employee_id)

        try:
            emp = await asyncio.to_thread(_load_emp)
        except ApprovalError as exc:
            await _try_delete_message(msg)
            await msg.reply_text(str(exc))
            return

        if int(emp["enrolled_samples"]) >= ENROLL_MAX_SAMPLES:
            await _try_delete_message(msg)
            self._sessions.pop(user.id, None)
            try:
                await asyncio.to_thread(
                    log_enrollment_sample,
                    actor_id=employee_id,
                    employee_id=employee_id,
                    accepted=False,
                    reason="max_samples_reached",
                    db_path=self._db_path,
                )
            except AuditLoggingError:
                await msg.reply_text("Audit logging failed; sample was rejected.")
                return
            await msg.reply_text(
                f"Maximum of {ENROLL_MAX_SAMPLES} enrollment samples already reached for "
                f"{employee_id}. Extra samples are rejected."
            )
            return

        # 2. Download, validate 15-60s BEFORE API, convert OGG/Opus -> 16 kHz mono WAV,
        #    call existing enroll_employee, and delete temp dir immediately.
        try:
            with tempfile.TemporaryDirectory(prefix="voicegate_enroll_") as tmp_name:
                tg_file = await context.bot.get_file(media.file_id)
                wav_path, _duration = await self._prepare_wav_fn(
                    tg_file,
                    temp_dir=Path(tmp_name),
                    declared_size=media.file_size,
                    declared_duration=media.duration,
                    min_duration=ENROLL_MIN_DURATION_SECONDS,
                    max_duration=ENROLL_MAX_DURATION_SECONDS,
                )
                summary = await asyncio.to_thread(
                    self._enroll_fn,
                    employee_id,
                    [wav_path],
                    display_name=emp["display_name"],
                )
        except (AudioDurationError, AudioSizeError) as exc:
            await _try_delete_message(msg)
            try:
                await asyncio.to_thread(
                    log_enrollment_sample,
                    actor_id=employee_id,
                    employee_id=employee_id,
                    accepted=False,
                    reason="validation_failed",
                    db_path=self._db_path,
                )
            except AuditLoggingError:
                await msg.reply_text("Audit logging failed; sample could not be processed.")
                return
            await msg.reply_text(
                f"{exc}\n"
                f"Enrollment requires {int(ENROLL_MIN_DURATION_SECONDS)} to "
                f"{int(ENROLL_MAX_DURATION_SECONDS)} seconds with at least "
                f"{int(ENROLL_MIN_SPEECH_SECONDS)} seconds of clear speech."
            )
            return
        except AudioConversionError as exc:
            await _try_delete_message(msg)
            try:
                await asyncio.to_thread(
                    log_enrollment_sample,
                    actor_id=employee_id,
                    employee_id=employee_id,
                    accepted=False,
                    reason="conversion_failed",
                    db_path=self._db_path,
                )
            except AuditLoggingError:
                await msg.reply_text("Audit logging failed; sample could not be processed.")
                return
            await msg.reply_text(
                f"Could not convert that voice note to WAV: {exc}\n"
                "Please ensure ffmpeg is installed and send a clear 15 to 60 second voice note."
            )
            return
        except EnrollmentError:
            await _try_delete_message(msg)
            try:
                await asyncio.to_thread(
                    log_enrollment_sample,
                    actor_id=employee_id,
                    employee_id=employee_id,
                    accepted=False,
                    reason="enrollment_api_error",
                    db_path=self._db_path,
                )
            except AuditLoggingError:
                await msg.reply_text("Audit logging failed; enrollment could not proceed.")
                return
            await msg.reply_text(
                "Enrollment sample was rejected by the voice service. "
                f"Make sure the clip is {int(ENROLL_MIN_DURATION_SECONDS)} to "
                f"{int(ENROLL_MAX_DURATION_SECONDS)} seconds with at least "
                f"{int(ENROLL_MIN_SPEECH_SECONDS)} seconds of clear speech, then try again."
            )
            return
        except Exception:
            await _try_delete_message(msg)
            logger.warning("Unexpected error during Telegram voice enrollment.")
            await msg.reply_text("Enrollment could not be completed right now. Please try again.")
            return

        # 3. Try to delete the user's voice message from the chat gracefully.
        await _try_delete_message(msg)

        cap_reached = bool(summary.get("cap_reached"))
        added_count = len(summary.get("enrolled") or [])

        if cap_reached or added_count == 0:
            self._sessions.pop(user.id, None)

            def _record_cap() -> None:
                with get_db(self._db_path) as conn:
                    log_enrollment_sample(
                        actor_id=employee_id,
                        employee_id=employee_id,
                        accepted=False,
                        reason="cap_reached_409",
                        conn=conn,
                    )

            try:
                await asyncio.to_thread(_record_cap)
            except AuditLoggingError:
                await msg.reply_text("Audit logging failed; enrollment cap could not be recorded.")
                return
            await msg.reply_text(
                f"Maximum of {ENROLL_MAX_SAMPLES} enrollment samples already reached for "
                f"{employee_id}. Extra samples were rejected."
            )
            return

        # 4. Update employee sample count & write ENROLLMENT_SAMPLE_ACCEPTED atomically (fail closed).
        def _commit_accepted() -> dict[str, Any]:
            with get_db(self._db_path) as conn:
                updated_emp = record_enrollment_count(
                    conn,
                    employee_id=employee_id,
                    added_samples=added_count,
                    cap_reached=False,
                )
                log_enrollment_sample(
                    actor_id=employee_id,
                    employee_id=employee_id,
                    accepted=True,
                    reason="sample_enrolled",
                    conn=conn,
                )
                return updated_emp

        try:
            updated_emp = await asyncio.to_thread(_commit_accepted)
        except AuditLoggingError:
            await msg.reply_text("Audit logging failed; enrollment record was not committed.")
            return

        new_count = int(updated_emp["enrolled_samples"])
        progress_line = format_enrollment_progress(new_count)

        if new_count >= ENROLL_MAX_SAMPLES:
            self._sessions.pop(user.id, None)
            await msg.reply_text(
                f"Voice sample accepted! {progress_line}\n"
                "You have reached the maximum of 5 samples. Enrollment is complete."
            )
        elif new_count >= ENROLL_RECOMMENDED_SAMPLES:
            remaining = ENROLL_MAX_SAMPLES - new_count
            await msg.reply_text(
                f"Voice sample accepted! {progress_line}\n"
                f"Recommended minimum reached. You may send up to {remaining} more sample(s) "
                "or send /done to finish."
            )
        else:
            await msg.reply_text(
                f"Voice sample accepted! {progress_line}\n"
                f"Please send your next {int(ENROLL_MIN_DURATION_SECONDS)} to "
                f"{int(ENROLL_MAX_DURATION_SECONDS)} second voice note (or /done to pause)."
            )
