"""Live Call Guard service (near-live, chunk-based voice monitoring).

Reuses:
- backend/app/core/storage.py (confined temp audio storage)
- backend/app/bot/audio.py (ffmpeg 16 kHz mono WAV conversion, no shell=True)
- backend/app/ai/voice_check.py (verify_voice 4-key contract & fixture loader)
- backend/app/approval/service.py (block_request_on_voice_fail)
- backend/app/audit/service.py (append-only hash-chained audit log, fail closed)
- backend/app/bot/live_call_alerts.py (notify_live_state in-process or via localhost bridge)
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, Callable

import httpx

from ..ai.config import ConfigError, load_config
from ..ai.voice_check import _load_fixture, verify_voice
from ..approval.service import (
    ApprovalError,
    ConflictError,
    NotFoundError,
    block_request_on_voice_fail,
    get_employee,
    get_request,
)
from ..audit.service import record_event
from ..bot.audio import AudioConversionError, AudioDurationError, convert_to_wav_16k_mono
from ..bot.live_call_alerts import get_live_call_manager, notify_live_state
from ..core.config import CoreConfig, load_core_config
from ..core.database import get_db, init_db, utc_now_iso
from ..core.storage import UploadValidationError, save_audio_upload

logger = logging.getLogger("voicegate.live_guard")

LIVE_MIN_CHUNK_SECONDS: float = 0.5
LIVE_MAX_CHUNK_SECONDS: float = 30.0
PASS_STREAK_REQUIRED: int = 2
VALID_RISKS = frozenset({"PASS", "BORDERLINE", "FAIL"})

ALLOWED_AUDIO_MIME_PREFIXES = ("audio/",)
ALLOWED_CONTAINER_MIMES = frozenset(
    {
        "video/webm",  # MediaRecorder sometimes tags audio-only WebM blobs as video/webm
        "application/ogg",
        "application/octet-stream",
        "",
    }
)

NEXT_STEP_BY_STATE: dict[str, str] = {
    "MONITORING": (
        "Keep monitoring the call and wait for two consecutive PASS chunks before approving. "
        "This is evidence, not proof."
    ),
    "PASS": (
        "Two consecutive chunks passed, but scores are evidence, not proof. "
        "Proceed with second-device number-matching approval only if the request makes sense."
    ),
    "BORDERLINE": (
        "Pause approval and call the claimed employee back on a number you already know "
        "before taking action. This is evidence, not proof."
    ),
    "FAIL": (
        "Request is blocked and cannot be overridden by approval. "
        "Do not share codes; call the employee back on a number you already know. "
        "This is evidence, not proof."
    ),
}


class LiveGuardError(ApprovalError):
    """Base error for Live Call Guard validation or workflow failures."""


class LiveGuardAuditError(RuntimeError):
    """Raised when hash-chained audit logging fails (must fail closed)."""


@dataclass
class _ChunkRecord:
    chunk_index: int
    received_at: str
    risk: str
    reason: str
    error: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_index": self.chunk_index,
            "received_at": self.received_at,
            "risk": self.risk,
            "reason": self.reason,
            "error": self.error,
        }


@dataclass
class _LiveSession:
    request_id: str
    claimed_employee_id: str
    claimed_employee_name: str
    known_callback_hint: str
    guard_actor_id: str
    max_chunks: int
    ai_mode: str
    active: bool = True
    state: str = "MONITORING"
    fail_latched: bool = False
    fail_alert_dispatched: bool = False
    pass_streak: int = 0
    chunks_processed: int = 0
    last_reason: str = (
        "Live Call Guard started with participant consent. Waiting for first 7-second audio chunk."
    )
    request_status: str = "PENDING_VOICE"
    telegram_notified: bool = False
    started_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    ended_at: str | None = None
    ended_reason: str | None = None
    chunks: list[_ChunkRecord] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def to_dict(self) -> dict[str, Any]:
        hint = self.known_callback_hint.strip()
        next_step = NEXT_STEP_BY_STATE.get(self.state, NEXT_STEP_BY_STATE["BORDERLINE"])
        if hint and self.state in ("BORDERLINE", "FAIL"):
            next_step = f"{next_step} (Known directory hint: {hint})"
        return {
            "request_id": self.request_id,
            "claimed_employee_id": self.claimed_employee_id,
            "claimed_employee_name": self.claimed_employee_name,
            "known_callback_hint": self.known_callback_hint,
            "active": self.active,
            "state": self.state,
            "fail_latched": self.fail_latched,
            "pass_streak": self.pass_streak,
            "chunks_processed": self.chunks_processed,
            "max_chunks": self.max_chunks,
            "last_reason": self.last_reason,
            "next_step": next_step,
            "request_status": self.request_status,
            "ai_mode": self.ai_mode,
            "telegram_notified": self.telegram_notified,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "ended_at": self.ended_at,
            "ended_reason": self.ended_reason,
            "chunks": [c.to_dict() for c in self.chunks],
        }


def _validate_content_type(content_type: str | None) -> None:
    ct = (content_type or "").split(";", 1)[0].strip().lower()
    if ct in ALLOWED_CONTAINER_MIMES:
        return
    if any(ct.startswith(prefix) for prefix in ALLOWED_AUDIO_MIME_PREFIXES):
        return
    raise UploadValidationError(
        f"Unsupported media type '{ct}'. Only audio files are accepted for Live Call Guard."
    )


def _safe_record_audit(
    conn: Any,
    *,
    event_type: str,
    actor_id: str,
    request_id: str,
    details: dict[str, Any],
) -> None:
    try:
        record_event(
            conn,
            event_type=event_type,
            actor_id=actor_id,
            request_id=request_id,
            details=details,
        )
    except Exception as exc:
        raise LiveGuardAuditError(f"Audit logging failed for {event_type}.") from exc


def _normalize_chunk_result(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {
            "aletheia": None,
            "eidos": None,
            "risk": "BORDERLINE",
            "reason": (
                "Voice verification returned an unexpected result and failed closed. "
                "This is evidence, not proof. Call back on a number you already know."
            ),
        }
    risk = raw.get("risk")
    reason = raw.get("reason")
    if risk not in VALID_RISKS or not isinstance(reason, str) or not reason.strip():
        return {
            "aletheia": raw.get("aletheia") if isinstance(raw.get("aletheia"), dict) else None,
            "eidos": raw.get("eidos") if isinstance(raw.get("eidos"), dict) else None,
            "risk": "BORDERLINE",
            "reason": (
                "Voice verification returned an incomplete result and failed closed. "
                "This is evidence, not proof. Call back on a number you already know."
            ),
        }
    return {
        "aletheia": raw.get("aletheia"),
        "eidos": raw.get("eidos"),
        "risk": risk,
        "reason": reason.strip()[:300],
    }


def _voice_source() -> str:
    try:
        return load_config().ai_mode
    except ConfigError:
        return "fixture"


def _default_telegram_notifier(
    request_id: str,
    state: str,
    last_result: dict[str, Any] | None,
    cfg: CoreConfig,
) -> bool:
    """Deliver live-call state to the existing Telegram bot without creating a second bot.

    1. If the bot's LiveCallAlertManager is running in the same process, call notify_live_state.
    2. Otherwise, if BOT_INTERNAL_TOKEN is configured, POST to the bot's localhost-only bridge.
    """
    manager = get_live_call_manager()
    if manager is not None:
        try:
            coro = notify_live_state(request_id, state, last_result)
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(coro)
                return True
            except RuntimeError:
                res = asyncio.run(coro)
                return bool(res.get("ok"))
        except Exception:
            logger.warning("In-process Telegram live-state notification failed for %s.", request_id)
            return False

    if not cfg.bot_internal_token:
        return False

    url = f"http://127.0.0.1:{cfg.bot_internal_port}/internal/live-state"
    safe_payload = {
        "request_id": request_id,
        "state": state,
        "last_result": last_result,
    }
    try:
        with httpx.Client(timeout=3.0) as client:
            resp = client.post(
                url,
                json=safe_payload,
                headers={"Authorization": f"Bearer {cfg.bot_internal_token}"},
            )
            if resp.status_code == 200:
                body = resp.json()
                return bool(body.get("ok"))
            logger.warning(
                "Bot internal bridge returned HTTP %d for %s.",
                resp.status_code,
                request_id,
            )
            return False
    except Exception:
        logger.warning("Could not reach Telegram bot internal bridge on port %d.", cfg.bot_internal_port)
        return False


class LiveGuardService:
    """Manages per-request near-live monitoring sessions, state machine, blocking, and audits."""

    def __init__(
        self,
        *,
        db_path: Path | None = None,
        config_loader: Callable[[], CoreConfig] = load_core_config,
        voice_verifier: Callable[[str, str], dict[str, Any]] = verify_voice,
        wav_converter: Callable[..., float] = convert_to_wav_16k_mono,
        telegram_notifier: Callable[[str, str, dict[str, Any] | None, CoreConfig], bool] | None = None,
    ) -> None:
        self._db_path = db_path
        self._config_loader = config_loader
        self._voice_verifier = voice_verifier
        self._wav_converter = wav_converter
        self._telegram_notifier = telegram_notifier or _default_telegram_notifier
        self._registry_lock = threading.Lock()
        self._sessions: dict[str, _LiveSession] = {}

    def _current_ai_mode(self) -> str:
        try:
            return load_config().ai_mode
        except ConfigError:
            return "misconfigured"

    def start_session(
        self,
        *,
        request_id: str,
        employee_id: str,
        consent_confirmed: bool,
        guard_actor_id: str | None = None,
    ) -> dict[str, Any]:
        if not consent_confirmed:
            raise LiveGuardError(
                "Explicit participant consent is required before starting Live Call Guard."
            )

        cfg = self._config_loader()
        ai_mode = self._current_ai_mode()
        actor = (guard_actor_id or "").strip() or "approver:live_guard"

        init_db(self._db_path)
        with get_db(self._db_path) as conn:
            req = get_request(conn, request_id)
            emp = get_employee(conn, employee_id)

            with self._registry_lock:
                existing = self._sessions.get(request_id)
                if existing is not None and existing.active:
                    raise ConflictError(
                        f"A Live Call Guard session is already active for request '{request_id}'."
                    )

                # Fail closed: record consent & session start BEFORE activating the session.
                _safe_record_audit(
                    conn,
                    event_type="LIVE_GUARD_SESSION_STARTED",
                    actor_id=actor,
                    request_id=request_id,
                    details={
                        "consent_confirmed": True,
                        "claimed_employee_id": emp["employee_id"],
                        "max_chunks": cfg.live_max_chunks,
                        "ai_mode": ai_mode,
                    },
                )

                session = _LiveSession(
                    request_id=request_id,
                    claimed_employee_id=emp["employee_id"],
                    claimed_employee_name=emp["display_name"],
                    known_callback_hint=emp["known_callback_hint"],
                    guard_actor_id=actor,
                    max_chunks=cfg.live_max_chunks,
                    ai_mode=ai_mode,
                    request_status=req["status"],
                )
                self._sessions[request_id] = session

        # Notify Telegram bot (single status message starts in MONITORING).
        notified = self._telegram_notifier(
            request_id,
            "MONITORING",
            {"reason": session.last_reason},
            cfg,
        )
        with session.lock:
            session.telegram_notified = notified
            return session.to_dict()

    def get_session(self, request_id: str) -> dict[str, Any]:
        with self._registry_lock:
            session = self._sessions.get(request_id)
        if session is None:
            raise NotFoundError(f"No Live Call Guard session found for request '{request_id}'.")

        # Sync latest request status from SQLite (e.g. if approver blocked via Telegram).
        try:
            with get_db(self._db_path) as conn:
                req = get_request(conn, request_id)
                latest_status = req["status"]
        except ApprovalError:
            latest_status = session.request_status

        with session.lock:
            session.request_status = latest_status
            return session.to_dict()

    def stop_session(
        self,
        *,
        request_id: str,
        reason: str = "Stopped manually by guard.",
    ) -> dict[str, Any]:
        with self._registry_lock:
            session = self._sessions.get(request_id)
        if session is None:
            raise NotFoundError(f"No Live Call Guard session found for request '{request_id}'.")

        with session.lock:
            if not session.active:
                return session.to_dict()

            now = utc_now_iso()
            with get_db(self._db_path) as conn:
                _safe_record_audit(
                    conn,
                    event_type="LIVE_GUARD_SESSION_ENDED",
                    actor_id=session.guard_actor_id,
                    request_id=request_id,
                    details={
                        "chunks_processed": session.chunks_processed,
                        "final_state": session.state,
                        "fail_latched": session.fail_latched,
                        "ended_reason": reason[:200],
                    },
                )
            session.active = False
            session.ended_at = now
            session.updated_at = now
            session.ended_reason = reason
            return session.to_dict()

    def process_chunk(
        self,
        *,
        request_id: str,
        employee_id: str,
        filename: str | None,
        content_type: str | None,
        stream: BinaryIO,
        fixture_scenario: str | None = None,
    ) -> dict[str, Any]:
        """Validate, convert to mono WAV via ffmpeg, verify, update state, block on FAIL, and delete temp audio."""
        with self._registry_lock:
            session = self._sessions.get(request_id)
        if session is None:
            raise NotFoundError(
                f"No Live Call Guard session found for request '{request_id}'. Start a session first."
            )

        with session.lock:
            if not session.active:
                return {
                    "ignored": True,
                    "message": "Session has already ended; chunk ignored.",
                    "chunk": None,
                    "session": session.to_dict(),
                    "source": _voice_source(),
                }
            if session.chunks_processed >= session.max_chunks:
                now = utc_now_iso()
                cap_msg = f"Stopped automatically after reaching the cap of {session.max_chunks} chunks."
                with get_db(self._db_path) as conn:
                    _safe_record_audit(
                        conn,
                        event_type="LIVE_GUARD_SESSION_ENDED",
                        actor_id=session.guard_actor_id,
                        request_id=request_id,
                        details={
                            "chunks_processed": session.chunks_processed,
                            "final_state": session.state,
                            "fail_latched": session.fail_latched,
                            "ended_reason": cap_msg,
                        },
                    )
                session.active = False
                session.ended_at = now
                session.updated_at = now
                session.ended_reason = cap_msg
                return {
                    "ignored": True,
                    "message": cap_msg,
                    "chunk": None,
                    "session": session.to_dict(),
                    "source": _voice_source(),
                }

        cfg = self._config_loader()

        # Reject non-audio files and enforce chunk upload size cap BEFORE touching ffmpeg or AI.
        _validate_content_type(content_type)
        saved_raw: Path | None = None
        converted_wav: Path | None = None
        chunk_error = False

        try:
            saved_raw = save_audio_upload(
                filename=filename,
                stream=stream,
                upload_dir=cfg.upload_dir,
                max_bytes=cfg.live_max_chunk_bytes,
            )

            # Convert uploaded chunk to 16 kHz mono WAV via ffmpeg inside confined upload_dir.
            base_dir = cfg.upload_dir.resolve()
            converted_wav = (base_dir / f"{uuid.uuid4().hex}_mono.wav").resolve()
            if not converted_wav.is_relative_to(base_dir):
                raise UploadValidationError("Invalid converted audio path.")

            try:
                self._wav_converter(
                    saved_raw,
                    converted_wav,
                    min_duration=LIVE_MIN_CHUNK_SECONDS,
                    max_duration=LIVE_MAX_CHUNK_SECONDS,
                )
            except (AudioConversionError, AudioDurationError) as exc:
                chunk_error = True
                result = {
                    "aletheia": None,
                    "eidos": None,
                    "risk": "BORDERLINE",
                    "reason": (
                        f"Chunk audio processing failed ({exc}); counted as BORDERLINE (fail closed). "
                        "This is evidence, not proof. Call back on a number you already know."
                    ),
                }
            else:
                result = self._run_voice_check(
                    wav_path=converted_wav,
                    employee_id=employee_id,
                    fixture_scenario=fixture_scenario,
                    cfg=cfg,
                )
        finally:
            # Always delete temporary chunk audio immediately after verification.
            if saved_raw is not None:
                saved_raw.unlink(missing_ok=True)
            if converted_wav is not None:
                converted_wav.unlink(missing_ok=True)

        return self._apply_chunk_result(
            session=session,
            result=result,
            chunk_error=chunk_error,
            cfg=cfg,
        )

    def _run_voice_check(
        self,
        *,
        wav_path: Path,
        employee_id: str,
        fixture_scenario: str | None,
        cfg: CoreConfig,
    ) -> dict[str, Any]:
        """Call verify_voice (honouring AI_MODE; supports fixture scenario demo override without changing format)."""
        try:
            ai_cfg = load_config()
            if ai_cfg.ai_mode == "fixture":
                chosen = (fixture_scenario or "").strip() or cfg.live_fixture_scenario
                if chosen:
                    return _normalize_chunk_result(_load_fixture(chosen))
        except ConfigError:
            pass

        try:
            raw = self._voice_verifier(str(wav_path), employee_id)
        except Exception:
            return {
                "aletheia": None,
                "eidos": None,
                "risk": "BORDERLINE",
                "reason": (
                    "Voice verification raised an error and failed closed as BORDERLINE. "
                    "This is evidence, not proof. Call back on a number you already know."
                ),
            }
        return _normalize_chunk_result(raw)

    def _apply_chunk_result(
        self,
        *,
        session: _LiveSession,
        result: dict[str, Any],
        chunk_error: bool,
        cfg: CoreConfig,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        chunk_risk = result["risk"]
        chunk_reason = result["reason"]

        should_notify_telegram = False
        first_fail_triggered = False
        new_state = session.state

        with session.lock:
            if not session.active:
                return {
                    "ignored": True,
                    "message": "Session ended while chunk was in flight; result ignored.",
                    "chunk": None,
                    "session": session.to_dict(),
                    "source": _voice_source(),
                }

            previous_state = session.state
            chunk_index = session.chunks_processed + 1

            # State machine rules:
            # 1. Any FAIL latches FAIL for the rest of the session.
            # 2. PASS needs 2 consecutive PASS chunks.
            # 3. BORDERLINE (or chunk error) resets PASS streak and sets BORDERLINE.
            if session.fail_latched:
                next_state = "FAIL"
                next_streak = 0
                next_fail_latched = True
            elif chunk_risk == "FAIL":
                next_state = "FAIL"
                next_streak = 0
                next_fail_latched = True
            elif chunk_risk == "PASS" and not chunk_error:
                next_streak = session.pass_streak + 1
                next_state = "PASS" if next_streak >= PASS_STREAK_REQUIRED else previous_state
                next_fail_latched = False
            else:
                next_streak = 0
                next_state = "BORDERLINE"
                next_fail_latched = False

            state_changed = next_state != previous_state
            first_fail_triggered = next_fail_latched and not session.fail_alert_dispatched
            cap_reached = chunk_index >= session.max_chunks
            cap_msg = f"Reached maximum cap of {session.max_chunks} chunks; monitoring stopped."

            # Fail closed: commit audit trail (and request block on FAIL) BEFORE updating memory/UI.
            with get_db(self._db_path) as conn:
                _safe_record_audit(
                    conn,
                    event_type="LIVE_GUARD_CHUNK_RESULT",
                    actor_id=session.guard_actor_id,
                    request_id=session.request_id,
                    details={
                        "chunk_index": chunk_index,
                        "risk": chunk_risk,
                        "reason": chunk_reason[:300],
                    },
                )

                if state_changed:
                    _safe_record_audit(
                        conn,
                        event_type="LIVE_GUARD_STATE_CHANGED",
                        actor_id=session.guard_actor_id,
                        request_id=session.request_id,
                        details={
                            "previous_state": previous_state,
                            "new_state": next_state,
                            "fail_latched": next_fail_latched,
                        },
                    )

                latest_req_status = session.request_status
                if first_fail_triggered:
                    try:
                        blocked_req = block_request_on_voice_fail(
                            conn,
                            request_id=session.request_id,
                            reason=f"Live Call Guard FAIL: {chunk_reason}",
                            actor_id=session.guard_actor_id,
                        )
                        latest_req_status = blocked_req["status"]
                    except ConflictError:
                        # Already executed; keep existing status, still latch FAIL.
                        latest_req_status = get_request(conn, session.request_id)["status"]
                else:
                    latest_req_status = get_request(conn, session.request_id)["status"]

                if cap_reached:
                    _safe_record_audit(
                        conn,
                        event_type="LIVE_GUARD_SESSION_ENDED",
                        actor_id=session.guard_actor_id,
                        request_id=session.request_id,
                        details={
                            "chunks_processed": chunk_index,
                            "final_state": next_state,
                            "fail_latched": next_fail_latched,
                            "ended_reason": cap_msg,
                        },
                    )

            # Commit in-memory session state after DB/audit succeeded.
            record = _ChunkRecord(
                chunk_index=chunk_index,
                received_at=now,
                risk=chunk_risk,
                reason=chunk_reason,
                error=chunk_error,
            )
            session.chunks.append(record)
            session.chunks_processed = chunk_index
            session.state = next_state
            session.pass_streak = next_streak
            session.fail_latched = next_fail_latched
            session.last_reason = chunk_reason
            session.request_status = latest_req_status
            session.updated_at = now
            if cap_reached:
                session.active = False
                session.ended_at = now
                session.ended_reason = cap_msg

            should_notify_telegram = state_changed or first_fail_triggered
            new_state = next_state
            if first_fail_triggered:
                session.fail_alert_dispatched = True

        # Push Telegram updates outside the session lock so network I/O never blocks other chunks.
        if should_notify_telegram:
            delivered = self._telegram_notifier(
                session.request_id,
                new_state,
                {
                    "aletheia": result.get("aletheia"),
                    "eidos": result.get("eidos"),
                    "risk": chunk_risk,
                    "reason": chunk_reason,
                },
                cfg,
            )
            with session.lock:
                session.telegram_notified = delivered or session.telegram_notified
            if first_fail_triggered:
                with get_db(self._db_path) as conn:
                    _safe_record_audit(
                        conn,
                        event_type="LIVE_GUARD_FAIL_ALERT_SENT",
                        actor_id=session.guard_actor_id,
                        request_id=session.request_id,
                        details={
                            "telegram_delivered": delivered,
                            "request_blocked": session.request_status == "BLOCKED",
                        },
                    )

        with session.lock:
            msg = (
                session.ended_reason
                if not session.active and session.ended_reason
                else f"Chunk #{record.chunk_index} processed ({record.risk})."
            )
            return {
                "ignored": False,
                "message": msg,
                "chunk": record.to_dict(),
                "session": session.to_dict(),
                "source": _voice_source(),
            }


_default_service = LiveGuardService()


def get_live_guard_service() -> LiveGuardService:
    return _default_service
