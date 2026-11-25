"""Server-side configuration for Break-Glass Voice Gate core backend."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[3]

try:
    from dotenv import load_dotenv

    for _env_path in (REPO_ROOT / ".env", BACKEND_ROOT / ".env", Path.cwd() / ".env"):
        if _env_path.is_file():
            load_dotenv(dotenv_path=_env_path, encoding="utf-8-sig", override=False)
except ModuleNotFoundError:
    pass

DEFAULT_DB_PATH = BACKEND_ROOT / "data" / "voice_gate.db"
DEFAULT_UPLOAD_DIR = BACKEND_ROOT / "data" / "uploads"
DEFAULT_CORS_ORIGINS = ("http://localhost:3000", "http://localhost:5173")


@dataclass(frozen=True)
class CoreConfig:
    database_path: Path
    upload_dir: Path
    cors_origins: tuple[str, ...]
    challenge_ttl_seconds: int
    approval_ttl_seconds: int
    max_upload_bytes: int
    reviewer_api_token: str | None
    live_max_chunks: int = 12
    live_max_chunk_bytes: int = 4 * 1024 * 1024
    live_fixture_scenario: str | None = None
    bot_internal_port: int = 8765
    bot_internal_token: str | None = None


def load_core_config() -> CoreConfig:
    """Load backend core settings from environment variables."""
    db_raw = os.getenv("DATABASE_PATH", "").strip()
    db_path = Path(db_raw).resolve() if db_raw else DEFAULT_DB_PATH

    upload_raw = os.getenv("UPLOAD_DIR", "").strip()
    upload_dir = Path(upload_raw).resolve() if upload_raw else DEFAULT_UPLOAD_DIR

    origins_raw = os.getenv("CORS_ORIGINS", "").strip()
    if origins_raw:
        origins = tuple(
            origin.strip().rstrip("/")
            for origin in origins_raw.split(",")
            if origin.strip() and origin.strip() != "*"
        )
    else:
        origins = DEFAULT_CORS_ORIGINS

    ttl_raw = os.getenv("CHALLENGE_TTL_SECONDS", "300").strip()
    try:
        challenge_ttl = max(30, min(int(ttl_raw), 3600))
    except ValueError:
        challenge_ttl = 300

    # Documented 2-minute approval expiry (120 seconds).
    approval_ttl_raw = os.getenv("APPROVAL_TTL_SECONDS", "120").strip()
    try:
        approval_ttl = max(10, min(int(approval_ttl_raw), 600))
    except ValueError:
        approval_ttl = 120

    max_mb_raw = os.getenv("MAX_UPLOAD_MB", "15").strip()
    try:
        max_upload_bytes = max(1, min(int(max_mb_raw), 50)) * 1024 * 1024
    except ValueError:
        max_upload_bytes = 15 * 1024 * 1024

    live_max_chunks_raw = os.getenv("LIVE_MAX_CHUNKS", "12").strip()
    try:
        live_max_chunks = max(1, min(int(live_max_chunks_raw), 120))
    except ValueError:
        live_max_chunks = 12

    live_chunk_mb_raw = os.getenv("LIVE_MAX_CHUNK_MB", "4").strip()
    try:
        live_max_chunk_bytes = min(max_upload_bytes, max(1, min(int(live_chunk_mb_raw), 15)) * 1024 * 1024)
    except ValueError:
        live_max_chunk_bytes = min(max_upload_bytes, 4 * 1024 * 1024)

    live_fixture_scenario = os.getenv("LIVE_FIXTURE_SCENARIO", "").strip() or None

    bot_port_raw = os.getenv("BOT_INTERNAL_PORT", "8765").strip()
    try:
        bot_internal_port = max(1024, min(int(bot_port_raw), 65535))
    except ValueError:
        bot_internal_port = 8765

    bot_internal_token = os.getenv("BOT_INTERNAL_TOKEN", "").strip() or None
    reviewer_token = os.getenv("REVIEWER_API_TOKEN", "").strip() or None

    return CoreConfig(
        database_path=db_path,
        upload_dir=upload_dir,
        cors_origins=origins or DEFAULT_CORS_ORIGINS,
        challenge_ttl_seconds=challenge_ttl,
        approval_ttl_seconds=approval_ttl,
        max_upload_bytes=max_upload_bytes,
        reviewer_api_token=reviewer_token,
        live_max_chunks=live_max_chunks,
        live_max_chunk_bytes=live_max_chunk_bytes,
        live_fixture_scenario=live_fixture_scenario,
        bot_internal_port=bot_internal_port,
        bot_internal_token=bot_internal_token,
    )
