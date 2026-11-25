"""Aletheia-only voice-note analysis and plain-text Telegram reply formatter."""

from __future__ import annotations

import json
import logging
import math
import re
from pathlib import Path
from typing import Any, Literal

from ..ai import policy
from ..ai.config import ConfigError, load_config
from ..ai.detectif_client import DetectifClient, DetectifError

logger = logging.getLogger("voicegate.bot.analyze")
Risk = Literal["PASS", "BORDERLINE", "FAIL"]

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures"
SCENARIO_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")

EVIDENCE_NOTICE = "This is evidence, not proof."
NEXT_STEP_UNSAFE = (
    "Do not send money or share codes. Call the person back on a number you already know."
)
NEXT_STEP_REAL = (
    "No synthetic-voice signs found, but still confirm anything urgent on a number you already know."
)


def _is_valid_score(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value) and 0.0 <= float(value) <= 1.0


def _is_positive_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value) and float(value) > 0.0


def _validate_aletheia(aletheia: Any) -> dict[str, Any] | None:
    if not isinstance(aletheia, dict):
        return None
    verdict = aletheia.get("verdict")
    score = aletheia.get("score")
    duration = aletheia.get("duration_seconds")
    operation_id = aletheia.get("operation_id")

    if verdict not in ("real", "fake"):
        return None
    if not _is_valid_score(score):
        return None
    if not _is_positive_number(duration):
        return None
    if not isinstance(operation_id, str) or not operation_id.strip():
        return None

    return {
        "verdict": verdict,
        "score": float(score),
        "duration_seconds": float(duration),
        "operation_id": operation_id.strip(),
    }


def classify_aletheia_only(aletheia: dict[str, Any] | None) -> tuple[Risk, str]:
    """Fail-closed Aletheia-only classification honouring policy.ALETHEIA_BORDER_SCORE."""
    parsed = _validate_aletheia(aletheia)
    if parsed is None:
        return (
            "BORDERLINE",
            f"The voice check could not be completed. {EVIDENCE_NOTICE} {NEXT_STEP_UNSAFE}",
        )

    verdict = parsed["verdict"]
    score = parsed["score"]

    if verdict == "fake":
        return (
            "FAIL",
            f"Synthetic voice signs detected (verdict: fake, score: {score:.2f} on 0 to 1 scale). "
            f"{EVIDENCE_NOTICE} {NEXT_STEP_UNSAFE}",
        )

    # verdict == "real"
    if policy.ALETHEIA_BORDER_SCORE is not None and score >= policy.ALETHEIA_BORDER_SCORE:
        return (
            "BORDERLINE",
            f"Voice check returned real (score: {score:.2f} on 0 to 1 scale), "
            f"but the score is close to the review threshold. "
            f"{EVIDENCE_NOTICE} {NEXT_STEP_UNSAFE}",
        )

    return (
        "PASS",
        f"Voice check returned real (score: {score:.2f} on 0 to 1 scale). "
        f"{EVIDENCE_NOTICE} {NEXT_STEP_REAL}",
    )


def _load_fixture_aletheia(scenario: str) -> dict[str, Any]:
    if not SCENARIO_PATTERN.match(scenario):
        risk, reason = classify_aletheia_only(None)
        return {"aletheia": None, "risk": risk, "reason": reason}

    fixture_path = FIXTURES_DIR / f"verify_{scenario}.json"
    if not fixture_path.is_file():
        risk, reason = classify_aletheia_only(None)
        return {"aletheia": None, "risk": risk, "reason": reason}

    try:
        data = json.loads(fixture_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        risk, reason = classify_aletheia_only(None)
        return {"aletheia": None, "risk": risk, "reason": reason}

    raw_aletheia = data.get("aletheia") if isinstance(data, dict) else None
    parsed = _validate_aletheia(raw_aletheia)

    # Preserve borderline sample fixture behaviour even while ALETHEIA_BORDER_SCORE is None.
    if parsed is not None and isinstance(data, dict) and data.get("risk") == "BORDERLINE":
        return {
            "aletheia": parsed,
            "risk": "BORDERLINE",
            "reason": (
                f"Voice check returned {parsed['verdict']} "
                f"(score: {parsed['score']:.2f} on 0 to 1 scale), "
                f"and the result is marked unsure. {EVIDENCE_NOTICE} {NEXT_STEP_UNSAFE}"
            ),
        }

    risk, reason = classify_aletheia_only(parsed)
    return {"aletheia": parsed, "risk": risk, "reason": reason}


def _parse_detect_response(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Extract only documented Detectif /v1/deepfake/detect fields."""
    result = raw.get("result")
    operation_id = raw.get("operation_id")
    if not isinstance(result, dict) or not isinstance(operation_id, str) or not operation_id:
        return None
    candidate = {
        "verdict": result.get("verdict"),
        "score": result.get("score"),
        "duration_seconds": result.get("duration_seconds"),
        "operation_id": operation_id,
    }
    return _validate_aletheia(candidate)


def analyze_audio(wav_path: str | Path) -> dict[str, Any]:
    """Run Aletheia deepfake detection on a WAV file, honouring AI_MODE=fixture|live.

    Returns: {"aletheia": dict | None, "risk": "PASS"|"BORDERLINE"|"FAIL", "reason": str}
    Always fails closed on any error or missing field.
    """
    try:
        config = load_config()
    except ConfigError:
        risk, reason = classify_aletheia_only(None)
        return {"aletheia": None, "risk": risk, "reason": reason}

    if config.ai_mode == "fixture":
        return _load_fixture_aletheia(config.fixture_scenario)

    try:
        assert config.detectif_api_key is not None
        with DetectifClient(
            config.detectif_api_key,
            base_url=config.detectif_base_url,
        ) as client:
            raw = client.detect_deepfake(wav_path)
    except DetectifError as exc:
        logger.warning(
            "Detectif Aletheia API failed (status=%s, code=%s).",
            exc.status,
            exc.code,
        )
        risk, reason = classify_aletheia_only(None)
        return {"aletheia": None, "risk": risk, "reason": reason}
    except OSError:
        logger.warning("Could not read converted WAV file for Aletheia analysis.")
        risk, reason = classify_aletheia_only(None)
        return {"aletheia": None, "risk": risk, "reason": reason}

    aletheia = _parse_detect_response(raw)
    risk, reason = classify_aletheia_only(aletheia)
    return {"aletheia": aletheia, "risk": risk, "reason": reason}


def format_telegram_reply(analysis: dict[str, Any]) -> str:
    """Build a short, plain-text Telegram reply with no markdown tables or % claims."""
    aletheia = _validate_aletheia(analysis.get("aletheia"))
    risk = analysis.get("risk")

    if aletheia is None or risk not in ("PASS", "BORDERLINE", "FAIL"):
        return (
            "Voice check unavailable: the check could not be done right now.\n"
            f"{EVIDENCE_NOTICE}\n"
            f"Next step: {NEXT_STEP_UNSAFE}"
        )

    verdict = aletheia["verdict"]
    score = aletheia["score"]

    if risk == "PASS" and verdict == "real":
        next_step = NEXT_STEP_REAL
    else:
        next_step = NEXT_STEP_UNSAFE

    return (
        f"Verdict: {verdict}\n"
        f"Score (0 to 1): {score:.2f}\n"
        f"Assessment: {risk}\n"
        f"{EVIDENCE_NOTICE}\n"
        f"Next step: {next_step}"
    )
