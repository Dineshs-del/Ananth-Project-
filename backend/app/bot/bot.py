"""Async Telegram bot (long polling) for checking voice notes with DetectifAI Aletheia."""

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

from dotenv import load_dotenv
from telegram import Message, Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from ..ai.config import ConfigError, load_config
from .analyze import analyze_audio, format_telegram_reply
from .audio import (
    AudioConversionError,
    AudioDurationError,
    AudioSizeError,
    download_and_prepare_wav,
)

load_dotenv()

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
) -> None:
    """Append check outcome to the existing hash-chained audit log if available."""
    try:
        from ..audit.service import record_event
        from ..core.database import get_db, init_db

        init_db(seed_sample_employees=False)
        aletheia = analysis.get("aletheia") if isinstance(analysis, dict) else None
        details: dict[str, Any] = {
            "channel": "telegram",
            "chat_type": chat_type,
            "ai_mode": ai_mode,
            "risk": analysis.get("risk"),
            "verdict": aletheia.get("verdict") if isinstance(aletheia, dict) else None,
            "score": aletheia.get("score") if isinstance(aletheia, dict) else None,
            "operation_id": aletheia.get("operation_id") if isinstance(aletheia, dict) else None,
        }
        with get_db() as conn:
            record_event(
                conn,
                event_type="TELEGRAM_VOICE_CHECK",
                actor_id=f"tg:{user_id}",
                request_id=None,
                details=details,
            )
    except Exception:
        # Never break the user reply if the core audit DB is not present in this branch.
        logger.debug("Audit hook skipped (audit DB unavailable).")


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

    await asyncio.to_thread(
        _record_audit_hook,
        user_id=user.id,
        chat_type=str(chat.type),
        analysis=analysis,
        ai_mode=ai_mode,
    )

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
        "Send or forward a voice note or audio file here in DM, "
        "or reply to a voice note in a group with /check (or mention me) "
        "to check for synthetic-voice signs."
    )


async def handle_dm_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle voice notes or audio files sent/forwarded in direct messages."""
    msg = update.effective_message
    if msg is None:
        return
    await _process_audio_message(update, context, msg)


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


def build_application(token: str) -> Application:
    """Wire handlers onto a python-telegram-bot Application."""
    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("check", handle_check_command))
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & (filters.VOICE | filters.AUDIO),
            handle_dm_audio,
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

    logger.info("Starting Break-Glass Voice Gate Telegram bot (AI_MODE=%s) via long polling.", ai_cfg.ai_mode)
    app = build_application(token)
    app.run_polling(drop_pending_updates=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
