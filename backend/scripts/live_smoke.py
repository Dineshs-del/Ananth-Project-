"""One-shot live Detectif enroll/verify smoke test."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

from backend.app.ai.config import ConfigError, load_config
from backend.app.ai.detectif_client import (
    SUPPORTED_AUDIO_EXTENSIONS,
    DetectifClient,
    DetectifError,
    validate_speaker_id,
)
from backend.app.ai.policy import classify
from backend.app.ai.voice_check import _parse_aletheia_from_verify, _parse_eidos_response
from backend.app.approval.service import ApprovalError, get_employee
from backend.app.core.database import get_db

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"
LABEL_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


def _is_enrolled_locally(speaker_id: str) -> bool:
    try:
        with get_db() as conn:
            employee = get_employee(conn, speaker_id)
    except (ApprovalError, sqlite3.Error):
        return False
    return int(employee["enrolled_samples"]) > 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Make at most one live Detectif enroll call and exactly one verify call, "
            "then save the raw verify JSON."
        )
    )
    parser.add_argument("wav_path", type=Path, help="Local WAV/audio file to verify")
    parser.add_argument(
        "--label",
        required=True,
        help="Short label; saves to backend/fixtures/raw_<label>.json",
    )
    parser.add_argument(
        "--speaker-id",
        default="emp_rahul",
        help="Detectif speaker ID to enroll/verify (default: emp_rahul)",
    )
    parser.add_argument("--display-name", default=None, help="Optional enrollment display name")
    args = parser.parse_args(argv)

    if not LABEL_PATTERN.match(args.label):
        print("Error: --label must contain only letters, digits, '_' or '-'.", file=sys.stderr)
        return 2

    try:
        config = load_config()
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if config.ai_mode != "live":
        print("Error: AI_MODE must be 'live' for live_smoke.", file=sys.stderr)
        return 1
    if not config.detectif_api_key:
        print("Error: DETECTIF_API_KEY is required when AI_MODE=live.", file=sys.stderr)
        return 1

    if not args.wav_path.is_file():
        print(f"Error: audio file not found: {args.wav_path}", file=sys.stderr)
        return 2
    if args.wav_path.suffix.lower() not in SUPPORTED_AUDIO_EXTENSIONS:
        print(f"Error: unsupported audio extension: {args.wav_path.suffix}", file=sys.stderr)
        return 2

    try:
        validate_speaker_id(args.speaker_id)
    except DetectifError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    already_enrolled = _is_enrolled_locally(args.speaker_id)
    if already_enrolled:
        print(
            f"Skipped enrollment: speaker '{args.speaker_id}' is already enrolled locally.",
            file=sys.stderr,
        )

    try:
        with DetectifClient(
            config.detectif_api_key,
            base_url=config.detectif_base_url,
            backoff_seconds=(),
        ) as client:
            if not already_enrolled:
                client.enroll_speaker(
                    args.wav_path,
                    args.speaker_id,
                    display_name=args.display_name,
                )
            raw_response: dict[str, Any] = client.verify_speaker(
                args.wav_path,
                args.speaker_id,
                detect_deepfake=True,
            )
    except DetectifError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    out_path = FIXTURES_DIR / f"raw_{args.label}.json"
    out_path.write_text(
        json.dumps(raw_response, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )

    aletheia = _parse_aletheia_from_verify(raw_response)
    eidos = _parse_eidos_response(raw_response)
    risk, _reason = classify(aletheia, eidos)
    print(
        json.dumps(
            {"aletheia": aletheia, "eidos": eidos, "risk": risk},
            indent=2,
        )
    )
    print(f"Saved raw fixture to {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
