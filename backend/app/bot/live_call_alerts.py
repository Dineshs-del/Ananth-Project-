"""Live-call Telegram alert manager for Break-Glass Voice Gate.

Behaviour:
- Sends ONE status message per (request_id, approver chat) when a live-call session starts,
  and edits that same message in place as risk changes (MONITORING, PASS, BORDERLINE, FAIL).
- Latches FAIL for the rest of the call (cannot downgrade back to PASS/BORDERLINE/MONITORING).
- On the first FAIL, sends ONE separate alert message per approver chat with two signed buttons:
  "Block" (marks the request blocked via existing approval.service.block_request) and
  "Call back on known number" (replies with plain instructions).
- Includes a plain next step and "This is evidence, not proof." in every message.
- Never displays or logs raw audio, file paths, or secrets.
- Handles Telegram RetryAfter rate limits (coalesced deferred edits) and
  "Message is not modified" errors gracefully.
- Fail closed on audit logging failures and unknown states.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, RetryAfter, TelegramError
from telegram.ext import Application, ContextTypes

from ..approval.service import ApprovalError, block_request, get_request
from ..core.database import get_db, init_db
from .audit_bridge import AuditLoggingError, log_block, log_live_call_state

logger = logging.getLogger("voicegate.bot.live_call")

VALID_LIVE_STATES = frozenset({"MONITORING", "PASS", "BORDERLINE", "FAIL"})
MAX_TELEGRAM_CALLBACK_BYTES: int = 64
DEFAULT_MIN_EDIT_INTERVAL_SECONDS: float = 1.5

EVIDENCE_NOTICE = "This is evidence, not proof."
CALLBACK_INSTRUCTIONS = (
    "Do not approve this request or share codes. "
    "Call the requester back on a number you already know."
)

NEXT_STEP_BY_STATE: dict[str, str] = {
    "MONITORING": (
        "Next step: Keep the call under review and wait for voice verification checks "
        "before approving."
    ),
    "PASS": (
        "Next step: Voice checks look consistent so far, but scores are not proof. "
        "Continue with second-device approval only if the request makes sense."
    ),
    "BORDERLINE": (
        "Next step: Pause approval and call the requester back on a number you already know "
        "before taking action."
    ),
    "FAIL": (
        "Next step: Do not approve this request or share codes. "
        "Tap Block and call the requester back on a number you already know."
    ),
}

# Active module-level manager registered when the Telegram bot initializes.
_active_manager: LiveCallAlertManager | None = None


def register_live_call_manager(manager: LiveCallAlertManager | None) -> None:
    """Register (or clear) the active LiveCallAlertManager for notify_live_state."""
    global _active_manager
    _active_manager = manager


def get_live_call_manager() -> LiveCallAlertManager | None:
    """Return the currently registered LiveCallAlertManager, if any."""
    return _active_manager


def _format_score(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "unavailable"
    return f"{float(value):.2f} (0 to 1)"


def _normalize_state(state: Any, last_result: dict[str, Any] | None) -> str:
    """Normalize incoming state; fail closed to BORDERLINE/FAIL on unknown or FAIL evidence."""
    normalized = str(state or "").strip().upper()
    if normalized not in VALID_LIVE_STATES:
        normalized = "BORDERLINE"

    # Never allow a FAIL result payload to downgrade to a non-FAIL state.
    if isinstance(last_result, dict):
        result_risk = str(last_result.get("risk") or "").strip().upper()
        if result_risk == "FAIL":
            return "FAIL"
        if result_risk == "BORDERLINE" and normalized == "PASS":
            return "BORDERLINE"

    return normalized


def _extract_safe_result(last_result: Any) -> dict[str, Any]:
    """Extract only safe display fields from last_result. Never touches raw audio or secrets."""
    if not isinstance(last_result, dict):
        return {"reason": "", "aletheia": None, "eidos": None}

    raw_reason = last_result.get("reason")
    reason = str(raw_reason).strip()[:300] if isinstance(raw_reason, str) else ""

    aletheia_out: dict[str, Any] | None = None
    raw_aletheia = last_result.get("aletheia")
    if isinstance(raw_aletheia, dict):
        verdict = raw_aletheia.get("verdict")
        score = raw_aletheia.get("score")
        aletheia_out = {
            "verdict": str(verdict)[:64] if verdict is not None else "unavailable",
            "score": score if not isinstance(score, bool) and isinstance(score, (int, float)) else None,
        }

    eidos_out: dict[str, Any] | None = None
    raw_eidos = last_result.get("eidos")
    if isinstance(raw_eidos, dict):
        decision = raw_eidos.get("decision")
        similarity = raw_eidos.get("similarity")
        eidos_out = {
            "decision": str(decision)[:64] if decision is not None else "unavailable",
            "similarity": (
                similarity
                if not isinstance(similarity, bool) and isinstance(similarity, (int, float))
                else None
            ),
        }

    return {
        "reason": reason,
        "aletheia": aletheia_out,
        "eidos": eidos_out,
    }


def _retry_after_seconds(exc: RetryAfter) -> float:
    raw = getattr(exc, "retry_after", 1.0)
    if hasattr(raw, "total_seconds"):
        return max(0.05, float(raw.total_seconds()))
    try:
        return max(0.05, float(raw))
    except (TypeError, ValueError):
        return 1.0


class LiveCallCallbackSigner:
    """HMAC-SHA256 signer for compact, unforgeable live-call FAIL alert buttons."""

    def __init__(self, secret_key: bytes | None = None) -> None:
        if secret_key is not None:
            self._key = secret_key
        else:
            env_secret = os.getenv("BOT_CALLBACK_SECRET", "").strip()
            self._key = env_secret.encode("utf-8") if env_secret else secrets.token_bytes(32)

    def _digest(self, *, op: str, request_id: str, chat_id: int) -> str:
        msg = f"live|{op}|{request_id}|{chat_id}".encode("utf-8")
        return hmac.new(self._key, msg, hashlib.sha256).hexdigest()[:16]

    def sign(self, *, op: str, request_id: str, chat_id: int) -> str:
        if op not in ("lb", "lc"):
            raise ValueError(f"Unsupported live-call callback op: {op}")
        sig = self._digest(op=op, request_id=request_id, chat_id=chat_id)
        payload = f"{op}:{request_id}:-:{sig}"
        if len(payload.encode("utf-8")) > MAX_TELEGRAM_CALLBACK_BYTES:
            raise ValueError("Live-call callback payload exceeded Telegram 64-byte limit.")
        return payload

    def verify(self, payload: str, *, chat_id: int) -> tuple[str, str] | None:
        if not isinstance(payload, str):
            return None
        parts = payload.split(":")
        if len(parts) != 4:
            return None
        op, request_id, marker, provided_sig = parts
        if op not in ("lb", "lc") or not request_id or marker != "-" or len(provided_sig) != 16:
            return None
        expected_sig = self._digest(op=op, request_id=request_id, chat_id=chat_id)
        if not hmac.compare_digest(provided_sig, expected_sig):
            return None
        return op, request_id


@dataclass
class _ChatMessageState:
    status_message_id: int | None = None
    last_sent_text: str = ""
    last_edit_monotonic: float = 0.0
    rate_limited_until: float = 0.0
    pending_text: str | None = None
    pending_task: asyncio.Task[None] | None = None
    fail_alert_message_id: int | None = None


@dataclass
class _LiveCallSession:
    request_id: str
    effective_state: str = "MONITORING"
    fail_latched: bool = False
    fail_reason: str = ""
    last_safe_result: dict[str, Any] = field(default_factory=dict)
    blocked_by: str | None = None
    chats: dict[int, _ChatMessageState] = field(default_factory=dict)


def format_live_status_message(
    *,
    request_id: str,
    state: str,
    safe_result: dict[str, Any],
    fail_latched: bool,
    req: dict[str, Any] | None = None,
    blocked_by: str | None = None,
) -> str:
    """Format the single live-call status message edited in place for approvers."""
    state_label = "FAIL (LATCHED)" if fail_latched else state
    lines = [
        f"LIVE-CALL VOICE MONITOR — {state_label}",
        f"Request: {request_id}",
    ]

    if req is not None:
        requester = req.get("employee_name") or req.get("employee_id") or "Unknown"
        lines.append(f"Requester: {requester} ({req['employee_id']})")
        lines.append(f"Action: {req['action_summary']}")

    lines.append("")
    lines.append(f"Current state: {state_label}")

    aletheia = safe_result.get("aletheia")
    if isinstance(aletheia, dict):
        lines.append(
            f"Aletheia: verdict={aletheia.get('verdict', 'unavailable')}, "
            f"score={_format_score(aletheia.get('score'))}"
        )
    eidos = safe_result.get("eidos")
    if isinstance(eidos, dict):
        lines.append(
            f"Eidos: decision={eidos.get('decision', 'unavailable')}, "
            f"similarity={_format_score(eidos.get('similarity'))}"
        )

    reason = str(safe_result.get("reason") or "").strip()
    if reason:
        lines.append(f"Reason: {reason}")

    if fail_latched:
        lines.append("Notice: FAIL is latched for the rest of this call and cannot be cleared.")

    if blocked_by:
        lines.append(f"Outcome: BLOCKED by {blocked_by}.")

    lines.append(EVIDENCE_NOTICE)

    hint = str((req or {}).get("known_callback_hint") or "").strip()
    next_step = NEXT_STEP_BY_STATE.get(state, NEXT_STEP_BY_STATE["BORDERLINE"])
    if hint and state in ("BORDERLINE", "FAIL"):
        next_step = f"{next_step} (Known directory hint: {hint})"
    lines.append(next_step)

    return "\n".join(lines)


def format_live_fail_alert_message(
    *,
    request_id: str,
    safe_result: dict[str, Any],
    req: dict[str, Any] | None = None,
    outcome: str | None = None,
) -> str:
    """Format the one-time separate FAIL alert message with plain next steps."""
    lines = [
        "ALERT: LIVE-CALL VOICE CHECK FAILED (LATCHED)",
        f"Request: {request_id}",
    ]
    if req is not None:
        requester = req.get("employee_name") or req.get("employee_id") or "Unknown"
        lines.append(f"Requester: {requester} ({req['employee_id']})")
        lines.append(f"Action: {req['action_summary']}")

    reason = str(safe_result.get("reason") or "").strip() or (
        "Live-call voice check triggered a hard FAIL."
    )
    lines.extend(
        [
            "",
            f"Reason: {reason}",
            "FAIL is latched for the rest of this call and cannot be overridden by approval.",
            EVIDENCE_NOTICE,
        ]
    )

    hint = str((req or {}).get("known_callback_hint") or "").strip()
    next_step = NEXT_STEP_BY_STATE["FAIL"]
    if hint:
        next_step = f"{next_step} (Known directory hint: {hint})"
    lines.append(next_step)

    if outcome:
        lines.append("")
        lines.append(f"Outcome: {outcome}")

    return "\n".join(lines)


def build_live_fail_keyboard(
    *,
    request_id: str,
    chat_id: int,
    signer: LiveCallCallbackSigner,
) -> InlineKeyboardMarkup:
    """Build the two-button keyboard for a live-call FAIL alert (NO approve buttons)."""
    block_cb = signer.sign(op="lb", request_id=request_id, chat_id=chat_id)
    callback_cb = signer.sign(op="lc", request_id=request_id, chat_id=chat_id)
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Block", callback_data=block_cb),
                InlineKeyboardButton("Call back on known number", callback_data=callback_cb),
            ]
        ]
    )


class LiveCallAlertManager:
    """Manages live-call status messages, FAIL latching, one-time alerts, and rate-limited edits."""

    def __init__(
        self,
        *,
        approver_chats_ref: dict[int, str],
        db_path: Path | None = None,
        bot: Any = None,
        signer: LiveCallCallbackSigner | None = None,
        min_edit_interval: float = DEFAULT_MIN_EDIT_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._approver_chats = approver_chats_ref
        self._db_path = db_path
        self._bot = bot
        self._signer = signer or LiveCallCallbackSigner()
        self._min_edit_interval = max(0.0, float(min_edit_interval))
        self._clock = clock
        self._sessions: dict[str, _LiveCallSession] = {}

    def attach_bot(self, bot: Any) -> None:
        """Bind the running Telegram Bot instance (called from post_init)."""
        self._bot = bot

    async def start(self, app: Application) -> None:
        self.attach_bot(app.bot)
        register_live_call_manager(self)

    async def stop(self, _app: Application | None = None) -> None:
        """Cancel any pending rate-limited deferred edit tasks cleanly."""
        for session in self._sessions.values():
            for chat_state in session.chats.values():
                if chat_state.pending_task is not None and not chat_state.pending_task.done():
                    chat_state.pending_task.cancel()
                chat_state.pending_task = None
        if get_live_call_manager() is self:
            register_live_call_manager(None)

    def _load_request_optional(self, request_id: str) -> dict[str, Any] | None:
        try:
            init_db(self._db_path)
            with get_db(self._db_path) as conn:
                return get_request(conn, request_id)
        except ApprovalError:
            return None
        except Exception:
            logger.warning("Could not load request metadata for live-call alert %s.", request_id)
            return None

    async def notify_live_state(
        self,
        request_id: str,
        state: str,
        last_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Update the live-call status message and trigger a one-time FAIL alert if needed."""
        clean_request_id = str(request_id or "").strip()
        if not clean_request_id:
            raise ValueError("request_id is required for notify_live_state.")

        session = self._sessions.get(clean_request_id)
        if session is None:
            session = _LiveCallSession(request_id=clean_request_id)
            self._sessions[clean_request_id] = session

        incoming_state = _normalize_state(state, last_result)
        safe_result = _extract_safe_result(last_result)

        # Latch FAIL for the rest of the call.
        if session.fail_latched or incoming_state == "FAIL":
            if not session.fail_latched:
                session.fail_latched = True
                session.fail_reason = (
                    safe_result["reason"] or "Live-call voice check triggered a hard FAIL."
                )
            elif incoming_state == "FAIL" and safe_result["reason"]:
                session.fail_reason = safe_result["reason"]
            effective_state = "FAIL"
            safe_result = {
                **safe_result,
                "reason": session.fail_reason,
            }
        else:
            effective_state = incoming_state

        previous_state = session.effective_state
        session.effective_state = effective_state
        session.last_safe_result = safe_result

        # Fail closed: record live-call state transition in the existing audit chain
        # BEFORE sending/editing Telegram messages.
        if effective_state != previous_state or not session.chats:
            await asyncio.to_thread(
                log_live_call_state,
                actor_id="system:live_call",
                request_id=clean_request_id,
                state=effective_state,
                reason=safe_result.get("reason") or f"Live-call state: {effective_state}",
                fail_latched=session.fail_latched,
                db_path=self._db_path,
            )

        if self._bot is None or not self._approver_chats:
            return {
                "ok": False,
                "request_id": clean_request_id,
                "effective_state": effective_state,
                "fail_latched": session.fail_latched,
                "status_updates": 0,
                "fail_alerts_sent": 0,
                "reason": "No Telegram bot or registered approver chat is available.",
            }

        req = await asyncio.to_thread(self._load_request_optional, clean_request_id)
        status_text = format_live_status_message(
            request_id=clean_request_id,
            state=effective_state,
            safe_result=safe_result,
            fail_latched=session.fail_latched,
            req=req,
            blocked_by=session.blocked_by,
        )

        status_updates = 0
        fail_alerts_sent = 0

        for chat_id in list(self._approver_chats.keys()):
            chat_state = session.chats.setdefault(chat_id, _ChatMessageState())

            if chat_state.status_message_id is None:
                sent = await self._send_initial_status(
                    chat_id=chat_id,
                    text=status_text,
                    chat_state=chat_state,
                )
                if sent:
                    status_updates += 1
            else:
                edited = await self._edit_status_with_rate_limit(
                    chat_id=chat_id,
                    text=status_text,
                    chat_state=chat_state,
                    urgent=(effective_state == "FAIL"),
                )
                if edited:
                    status_updates += 1

            # On the FIRST FAIL for this chat, send ONE separate alert message with buttons.
            if session.fail_latched and chat_state.fail_alert_message_id is None:
                alert_text = format_live_fail_alert_message(
                    request_id=clean_request_id,
                    safe_result=safe_result,
                    req=req,
                )
                keyboard = build_live_fail_keyboard(
                    request_id=clean_request_id,
                    chat_id=chat_id,
                    signer=self._signer,
                )
                try:
                    alert_msg = await self._bot.send_message(
                        chat_id=chat_id,
                        text=alert_text,
                        reply_markup=keyboard,
                    )
                    chat_state.fail_alert_message_id = alert_msg.message_id
                    fail_alerts_sent += 1
                except TelegramError:
                    logger.warning(
                        "Failed to send live-call FAIL alert for %s to chat %s.",
                        clean_request_id,
                        chat_id,
                    )

        return {
            "ok": True,
            "request_id": clean_request_id,
            "effective_state": effective_state,
            "fail_latched": session.fail_latched,
            "status_updates": status_updates,
            "fail_alerts_sent": fail_alerts_sent,
        }

    async def _send_initial_status(
        self,
        *,
        chat_id: int,
        text: str,
        chat_state: _ChatMessageState,
    ) -> bool:
        try:
            sent = await self._bot.send_message(chat_id=chat_id, text=text)
            chat_state.status_message_id = sent.message_id
            chat_state.last_sent_text = text
            chat_state.last_edit_monotonic = self._clock()
            return True
        except TelegramError:
            logger.warning("Failed to send initial live-call status to chat %s.", chat_id)
            return False

    async def _edit_status_with_rate_limit(
        self,
        *,
        chat_id: int,
        text: str,
        chat_state: _ChatMessageState,
        urgent: bool = False,
    ) -> bool:
        """Edit the single status message while respecting rate limits and ignoring no-op edits."""
        if chat_state.status_message_id is None:
            return False

        # Skip redundant edits when text hasn't changed.
        if text == chat_state.last_sent_text and chat_state.pending_text is None:
            return False

        now = self._clock()

        # Respect any active Telegram RetryAfter cooldown strictly.
        if now < chat_state.rate_limited_until:
            wait = max(0.05, chat_state.rate_limited_until - now)
            self._schedule_deferred_edit(chat_id=chat_id, chat_state=chat_state, text=text, delay=wait)
            return False

        # Coalesce rapid non-urgent edits to stay below Telegram edit limits.
        if not urgent and self._min_edit_interval > 0:
            elapsed = now - chat_state.last_edit_monotonic
            if elapsed < self._min_edit_interval:
                wait = max(0.05, self._min_edit_interval - elapsed)
                self._schedule_deferred_edit(chat_id=chat_id, chat_state=chat_state, text=text, delay=wait)
                return False

        return await self._perform_edit(chat_id=chat_id, chat_state=chat_state, text=text)

    def _schedule_deferred_edit(
        self,
        *,
        chat_id: int,
        chat_state: _ChatMessageState,
        text: str,
        delay: float,
    ) -> None:
        chat_state.pending_text = text
        if chat_state.pending_task is not None and not chat_state.pending_task.done():
            return
        chat_state.pending_task = asyncio.create_task(
            self._flush_deferred_edit(chat_id=chat_id, chat_state=chat_state, delay=delay)
        )

    async def _flush_deferred_edit(
        self,
        *,
        chat_id: int,
        chat_state: _ChatMessageState,
        delay: float,
    ) -> None:
        try:
            await asyncio.sleep(delay)
            while chat_state.pending_text is not None:
                next_text = chat_state.pending_text
                chat_state.pending_text = None
                now = self._clock()
                if now < chat_state.rate_limited_until:
                    await asyncio.sleep(max(0.05, chat_state.rate_limited_until - now))
                await self._perform_edit(chat_id=chat_id, chat_state=chat_state, text=next_text)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.warning("Deferred live-call status edit failed for chat %s.", chat_id)
        finally:
            chat_state.pending_task = None

    async def _perform_edit(
        self,
        *,
        chat_id: int,
        chat_state: _ChatMessageState,
        text: str,
    ) -> bool:
        if chat_state.status_message_id is None or self._bot is None:
            return False
        if text == chat_state.last_sent_text:
            return False
        try:
            await self._bot.edit_message_text(
                chat_id=chat_id,
                message_id=chat_state.status_message_id,
                text=text,
            )
            chat_state.last_sent_text = text
            chat_state.last_edit_monotonic = self._clock()
            return True
        except RetryAfter as exc:
            wait = _retry_after_seconds(exc)
            chat_state.rate_limited_until = self._clock() + wait
            logger.warning(
                "Telegram edit rate limit hit for chat %s; deferring edit by %.2fs.",
                chat_id,
                wait,
            )
            self._schedule_deferred_edit(chat_id=chat_id, chat_state=chat_state, text=text, delay=wait)
            return False
        except BadRequest as exc:
            if "message is not modified" in str(exc).lower():
                chat_state.last_sent_text = text
                return False
            logger.warning("Telegram live-call edit failed: %s", exc)
            return False
        except TelegramError:
            logger.warning("Telegram live-call edit failed for chat %s.", chat_id)
            return False

    async def handle_callback_query(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle signed 'Block' ('lb') and 'Call back on known number' ('lc') buttons."""
        query = update.callback_query
        if query is None or not query.data:
            return

        msg = query.message
        chat_id = msg.chat_id if msg is not None else None
        message_id = msg.message_id if msg is not None else None
        if chat_id is None or message_id is None:
            await query.answer("Invalid message context.", show_alert=True)
            return

        reviewer_id = self._approver_chats.get(chat_id)
        if not reviewer_id:
            await query.answer(
                "This chat is not registered. Run /approver <employee_id> first.",
                show_alert=True,
            )
            return

        verified = self._signer.verify(query.data, chat_id=chat_id)
        if verified is None:
            await query.answer("Forged or stale alert button rejected.", show_alert=True)
            return

        op, request_id = verified
        req = await asyncio.to_thread(self._load_request_optional, request_id)

        if op == "lc":
            hint = str((req or {}).get("known_callback_hint") or "").strip()
            hint_line = f"\nKnown directory hint: {hint}" if hint else ""
            await query.answer("Callback instructions sent.")
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"Callback instructions for {request_id}:\n"
                    f"{CALLBACK_INSTRUCTIONS}{hint_line}\n"
                    f"{EVIDENCE_NOTICE}"
                ),
            )
            return

        if op == "lb":
            session = self._sessions.get(request_id)
            if session is not None and session.blocked_by is not None:
                await query.answer(
                    f"Request already blocked by {session.blocked_by}.",
                    show_alert=True,
                )
                return

            def _block_and_audit() -> dict[str, Any]:
                init_db(self._db_path)
                with get_db(self._db_path) as conn:
                    updated = block_request(
                        conn,
                        request_id=request_id,
                        reviewer_id=reviewer_id,
                        reason="Blocked via Telegram live-call FAIL alert.",
                    )
                    log_block(
                        actor_id=reviewer_id,
                        request_id=request_id,
                        channel="telegram_live_call",
                        conn=conn,
                    )
                    return updated

            try:
                updated_req = await asyncio.to_thread(_block_and_audit)
            except ApprovalError as exc:
                await query.answer(str(exc)[:200], show_alert=True)
                return
            except AuditLoggingError:
                await query.answer(
                    "Audit logging failed; block did not proceed.",
                    show_alert=True,
                )
                return

            if session is not None:
                session.blocked_by = reviewer_id
                session.fail_latched = True
                session.effective_state = "FAIL"

            safe_result = session.last_safe_result if session is not None else {"reason": ""}
            alert_text = format_live_fail_alert_message(
                request_id=request_id,
                safe_result=safe_result,
                req=updated_req,
                outcome=f"BLOCKED by {reviewer_id}. Hard FAIL cannot be overridden.",
            )
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=alert_text,
                    reply_markup=None,
                )
            except BadRequest as exc:
                if "message is not modified" not in str(exc).lower():
                    logger.warning("Could not edit live-call FAIL alert: %s", exc)
            except TelegramError:
                logger.warning("Could not edit live-call FAIL alert in chat %s.", chat_id)

            # Also refresh the single status message to show BLOCKED.
            if session is not None and chat_id in session.chats:
                status_text = format_live_status_message(
                    request_id=request_id,
                    state="FAIL",
                    safe_result=safe_result,
                    fail_latched=True,
                    req=updated_req,
                    blocked_by=reviewer_id,
                )
                await self._edit_status_with_rate_limit(
                    chat_id=chat_id,
                    text=status_text,
                    chat_state=session.chats[chat_id],
                    urgent=True,
                )

            await query.answer("Request marked BLOCKED.")
            return


async def notify_live_state(
    request_id: str,
    state: str,
    last_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Small entry point for the live-call backend to push live risk states to the bot."""
    manager = get_live_call_manager()
    if manager is None:
        return {
            "ok": False,
            "request_id": str(request_id or ""),
            "effective_state": _normalize_state(state, last_result),
            "fail_latched": _normalize_state(state, last_result) == "FAIL",
            "status_updates": 0,
            "fail_alerts_sent": 0,
            "reason": "Telegram live-call alert manager is not initialized.",
        }
    return await manager.notify_live_state(request_id, state, last_result)
