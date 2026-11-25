"""Pytest suite for Telegram bot analysis, reply formatting, audio bounds, and rate limit (no network)."""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

try:
    from backend.app.ai import policy
    from backend.app.bot import analyze, audio
    from backend.app.bot.bot import UserRateLimiter
except ModuleNotFoundError:
    from app.ai import policy  # type: ignore[no-redef]
    from app.bot import analyze, audio  # type: ignore[no-redef]
    from app.bot.bot import UserRateLimiter  # type: ignore[no-redef]


def _write_pcm_wav(path: Path, duration_seconds: float, sample_rate: int = 16000) -> None:
    frames = int(duration_seconds * sample_rate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * frames)


@pytest.mark.parametrize(
    ("scenario", "expected_risk", "expected_verdict"),
    [
        ("pass", "PASS", "real"),
        ("borderline", "BORDERLINE", "real"),
        ("fail", "FAIL", "fake"),
        ("error", "BORDERLINE", None),
        ("nonexistent_scenario", "BORDERLINE", None),
    ],
)
def test_analyze_audio_fixture_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    scenario: str,
    expected_risk: str,
    expected_verdict: str | None,
) -> None:
    wav = tmp_path / "clip.wav"
    _write_pcm_wav(wav, 5.0)

    monkeypatch.setenv("AI_MODE", "fixture")
    monkeypatch.setenv("FIXTURE_SCENARIO", scenario)
    monkeypatch.delenv("DETECTIF_API_KEY", raising=False)

    res = analyze.analyze_audio(wav)
    assert set(res.keys()) == {"aletheia", "risk", "reason"}
    assert res["risk"] == expected_risk

    reply = analyze.format_telegram_reply(res)
    assert "%" not in reply  # Never convert score into a "% fake" or "% real" claim
    assert "This is evidence, not proof." in reply
    assert "|" not in reply  # No markdown tables

    if expected_verdict is None:
        assert res["aletheia"] is None
        assert "could not be done" in reply.lower()
        assert "Do not send money or share codes. Call the person back on a number you already know." in reply
    else:
        assert res["aletheia"] is not None
        assert res["aletheia"]["verdict"] == expected_verdict
        assert f"Verdict: {expected_verdict}" in reply
        if expected_risk == "PASS":
            assert "No synthetic-voice signs found, but still confirm anything urgent on a number you already know." in reply
        else:
            assert "Do not send money or share codes. Call the person back on a number you already know." in reply


def test_aletheia_border_threshold_respected(monkeypatch: pytest.MonkeyPatch) -> None:
    sample = {
        "verdict": "real",
        "score": 0.45,
        "duration_seconds": 6.0,
        "operation_id": "op_1",
    }
    monkeypatch.setattr(policy, "ALETHEIA_BORDER_SCORE", None)
    risk_unset, _ = analyze.classify_aletheia_only(sample)
    assert risk_unset == "PASS"

    monkeypatch.setattr(policy, "ALETHEIA_BORDER_SCORE", 0.40)
    risk_set, _ = analyze.classify_aletheia_only(sample)
    assert risk_set == "BORDERLINE"


def test_fail_closed_on_malformed_aletheia() -> None:
    for bad in (
        None,
        {},
        {"verdict": "unknown", "score": 0.2, "duration_seconds": 5.0, "operation_id": "op"},
        {"verdict": "real", "score": True, "duration_seconds": 5.0, "operation_id": "op"},
        {"verdict": "real", "score": 1.5, "duration_seconds": 5.0, "operation_id": "op"},
    ):
        risk, _ = analyze.classify_aletheia_only(bad)  # type: ignore[arg-type]
        assert risk == "BORDERLINE"


def test_audio_duration_bounds(tmp_path: Path) -> None:
    short_wav = tmp_path / "short.wav"
    _write_pcm_wav(short_wav, 2.0)
    with pytest.raises(audio.AudioDurationError, match="too short"):
        audio.validate_duration(audio.read_wav_duration(short_wav))

    ok_wav = tmp_path / "ok.wav"
    _write_pcm_wav(ok_wav, 5.0)
    assert audio.validate_duration(audio.read_wav_duration(ok_wav)) == pytest.approx(5.0, abs=0.05)

    with pytest.raises(audio.AudioDurationError, match="too long"):
        audio.validate_duration(125.0)


def test_per_user_rate_limiter() -> None:
    now = [1000.0]
    limiter = UserRateLimiter(max_checks=5, window_seconds=600, clock=lambda: now[0])

    for _ in range(5):
        allowed, wait = limiter.allow(user_id=42)
        assert allowed is True and wait == 0

    blocked, wait = limiter.allow(user_id=42)
    assert blocked is False and wait > 0

    # Different user is unaffected
    other_allowed, _ = limiter.allow(user_id=99)
    assert other_allowed is True

    # After 10-minute window passes, user 42 is allowed again
    now[0] += 601.0
    allowed_after, _ = limiter.allow(user_id=42)
    assert allowed_after is True
