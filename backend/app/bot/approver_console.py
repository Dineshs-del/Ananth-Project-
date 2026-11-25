"""Telegram approver console for Break-Glass Voice Gate.

Reuses existing backend services (approval.service, audit.service, core.database)
without inventing new HTTP routes or altering existing schemas.
All audit writes are fail-closed: if audit logging fails, the action does not proceed.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import Application, ContextTypes

from ..approval.service import (
    APPROVER_ROLES,
    ApprovalError,
    decide_approval,
    execute_request,
    get_approval,
    get_employee,
    get_request,
    list_employees,
    list_requests,
)
from ..audit.service import record_event
from ..core.database import get_db, init_db
from .audit_bridge import (
    AuditLoggingError,
    build_audit_report,
    log_approval_decision,
    log_approval_shown,
    log_block,
    log_expiry,
)

logger = logging.getLogger("voicegate.bot.approver")

EVIDENCE_NOTICE = "This is evidence, not proof."
CALLBACK_INSTRUCTIONS = (
    "Do not approve this request or share codes. "
    "Call the requester back on a number you already know."
)
POLL_INTERVAL_SECONDS: float = 2.0
COUNTDOWN_STEP_SECONDS: int = 10
MAX_TELEGRAM_CALLBACK_BYTES: int = 64


def _remaining_seconds(iso_timestamp: str | None) -> int:
    if not iso_timestamp:
        return 0
    expires = datetime.fromisoformat(iso_timestamp)
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    delta = int((expires - datetime.now(timezone.utc)).total_seconds())
    return max(0, delta)


def _format_score(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "unavailable"
    return f"{float(value):.2f} (0 to 1)"


class CallbackSigner:
    """HMAC-SHA256 signer for compact, unforgeable Telegram inline callback data."""

    def __init__(self, secret_key: bytes | None = None) -> None:
        if secret_key is not None:
            self._key = secret_key
        else:
            env_secret = os.getenv("BOT_CALLBACK_SECRET", "").strip()
            self._key = env_secret.encode("utf-8") if env_secret else secrets.token_bytes(32)

    def _digest(
        self,
        *,
        op: str,
        request_id: str,
        approval_id: str | None,
        value: str,
        chat_id: int,
    ) -> str:
        msg = f"{op}|{request_id}|{approval_id or 'none'}|{value}|{chat_id}".encode("utf-8")
        return hmac.new(self._key, msg, hashlib.sha256).hexdigest()[:16]

    def sign(
        self,
        *,
        op: str,
        request_id: str,
        approval_id: str | None,
        value: str,
        chat_id: int,
    ) -> str:
        sig = self._digest(
            op=op,
            request_id=request_id,
            approval_id=approval_id,
            value=value,
            chat_id=chat_id,
        )
        payload = f"{op}:{request_id}:{value}:{sig}"
        if len(payload.encode("utf-8")) > MAX_TELEGRAM_CALLBACK_BYTES:
            raise ValueError("Callback payload exceeded Telegram 64-byte limit.")
        return payload

    def parse(self, payload: str) -> tuple[str, str, str, str] | None:
        if not isinstance(payload, str):
            return None
        parts = payload.split(":")
        if len(parts) != 4:
            return None
        op, request_id, value, sig = parts
        if op not in ("a", "r", "b", "c") or not request_id or not value or len(sig) != 16:
            return None
        return op, request_id, value, sig

    def verify(
        self,
        payload: str,
        *,
        approval_id: str | None,
        chat_id: int,
    ) -> tuple[str, str, str] | None:
        parsed = self.parse(payload)
        if parsed is None:
            return None
        op, request_id, value, provided_sig = parsed
        expected_sig = self._digest(
            op=op,
            request_id=request_id,
            approval_id=approval_id,
            value=value,
            chat_id=chat_id,
        )
        if not hmac.compare_digest(provided_sig, expected_sig):
            return None
        return op, request_id, value


def format_approver_card(
    req: dict[str, Any],
    *,
    remaining_seconds: int | None = None,
    outcome: str | None = None,
) -> str:
    """Format the approver-only decision card (includes Aletheia + Eidos evidence)."""
    vr = req.get("voice_result") or {}
    risk = str(vr.get("risk") or "BORDERLINE")
    reason = str(vr.get("reason") or "Voice check result unavailable; failed closed.")

    aletheia = vr.get("aletheia") if isinstance(vr.get("aletheia"), dict) else None
    eidos = vr.get("eidos") if isinstance(vr.get("eidos"), dict) else None

    aletheia_line = (
        f"Aletheia: verdict={aletheia.get('verdict', 'unavailable')}, "
        f"score={_format_score(aletheia.get('score'))}"
        if aletheia
        else "Aletheia: unavailable"
    )
    eidos_line = (
        f"Eidos: decision={eidos.get('decision', 'unavailable')}, "
        f"similarity={_format_score(eidos.get('similarity'))}"
        if eidos
        else "Eidos: unavailable"
    )

    requester_name = req.get("employee_name") or req.get("employee_id") or "Unknown"
    callback_hint = str(req.get("known_callback_hint") or "").strip()

    if risk == "FAIL":
        header = "ALERT: BREAK-GLASS VOICE CHECK FAILED"
    elif risk == "BORDERLINE":
        header = "BREAK-GLASS APPROVAL (BORDERLINE — REVIEW REQUIRED)"
    else:
        header = "BREAK-GLASS APPROVAL REQUEST"

    lines = [
        header,
        f"Request: {req['request_id']}",
        f"Requester: {requester_name} ({req['employee_id']})",
        f"Action: {req['action_summary']}",
        f"Target: {req['target_resource']}",
        f"Justification: {req['justification']}",
        "",
        f"Risk: {risk}",
        aletheia_line,
        eidos_line,
        f"Reason: {reason}",
        EVIDENCE_NOTICE,
    ]

    if risk == "BORDERLINE" and outcome is None:
        hint_suffix = f" ({callback_hint})" if callback_hint else ""
        lines.append("")
        lines.append(
            "WARNING: BORDERLINE voice check. Call the requester back on a number you "
            f"already know{hint_suffix} before approving."
        )
    elif risk == "FAIL" and outcome is None:
        lines.append("")
        lines.append(
            "HARD FAIL: Approval is disabled and cannot be overridden. "
            "Block the request or call back on a number you already know."
        )

    if outcome is not None:
        lines.append("")
        lines.append(f"Outcome: {outcome}")
    elif risk in ("PASS", "BORDERLINE"):
        approval = req.get("active_approval")
        rem = (
            remaining_seconds
            if remaining_seconds is not None
            else _remaining_seconds(approval.get("expires_at") if isinstance(approval, dict) else None)
        )
        lines.append("")
        lines.append(f"Approval window: {rem}s remaining (120s window)")
        lines.append("Select the 2-digit number shown on the requester screen, or Reject.")

    return "\n".join(lines)


def build_approver_keyboard(
    req: dict[str, Any],
    *,
    chat_id: int,
    signer: CallbackSigner,
) -> InlineKeyboardMarkup | None:
    """Build inline keyboard with unforgeable HMAC-signed callbacks for the approver card."""
    vr = req.get("voice_result") or {}
    risk = vr.get("risk")
    request_id = req["request_id"]

    if risk == "FAIL":
        block_data = signer.sign(
            op="b",
            request_id=request_id,
            approval_id=None,
            value="-",
            chat_id=chat_id,
        )
        callback_data = signer.sign(
            op="c",
            request_id=request_id,
            approval_id=None,
            value="-",
            chat_id=chat_id,
        )
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Block request", callback_data=block_data),
                    InlineKeyboardButton(
                        "Call back on known number",
                        callback_data=callback_data,
                    ),
                ]
            ]
        )

    approval = req.get("active_approval")
    if not isinstance(approval, dict) or approval.get("status") != "PENDING":
        return None
    if _remaining_seconds(approval.get("expires_at")) <= 0:
        return None

    approval_id = str(approval["approval_id"])
    options = [str(opt) for opt in approval.get("number_options", [])]
    if len(options) != 3:
        return None

    number_buttons = [
        InlineKeyboardButton(
            f"Match {code}",
            callback_data=signer.sign(
                op="a",
                request_id=request_id,
                approval_id=approval_id,
                value=code,
                chat_id=chat_id,
            ),
        )
        for code in options
    ]
    reject_button = InlineKeyboardButton(
        "Reject",
        callback_data=signer.sign(
            op="r",
            request_id=request_id,
            approval_id=approval_id,
            value="-",
            chat_id=chat_id,
        ),
    )
    return InlineKeyboardMarkup([number_buttons, [reject_button]])


def format_requester_status(req: dict[str, Any], *, note: str | None = None) -> str:
    """Build a plain status update for the requester. NEVER includes scores or verdicts."""
    status = str(req.get("status") or "UNKNOWN")
    status_messages = {
        "PENDING_VOICE": "Waiting for voice verification.",
        "PENDING_APPROVAL": "Voice check received. Waiting for second-device approver review.",
        "ESCALATED_REVIEW": (
            "Voice check received. Request requires peer review by your approver; "
            "be ready for a callback on a known number."
        ),
        "BLOCKED": (
            "Request is blocked. Please contact your approver on a number you already know."
        ),
        "APPROVED": "Request approved by your approver.",
        "REJECTED": "Request was rejected by the approver.",
        "EXECUTED": "Request approved and simulated action executed.",
        "EXPIRED": "Approval window expired. Request a new approval if still needed.",
    }
    plain_status = note or status_messages.get(status, "Status updated.")
    return (
        f"Break-Glass Request {req['request_id']}\n"
        f"Action: {req['action_summary']}\n"
        f"Status: {plain_status}"
    )


class ApproverConsole:
    """Manages /approver registration, /report, card dispatch, countdowns, and callback decisions."""

    def __init__(
        self,
        *,
        db_path: Path | None = None,
        signer: CallbackSigner | None = None,
        poll_interval: float = POLL_INTERVAL_SECONDS,
        countdown_step: int = COUNTDOWN_STEP_SECONDS,
    ) -> None:
        self._db_path = db_path
        self._signer = signer or CallbackSigner()
        self._poll_interval = poll_interval
        self._countdown_step = countdown_step

        # chat_id -> employee_id (registered via /approver)
        self.approver_chats: dict[int, str] = {}
        # employee_id -> set[chat_id] (registered via /requester for plain status only)
        self.requester_chats: dict[str, set[int]] = {}
        # (request_id, approval_id_or_fail, chat_id) -> message_id
        self._dispatched_cards: dict[tuple[str, str, int], int] = {}
        # request_id -> notified requester status key
        self._notified_requester_state: dict[str, str] = {}
        # Request IDs already terminal before bot boot so history isn't replayed
        self._preexisting_terminal_ids: set[str] = set()
        # Confirmed FAIL alert blocks to prevent duplicate callback replay
        self._confirmed_fail_blocks: set[tuple[str, int]] = set()
        # Active countdown tasks keyed by (request_id, approval_id, chat_id)
        self._countdown_tasks: dict[tuple[str, str, int], asyncio.Task[None]] = {}
        self._watcher_task: asyncio.Task[None] | None = None

        self._load_optional_env_approver()

    def _load_optional_env_approver(self) -> None:
        raw_chat = os.getenv("TELEGRAM_APPROVER_CHAT_ID", "").strip()
        raw_emp = os.getenv("TELEGRAM_APPROVER_EMPLOYEE_ID", "").strip()
        if raw_chat and raw_emp:
            try:
                self.approver_chats[int(raw_chat)] = raw_emp
            except ValueError:
                logger.warning("Ignoring invalid TELEGRAM_APPROVER_CHAT_ID value.")

    def snapshot_existing_terminal_requests(self) -> None:
        """Record already-finished requests on startup so old requests do not spam approvers."""
        init_db(self._db_path)
        with get_db(self._db_path) as conn:
            for req in list_requests(conn, limit=200):
                if req["status"] in ("BLOCKED", "APPROVED", "REJECTED", "EXECUTED", "EXPIRED"):
                    self._preexisting_terminal_ids.add(req["request_id"])

    async def start(self, app: Application) -> None:
        """Start background SQLite watcher inside python-telegram-bot post_init."""
        await asyncio.to_thread(self.snapshot_existing_terminal_requests)
        self._watcher_task = asyncio.create_task(self._watch_loop(app))
        logger.info("Approver console watcher started.")

    async def stop(self, _app: Application) -> None:
        """Cancel background watcher and countdown tasks cleanly on shutdown."""
        if self._watcher_task is not None:
            self._watcher_task.cancel()
            try:
                await self._watcher_task
            except asyncio.CancelledError:
                pass
        for task in list(self._countdown_tasks.values()):
            task.cancel()
        self._countdown_tasks.clear()

    async def handle_approver_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Register the sender's chat as an approver chat bound to an employee_id."""
        msg = update.effective_message
        chat = update.effective_chat
        if msg is None or chat is None:
            return

        init_db(self._db_path)
        args = context.args or []
        if not args:
            with get_db(self._db_path) as conn:
                employees = list_employees(conn)
            emp_list = "\n".join(
                f"- {e['employee_id']} ({e['display_name']}, role: {e['role']})"
                for e in employees
            )
            await msg.reply_text(
                "Usage: /approver <employee_id>\n"
                "Example: /approver emp_priya\n\n"
                f"Known employees:\n{emp_list}"
            )
            return

        employee_id = args[0].strip()
        try:
            with get_db(self._db_path) as conn:
                emp = get_employee(conn, employee_id)
                record_event(
                    conn,
                    event_type="TELEGRAM_APPROVER_REGISTERED",
                    actor_id=employee_id,
                    request_id=None,
                    details={"chat_id": chat.id, "role": emp["role"]},
                )
        except ApprovalError as exc:
            await msg.reply_text(str(exc))
            return
        except Exception:
            # Fail closed: do not register chat if audit logging fails.
            await msg.reply_text("Audit logging failed; approver registration did not proceed.")
            return

        # Remove from requester chats if previously registered there so roles stay clean.
        for chats in self.requester_chats.values():
            chats.discard(chat.id)
        self.approver_chats[chat.id] = employee_id

        await msg.reply_text(
            f"Registered this chat as approver console for {emp['display_name']} ({employee_id}).\n"
            "New break-glass voice verifications will appear here with action-bound controls.\n"
            "Role and different-person checks are enforced by the approval service on every decision."
        )

    async def handle_report_command(
        self,
        update: Update,
        _context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Approver-only /report: verify existing audit hash chain and summarize last 5 events."""
        msg = update.effective_message
        chat = update.effective_chat
        if msg is None or chat is None:
            return

        employee_id = self.approver_chats.get(chat.id)
        if not employee_id:
            await msg.reply_text(
                "Access denied: /report is restricted to registered approvers. "
                "Run /approver <employee_id> first."
            )
            return

        def _verify_and_build() -> str:
            init_db(self._db_path)
            with get_db(self._db_path) as conn:
                emp = get_employee(conn, employee_id)
                if emp["role"] not in APPROVER_ROLES:
                    raise ApprovalError(
                        f"Access denied: employee '{employee_id}' has role '{emp['role']}' "
                        "and cannot view approver audit reports."
                    )
                return build_audit_report(conn)

        try:
            report_text = await asyncio.to_thread(_verify_and_build)
        except ApprovalError as exc:
            await msg.reply_text(str(exc))
            return
        except Exception:
            await msg.reply_text("Audit report could not be generated right now (failed closed).")
            return

        await msg.reply_text(report_text)

    async def handle_requester_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Register the sender's chat for plain requester status updates (never shows scores)."""
        msg = update.effective_message
        chat = update.effective_chat
        if msg is None or chat is None:
            return

        init_db(self._db_path)
        args = context.args or []
        if not args:
            await msg.reply_text("Usage: /requester <employee_id>\nExample: /requester emp_rahul")
            return

        employee_id = args[0].strip()
        try:
            with get_db(self._db_path) as conn:
                emp = get_employee(conn, employee_id)
        except ApprovalError as exc:
            await msg.reply_text(str(exc))
            return

        self.approver_chats.pop(chat.id, None)
        self.requester_chats.setdefault(employee_id, set()).add(chat.id)
        await msg.reply_text(
            f"Registered this chat for plain status updates for {emp['display_name']} ({employee_id})."
        )

    async def handle_status_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Return plain status of a request without ever exposing AI scores."""
        msg = update.effective_message
        if msg is None:
            return
        args = context.args or []
        if not args:
            await msg.reply_text("Usage: /status <request_id>")
            return
        request_id = args[0].strip()
        init_db(self._db_path)
        try:
            with get_db(self._db_path) as conn:
                req = get_request(conn, request_id)
        except ApprovalError as exc:
            await msg.reply_text(str(exc))
            return
        await msg.reply_text(format_requester_status(req))

    async def _notify_requester_plain(
        self,
        bot: Any,
        req: dict[str, Any],
        *,
        note: str | None = None,
    ) -> None:
        chat_ids = list(self.requester_chats.get(req["employee_id"], ()))
        if not chat_ids:
            return
        text = format_requester_status(req, note=note)
        for chat_id in chat_ids:
            try:
                await bot.send_message(chat_id=chat_id, text=text)
            except TelegramError:
                logger.warning("Could not deliver plain status update to requester chat.")

    async def dispatch_pending_cards(self, bot: Any) -> None:
        """Check SQLite for voice-verified requests, audit APPROVAL_SHOWN, and send approver cards."""
        if not self.approver_chats:
            return

        def _load_candidates() -> list[dict[str, Any]]:
            init_db(self._db_path)
            with get_db(self._db_path) as conn:
                return list_requests(conn, limit=50)

        requests = await asyncio.to_thread(_load_candidates)

        for req in reversed(requests):
            request_id = req["request_id"]
            if request_id in self._preexisting_terminal_ids:
                continue
            vr = req.get("voice_result")
            if not isinstance(vr, dict):
                continue

            risk = vr.get("risk")
            status = req["status"]
            approval = req.get("active_approval")

            if risk == "FAIL" and status == "BLOCKED":
                card_token = "FAIL"
            elif risk in ("PASS", "BORDERLINE") and status in ("PENDING_APPROVAL", "ESCALATED_REVIEW"):
                if not isinstance(approval, dict) or approval.get("status") != "PENDING":
                    continue
                if _remaining_seconds(approval.get("expires_at")) <= 0:
                    continue
                card_token = str(approval["approval_id"])
            else:
                continue

            # Notify requester once with a plain status message (no scores).
            req_state_key = f"{card_token}:{status}"
            if self._notified_requester_state.get(request_id) != req_state_key:
                self._notified_requester_state[request_id] = req_state_key
                await self._notify_requester_plain(bot, req)

            for chat_id, approver_emp_id in list(self.approver_chats.items()):
                key = (request_id, card_token, chat_id)
                if key in self._dispatched_cards:
                    continue

                # Fail closed: record APPROVAL_SHOWN in audit chain BEFORE showing card.
                try:
                    await asyncio.to_thread(
                        log_approval_shown,
                        actor_id=approver_emp_id,
                        request_id=request_id,
                        approval_ref=card_token,
                        risk=str(risk),
                        db_path=self._db_path,
                    )
                except AuditLoggingError:
                    logger.warning(
                        "Audit logging failed for APPROVAL_SHOWN (%s); skipping card dispatch.",
                        request_id,
                    )
                    continue

                card_text = format_approver_card(req)
                keyboard = build_approver_keyboard(req, chat_id=chat_id, signer=self._signer)
                try:
                    sent = await bot.send_message(
                        chat_id=chat_id,
                        text=card_text,
                        reply_markup=keyboard,
                    )
                except TelegramError:
                    logger.warning("Failed to send approver card for %s.", request_id)
                    continue

                self._dispatched_cards[key] = sent.message_id
                if risk in ("PASS", "BORDERLINE") and isinstance(approval, dict):
                    self._start_countdown(
                        bot=bot,
                        chat_id=chat_id,
                        message_id=sent.message_id,
                        request_id=request_id,
                        approval_id=str(approval["approval_id"]),
                    )

    async def _watch_loop(self, app: Application) -> None:
        while True:
            try:
                await self.dispatch_pending_cards(app.bot)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Approver watcher poll error; will retry.")
            await asyncio.sleep(self._poll_interval)

    def _start_countdown(
        self,
        *,
        bot: Any,
        chat_id: int,
        message_id: int,
        request_id: str,
        approval_id: str,
    ) -> None:
        key = (request_id, approval_id, chat_id)
        existing = self._countdown_tasks.pop(key, None)
        if existing is not None:
            existing.cancel()
        self._countdown_tasks[key] = asyncio.create_task(
            self._countdown_loop(
                bot=bot,
                chat_id=chat_id,
                message_id=message_id,
                request_id=request_id,
                approval_id=approval_id,
            )
        )

    def _cancel_countdowns_for_request(self, request_id: str) -> None:
        for key in list(self._countdown_tasks.keys()):
            if key[0] == request_id:
                task = self._countdown_tasks.pop(key)
                task.cancel()

    async def _countdown_loop(
        self,
        *,
        bot: Any,
        chat_id: int,
        message_id: int,
        request_id: str,
        approval_id: str,
    ) -> None:
        """Periodically refresh the 120s countdown and expire the card when time runs out."""
        try:
            while True:
                await asyncio.sleep(self._countdown_step)

                def _check_state() -> tuple[dict[str, Any], dict[str, Any]]:
                    with get_db(self._db_path) as conn:
                        return get_request(conn, request_id), get_approval(conn, approval_id)

                req, apr = await asyncio.to_thread(_check_state)
                rem = _remaining_seconds(apr.get("expires_at"))

                if apr["status"] == "PENDING" and rem > 0:
                    text = format_approver_card(req, remaining_seconds=rem)
                    kb = build_approver_keyboard(req, chat_id=chat_id, signer=self._signer)
                    await self._safe_edit(bot, chat_id, message_id, text, kb)
                    continue

                if apr["status"] == "EXPIRED" or (apr["status"] == "PENDING" and rem <= 0):
                    await self.expire_card(
                        bot=bot,
                        chat_id=chat_id,
                        message_id=message_id,
                        request_id=request_id,
                        approval_id=approval_id,
                        record_expiry_audit=True,
                    )
                    return

                # Decided externally (e.g. from web UI or another approver chat).
                outcome = f"{req['status']} (handled outside this message)"
                text = format_approver_card(req, outcome=outcome)
                await self._safe_edit(bot, chat_id, message_id, text, None)
                return
        except asyncio.CancelledError:
            return
        except Exception:
            logger.warning("Countdown loop stopped unexpectedly for %s.", request_id)
        finally:
            self._countdown_tasks.pop((request_id, approval_id, chat_id), None)

    async def expire_card(
        self,
        *,
        bot: Any,
        chat_id: int,
        message_id: int,
        request_id: str,
        approval_id: str | None = None,
        record_expiry_audit: bool = True,
    ) -> None:
        """Audit expiry (fail closed), then edit card to 'Expired' and disable buttons."""
        actor_id = self.approver_chats.get(chat_id, "system:telegram_bot")

        def _audit_and_load_req() -> dict[str, Any]:
            with get_db(self._db_path) as conn:
                req = get_request(conn, request_id)
                apr_id = approval_id or (
                    str(req["active_approval"]["approval_id"])
                    if isinstance(req.get("active_approval"), dict)
                    else "unknown"
                )
                if record_expiry_audit:
                    conn.execute(
                        "UPDATE approval_records SET status = 'EXPIRED' WHERE approval_id = ? AND status = 'PENDING'",
                        (apr_id,),
                    )
                    log_expiry(
                        actor_id=actor_id,
                        request_id=request_id,
                        approval_id=apr_id,
                        conn=conn,
                    )
                return get_request(conn, request_id)

        try:
            req = await asyncio.to_thread(_audit_and_load_req)
        except AuditLoggingError:
            logger.warning("Audit logging failed for APPROVAL_EXPIRED (%s); failing closed.", request_id)
            return

        text = format_approver_card(req, outcome="Expired — 120s approval window elapsed.")
        await self._safe_edit(bot, chat_id, message_id, text, None)
        await self._notify_requester_plain(
            bot,
            req,
            note="Approval window expired. Request a new approval if still needed.",
        )

    async def _safe_edit(
        self,
        bot: Any,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: InlineKeyboardMarkup | None,
    ) -> None:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=reply_markup,
            )
        except BadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                logger.warning("Telegram edit failed: %s", exc)
        except TelegramError:
            logger.warning("Telegram edit failed for chat %s.", chat_id)

    async def handle_callback_query(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Validate signed callback server-side and delegate decisions to approval.service."""
        query = update.callback_query
        if query is None or not query.data:
            return

        msg = query.message
        chat_id = msg.chat_id if msg is not None else None
        message_id = msg.message_id if msg is not None else None
        if chat_id is None or message_id is None:
            await query.answer("Invalid message context.", show_alert=True)
            return

        reviewer_id = self.approver_chats.get(chat_id)
        if not reviewer_id:
            await query.answer(
                "This chat is not registered. Run /approver <employee_id> first.",
                show_alert=True,
            )
            return

        parsed = self._signer.parse(query.data)
        if parsed is None:
            await query.answer("Invalid callback data.", show_alert=True)
            return
        op, request_id, _val, _sig = parsed

        def _fetch_req() -> dict[str, Any]:
            init_db(self._db_path)
            with get_db(self._db_path) as conn:
                return get_request(conn, request_id)

        try:
            req = await asyncio.to_thread(_fetch_req)
        except ApprovalError:
            await query.answer("Request not found.", show_alert=True)
            return

        vr = req.get("voice_result") or {}
        risk = vr.get("risk")
        approval = req.get("active_approval")
        expected_approval_id = (
            None if risk == "FAIL" else (str(approval["approval_id"]) if isinstance(approval, dict) else None)
        )

        verified = self._signer.verify(
            query.data,
            approval_id=expected_approval_id,
            chat_id=chat_id,
        )
        if verified is None:
            await query.answer("Forged or stale button callback rejected.", show_alert=True)
            return

        op, request_id, value = verified

        # FAIL alert actions: NEVER allow approve/override.
        if op == "c":
            hint = str(req.get("known_callback_hint") or "").strip()
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

        if op == "b":
            if risk != "FAIL":
                await query.answer("Block alert action is only valid for FAIL requests.", show_alert=True)
                return
            if (request_id, chat_id) in self._confirmed_fail_blocks:
                await query.answer("Block already confirmed for this request.", show_alert=True)
                return

            def _confirm_block() -> dict[str, Any]:
                with get_db(self._db_path) as conn:
                    reviewer = get_employee(conn, reviewer_id)
                    if reviewer["role"] not in APPROVER_ROLES:
                        raise ApprovalError(
                            f"Employee '{reviewer_id}' has role '{reviewer['role']}' and cannot confirm break-glass blocks."
                        )
                    if reviewer["employee_id"] == req["employee_id"]:
                        raise ApprovalError(
                            "Different-person rule violation: the requester cannot confirm their own request."
                        )
                    log_block(actor_id=reviewer_id, request_id=request_id, conn=conn)
                    return get_request(conn, request_id)

            try:
                updated_req = await asyncio.to_thread(_confirm_block)
            except ApprovalError as exc:
                await query.answer(str(exc), show_alert=True)
                return
            except AuditLoggingError:
                await query.answer(
                    "Audit logging failed; block confirmation did not proceed.",
                    show_alert=True,
                )
                return

            self._confirmed_fail_blocks.add((request_id, chat_id))
            self._cancel_countdowns_for_request(request_id)
            card_text = format_approver_card(
                updated_req,
                outcome=f"BLOCKED (confirmed by {reviewer_id}). Hard voice failure cannot be overridden.",
            )
            await self._safe_edit(context.bot, chat_id, message_id, card_text, None)
            await query.answer("Request confirmed blocked.")
            await self._notify_requester_plain(context.bot, updated_req)
            return

        # PASS / BORDERLINE approve ('a') or reject ('r') — delegated to existing approval.service.
        if risk == "FAIL" or not isinstance(approval, dict):
            await query.answer(
                "Hard voice-verification failure cannot be overridden by approval.",
                show_alert=True,
            )
            return

        approval_id = str(approval["approval_id"])

        if op == "a":
            requires_peer = bool(approval.get("requires_peer_review"))
            notes = (
                f"Telegram approver {reviewer_id} confirmed borderline peer review."
                if requires_peer
                else f"Approved via Telegram approver console by {reviewer_id}."
            )

            def _approve_and_execute() -> tuple[dict[str, Any], dict[str, Any]]:
                with get_db(self._db_path) as conn:
                    decide_approval(
                        conn,
                        approval_id=approval_id,
                        reviewer_id=reviewer_id,
                        action="approve",
                        matching_number=value,
                        notes=notes,
                        peer_review_confirmed=requires_peer,
                    )
                    log_approval_decision(
                        actor_id=reviewer_id,
                        request_id=request_id,
                        approval_id=approval_id,
                        decision="approve",
                        conn=conn,
                    )
                    receipt = execute_request(
                        conn,
                        request_id=request_id,
                        actor_id=reviewer_id,
                    )
                    return get_request(conn, request_id), receipt

            try:
                updated_req, receipt = await asyncio.to_thread(_approve_and_execute)
            except ApprovalError as exc:
                err_text = str(exc)
                await query.answer(err_text[:200], show_alert=True)
                if "expired" in err_text.lower():
                    self._cancel_countdowns_for_request(request_id)
                    await self.expire_card(
                        bot=context.bot,
                        chat_id=chat_id,
                        message_id=message_id,
                        request_id=request_id,
                        approval_id=approval_id,
                        record_expiry_audit=False,
                    )
                return
            except AuditLoggingError:
                await query.answer(
                    "Audit logging failed; approval did not proceed.",
                    show_alert=True,
                )
                return

            self._cancel_countdowns_for_request(request_id)
            card_text = format_approver_card(
                updated_req,
                outcome=f"APPROVED & EXECUTED (SIMULATED) by {reviewer_id}",
            )
            await self._safe_edit(context.bot, chat_id, message_id, card_text, None)
            await query.answer("Approved and executed (simulated).")
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"Execution Receipt ({request_id}):\n"
                    f"{receipt['execution_receipt']}"
                ),
            )
            await self._notify_requester_plain(context.bot, updated_req)
            return

        if op == "r":
            def _reject() -> dict[str, Any]:
                with get_db(self._db_path) as conn:
                    updated = decide_approval(
                        conn,
                        approval_id=approval_id,
                        reviewer_id=reviewer_id,
                        action="reject",
                        notes=f"Rejected via Telegram approver console by {reviewer_id}.",
                    )
                    log_approval_decision(
                        actor_id=reviewer_id,
                        request_id=request_id,
                        approval_id=approval_id,
                        decision="reject",
                        conn=conn,
                    )
                    return updated

            try:
                updated_req = await asyncio.to_thread(_reject)
            except ApprovalError as exc:
                err_text = str(exc)
                await query.answer(err_text[:200], show_alert=True)
                if "expired" in err_text.lower():
                    self._cancel_countdowns_for_request(request_id)
                    await self.expire_card(
                        bot=context.bot,
                        chat_id=chat_id,
                        message_id=message_id,
                        request_id=request_id,
                        approval_id=approval_id,
                        record_expiry_audit=False,
                    )
                return
            except AuditLoggingError:
                await query.answer(
                    "Audit logging failed; rejection did not proceed.",
                    show_alert=True,
                )
                return

            self._cancel_countdowns_for_request(request_id)
            card_text = format_approver_card(
                updated_req,
                outcome=f"REJECTED by {reviewer_id}",
            )
            await self._safe_edit(context.bot, chat_id, message_id, card_text, None)
            await query.answer("Request rejected.")
            await self._notify_requester_plain(context.bot, updated_req)
            return
