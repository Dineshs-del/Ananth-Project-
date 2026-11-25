"""Async Telegram bot (long polling) for Break-Glass Voice Gate.

Supports:
1. Voice-note deepfake checking via DetectifAI Aletheia (DM or group /check / @mention).
2. Break-Glass Approver Console (/approver, /report, live 120s approval cards,
   HMAC-signed callbacks, and requester plain-status updates via /requester & /status).
3. Admin-linked voice enrollment (/link, /enroll, /done) with explicit consent,
   15-60s validation before calling DetectifAI, 5-sample cap, and audio cleanup.
4. Live-call risk monitoring alerts (single edited status message, latched FAIL,
   one-time FAIL alert with Block and Call-back buttons).

Fail-closed audit policy: every bot action writes to the existing hash-chained audit
service BEFORE delivering results; if audit logging fails, the action does not proceed.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import tempfile
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Callable

from telegram import Message, Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from ..ai.config import ConfigError, load_config, load_project_dotenv
from .analyze import analyze_audio, format_telegram_reply
from .approver_console import ApproverConsole
from .audio import (
    AudioConversionError,
    AudioDurationError,
    AudioSizeError,
    download_and_prepare_wav,
)
from .audit_bridge import AuditLoggingError, log_voice_check_result
from .enrollment_flow import TelegramEnrollmentFlow
from .internal_bridge import BotInternalBridge
from .live_call_alerts import LiveCallAlertManager, register_live_call_manager

load_project_dotenv()

# Rate limiting constants (single source of truth).
RATE_LIMIT_MAX_CHECKS: int = 5
RATE_LIMIT_WINDOW_SECONDS: int = 600  # 10 minutes

PERMISSION_NOTICE = "Only send voice notes you have permission to check."

# Avoid logging HTTP request URLs (which contain the Telegram bot token).
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logger = logging.getLogger("voicegate.bot")


class UserRateLimiter:
    """Sliding-window per-user rate limiter."""

    def __init__(
        self,
        *,
        max_checks: int = RATE_LIMIT_MAX_CHECKS,
        window_seconds: int = RATE_LIMIT_WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_checks = max_checks
        self._window_seconds = window_seconds
        self._clock = clock
        self._hits: dict[int, deque[float]] = defaultdict(deque)

    def allow(self, user_id: int) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds)."""
        now = self._clock()
        cutoff = now - self._window_seconds
        bucket = self._hits[user_id]
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()

        if len(bucket) >= self._max_checks:
            wait_sec = max(1, int(self._window_seconds - (now - bucket[0])))
            return False, wait_sec

        bucket.append(now)
        return True, 0


_rate_limiter = UserRateLimiter()
_seen_users: set[int] = set()


def _record_audit_hook(
    *,
    user_id: int,
    chat_type: str,
    analysis: dict[str, Any],
    ai_mode: str,
    db_path: Path | None = None,
) -> None:
    """Record voice check result (risk + reason ONLY) in the existing audit chain.

    Fail closed: raises AuditLoggingError if the audit write fails.
    """
    risk = str(analysis.get("risk") or "BORDERLINE") if isinstance(analysis, dict) else "BORDERLINE"
    reason = str(analysis.get("reason") or "Voice check result unavailable.") if isinstance(analysis, dict) else "Voice check result unavailable."
    log_voice_check_result(
        actor_id=f"tg:{user_id}",
        risk=risk,
        reason=reason,
        chat_type=chat_type,
        ai_mode=ai_mode,
        db_path=db_path,
    )


def _extract_media(message: Message | None) -> tuple[str, int | None, int | None] | None:
    """Return (file_id, file_size, duration) if message holds a voice note or audio file."""
    if message is None:
        return None
    if message.voice is not None:
        return (
            message.voice.file_id,
            message.voice.file_size,
            message.voice.duration,
        )
    if message.audio is not None:
        return (
            message.audio.file_id,
            message.audio.file_size,
            message.audio.duration,
        )
    return None


async def _process_audio_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    target_message: Message,
) -> None:
    reply_anchor = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if reply_anchor is None or user is None or chat is None:
        return

    media = _extract_media(target_message)
    if media is None:
        return

    first_time = user.id not in _seen_users
    if first_time:
        _seen_users.add(user.id)

    allowed, retry_after = _rate_limiter.allow(user.id)
    if not allowed:
        mins = max(1, (retry_after + 59) // 60)
        await reply_anchor.reply_text(
            f"Rate limit reached ({RATE_LIMIT_MAX_CHECKS} checks per "
            f"{RATE_LIMIT_WINDOW_SECONDS // 60} minutes). "
            f"Please try again in about {mins} minute(s)."
        )
        return

    file_id, declared_size, declared_duration = media
    prefix = f"{PERMISSION_NOTICE}\n\n" if first_time else ""

    try:
        with tempfile.TemporaryDirectory(prefix="voicegate_tg_") as tmp_name:
            tg_file = await context.bot.get_file(file_id)
            wav_path, _duration = await download_and_prepare_wav(
                tg_file,
                temp_dir=Path(tmp_name),
                declared_size=declared_size,
                declared_duration=declared_duration,
            )
            analysis = await asyncio.to_thread(analyze_audio, wav_path)
    except (AudioDurationError, AudioSizeError) as exc:
        await reply_anchor.reply_text(f"{prefix}{exc}")
        return
    except AudioConversionError:
        await reply_anchor.reply_text(
            f"{prefix}The voice check could not be done because the audio could not be processed.\n"
            "This is evidence, not proof.\n"
            "Next step: Do not send money or share codes. Call the person back on a number you already know."
        )
        return
    except Exception:
        logger.warning("Unexpected error while checking Telegram voice clip.")
        await reply_anchor.reply_text(
            f"{prefix}The voice check could not be done right now.\n"
            "This is evidence, not proof.\n"
            "Next step: Do not send money or share codes. Call the person back on a number you already know."
        )
        return

    try:
        ai_mode = load_config().ai_mode
    except ConfigError:
        ai_mode = "unknown"

    # Fail closed: if audit logging fails, do NOT deliver the voice check result.
    try:
        await asyncio.to_thread(
            _record_audit_hook,
            user_id=user.id,
            chat_type=str(chat.type),
            analysis=analysis,
            ai_mode=ai_mode,
        )
    except AuditLoggingError:
        logger.warning("Audit logging failed for voice check; aborting reply (fail closed).")
        await reply_anchor.reply_text(
            f"{prefix}Audit logging failed, so this voice check could not proceed.\n"
            "This is evidence, not proof.\n"
            "Next step: Do not send money or share codes. Call the person back on a number you already know."
        )
        return

    reply_text = format_telegram_reply(analysis)
    await reply_anchor.reply_text(f"{prefix}{reply_text}")


async def handle_start(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    user = update.effective_user
    if msg is None:
        return
    if user is not None:
        _seen_users.add(user.id)
    await msg.reply_text(
        f"{PERMISSION_NOTICE}\n\n"
        "Voice-note checker:\n"
        "- Send or forward a voice note in DM, or reply /check (or mention me) in a group.\n\n"
        "Voice Enrollment (admin-linked accounts):\n"
        "- /enroll — start guided voice enrollment (15-60s clips, max 5)\n"
        "- /done — finish enrollment session\n"
        "- /link <telegram_user_id> <employee_id> — link a user (security_admin only)\n\n"
        "Break-Glass Approver Console:\n"
        "- /approver <employee_id> — register this chat as an approver console\n"
        "- /report — verify the audit hash chain and view the last 5 events (approvers only)\n"
        "- /requester <employee_id> — receive plain status updates (no scores)\n"
        "- /status <request_id> — check plain status of a request"
    )


def _make_dm_audio_handler(enrollment_flow: TelegramEnrollmentFlow):
    async def handle_dm_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        user = update.effective_user
        if msg is None:
            return
        if user is not None and enrollment_flow.has_active_session(user.id):
            await enrollment_flow.handle_enrollment_audio(update, context)
            return
        await _process_audio_message(update, context, msg)

    return handle_dm_audio


async def handle_check_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /check when replying to a voice note or audio file (works in groups and DMs)."""
    msg = update.effective_message
    if msg is None:
        return
    replied = msg.reply_to_message
    if replied is None or _extract_media(replied) is None:
        await msg.reply_text(
            "Reply to a voice note or audio file with /check to analyze it."
        )
        return
    await _process_audio_message(update, context, replied)


async def handle_group_mention(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle group replies to a voice note that tag @botname."""
    msg = update.effective_message
    if msg is None or not msg.text or msg.reply_to_message is None:
        return
    if _extract_media(msg.reply_to_message) is None:
        return

    bot_username = (context.bot.username or "").strip().lower()
    if not bot_username:
        return
    if f"@{bot_username}" not in msg.text.lower():
        return

    await _process_audio_message(update, context, msg.reply_to_message)


def build_application(
    token: str,
    *,
    console: ApproverConsole | None = None,
    enrollment: TelegramEnrollmentFlow | None = None,
    live_alerts: LiveCallAlertManager | None = None,
) -> Application:
    """Wire voice-note checker, enrollment flow, live-call alerts, and approver console."""
    approver_console = console or ApproverConsole()
    enrollment_flow = enrollment or TelegramEnrollmentFlow(
        approver_chats_ref=approver_console.approver_chats,
    )
    live_call_alerts = live_alerts or LiveCallAlertManager(
        approver_chats_ref=approver_console.approver_chats,
    )
    internal_bridge = BotInternalBridge()
    register_live_call_manager(live_call_alerts)

    async def _post_init(application: Application) -> None:
        await approver_console.start(application)
        await live_call_alerts.start(application)
        await internal_bridge.start()

    async def _post_shutdown(application: Application) -> None:
        await internal_bridge.stop()
        await live_call_alerts.stop(application)
        await approver_console.stop(application)

    app = (
        ApplicationBuilder()
        .token(token)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("check", handle_check_command))
    app.add_handler(CommandHandler("link", enrollment_flow.handle_link_command))
    app.add_handler(CommandHandler("enroll", enrollment_flow.handle_enroll_command))
    app.add_handler(CommandHandler("done", enrollment_flow.handle_done_command))
    app.add_handler(CommandHandler("approver", approver_console.handle_approver_command))
    app.add_handler(CommandHandler("report", approver_console.handle_report_command))
    app.add_handler(CommandHandler("requester", approver_console.handle_requester_command))
    app.add_handler(CommandHandler("status", approver_console.handle_status_command))
    app.add_handler(
        CallbackQueryHandler(enrollment_flow.handle_consent_callback, pattern=r"^e[cx]:")
    )
    app.add_handler(
        CallbackQueryHandler(live_call_alerts.handle_callback_query, pattern=r"^l[bc]:")
    )
    app.add_handler(CallbackQueryHandler(approver_console.handle_callback_query))
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & (filters.VOICE | filters.AUDIO),
            _make_dm_audio_handler(enrollment_flow),
        )
    )
    app.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & filters.REPLY & filters.TEXT & ~filters.COMMAND,
            handle_group_mention,
        )
    )
    return app


def main() -> int:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print(
            "Error: TELEGRAM_BOT_TOKEN is missing. Set it in your local .env file and try again.",
            file=sys.stderr,
        )
        return 1

    try:
        ai_cfg = load_config()
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    logger.info(
        "Starting Break-Glass Voice Gate Telegram bot (AI_MODE=%s, detectif_key_configured=%s) via long polling.",
        ai_cfg.ai_mode,
        bool(ai_cfg.detectif_api_key),
    )
    app = build_application(token)
    app.run_polling(drop_pending_updates=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
