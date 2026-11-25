"""Pytest suite for Day-2 policy, challenge, enrollment, client, and voice_check."""

from __future__ import annotations

import json
import re
import wave
from pathlib import Path
from typing import Any

import httpx
import pytest

try:
    from backend.app.ai import challenge, enrollment, policy, voice_check
    from backend.app.ai.detectif_client import (
        DetectifClient,
        DetectifError,
        _normalize_verify_response,
    )
    from backend.app.ai.voice_check import verify_voice
except ModuleNotFoundError:
    from app.ai import challenge, enrollment, policy, voice_check  # type: ignore[no-redef]
    from app.ai.detectif_client import (  # type: ignore[no-redef]
        DetectifClient,
        DetectifError,
        _normalize_verify_response,
    )
    from app.ai.voice_check import verify_voice  # type: ignore[no-redef]


def _sample_aletheia(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "verdict": "real",
        "score": 0.12,
        "duration_seconds": 5.5,
        "operation_id": "op_aletheia_123",
    }
    base.update(overrides)
    return base


def _sample_eidos(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "decision": "match",
        "similarity": 0.85,
        "duration_seconds": 5.5,
        "operation_id": "op_eidos_123",
    }
    base.update(overrides)
    return base


def _assert_reason_quality(reason: str) -> None:
    lower = reason.lower()
    assert isinstance(reason, str) and len(reason.strip()) > 20
    assert "evidence, not proof" in lower
    assert "next step:" in lower


def _write_wav(path: Path, seconds: float = 5.0) -> Path:
    frame_rate = 8000
    frames = max(1, int(frame_rate * seconds))
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(frame_rate)
        wav_file.writeframes(b"\x00\x00" * frames)
    return path


# --- Policy tests ---


def test_aletheia_fake_returns_fail_even_with_eidos_match() -> None:
    risk, reason = policy.classify(
        _sample_aletheia(verdict="fake", score=0.92),
        _sample_eidos(decision="match", similarity=0.91),
    )
    assert risk == "FAIL"
    _assert_reason_quality(reason)


def test_aletheia_fake_with_missing_eidos_still_fails() -> None:
    risk, reason = policy.classify(_sample_aletheia(verdict="fake", score=0.92), None)
    assert risk == "FAIL"
    _assert_reason_quality(reason)


def test_eidos_no_match_returns_fail_when_threshold_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(policy, "EIDOS_NEAR_MISS_SIMILARITY", None)
    risk, reason = policy.classify(
        _sample_aletheia(verdict="real", score=0.05),
        _sample_eidos(decision="no_match", similarity=0.55),
    )
    assert risk == "FAIL"
    _assert_reason_quality(reason)


def test_eidos_no_match_near_miss_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(policy, "EIDOS_NEAR_MISS_SIMILARITY", 0.50)

    risk_low, reason_low = policy.classify(
        _sample_aletheia(),
        _sample_eidos(decision="no_match", similarity=0.49),
    )
    assert risk_low == "FAIL"
    _assert_reason_quality(reason_low)

    risk_equal, reason_equal = policy.classify(
        _sample_aletheia(),
        _sample_eidos(decision="no_match", similarity=0.50),
    )
    assert risk_equal == "BORDERLINE"
    _assert_reason_quality(reason_equal)

    risk_above, reason_above = policy.classify(
        _sample_aletheia(),
        _sample_eidos(decision="no_match", similarity=0.62),
    )
    assert risk_above == "BORDERLINE"
    _assert_reason_quality(reason_above)


def test_real_and_match_returns_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(policy, "ALETHEIA_BORDER_SCORE", None)
    risk, reason = policy.classify(_sample_aletheia(), _sample_eidos())
    assert risk == "PASS"
    _assert_reason_quality(reason)


def test_real_and_match_with_aletheia_border_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(policy, "ALETHEIA_BORDER_SCORE", 0.40)

    risk_below, reason_below = policy.classify(
        _sample_aletheia(score=0.39), _sample_eidos()
    )
    assert risk_below == "PASS"
    _assert_reason_quality(reason_below)

    risk_equal, reason_equal = policy.classify(
        _sample_aletheia(score=0.40), _sample_eidos()
    )
    assert risk_equal == "BORDERLINE"
    _assert_reason_quality(reason_equal)

    risk_above, reason_above = policy.classify(
        _sample_aletheia(score=0.65), _sample_eidos()
    )
    assert risk_above == "BORDERLINE"
    _assert_reason_quality(reason_above)


def test_either_model_missing_returns_borderline() -> None:
    risk_both, reason_both = policy.classify(None, None)
    assert risk_both == "BORDERLINE"
    _assert_reason_quality(reason_both)

    risk_no_aletheia, reason_no_aletheia = policy.classify(None, _sample_eidos())
    assert risk_no_aletheia == "BORDERLINE"
    _assert_reason_quality(reason_no_aletheia)

    risk_no_eidos, reason_no_eidos = policy.classify(_sample_aletheia(), None)
    assert risk_no_eidos == "BORDERLINE"
    _assert_reason_quality(reason_no_eidos)


@pytest.mark.parametrize(
    "bad_aletheia",
    [
        {},
        _sample_aletheia(verdict="unknown"),
        _sample_aletheia(verdict=None),
        _sample_aletheia(score=-0.01),
        _sample_aletheia(score=1.01),
        _sample_aletheia(score=float("nan")),
        _sample_aletheia(score=True),
        _sample_aletheia(duration_seconds=0),
        _sample_aletheia(duration_seconds=-2.0),
        _sample_aletheia(operation_id=""),
    ],
)
def test_fail_closed_on_invalid_aletheia(bad_aletheia: object) -> None:
    risk, reason = policy.classify(bad_aletheia, _sample_eidos())  # type: ignore[arg-type]
    assert risk == "BORDERLINE"
    _assert_reason_quality(reason)


@pytest.mark.parametrize(
    "bad_eidos",
    [
        {},
        _sample_eidos(decision="maybe"),
        _sample_eidos(similarity=1.5),
        _sample_eidos(similarity=False),
        _sample_eidos(duration_seconds=0),
        _sample_eidos(operation_id=""),
    ],
)
def test_fail_closed_on_invalid_eidos(bad_eidos: dict[str, object]) -> None:
    risk, reason = policy.classify(_sample_aletheia(), bad_eidos)
    assert risk == "BORDERLINE"
    _assert_reason_quality(reason)


# --- Challenge tests ---


def test_challenge_word_list_and_generation() -> None:
    assert len(challenge.WORD_LIST) >= 200
    assert len(set(challenge.WORD_LIST)) == len(challenge.WORD_LIST)

    word_set = set(challenge.WORD_LIST)
    for _ in range(25):
        phrase = challenge.generate_challenge()
        # Extract the 3 chosen words from the phrase
        match = re.search(r"words are ([a-z]+), ([a-z]+), and ([a-z]+),", phrase)
        assert match is not None
        chosen = list(match.groups())
        assert len(set(chosen)) == 3
        assert all(w in word_set for w in chosen)
        digits = re.findall(r"\b\d\b", phrase)
        assert len(digits) == 2
        # Long enough to speak for ~4+ seconds (>= 15 words)
        assert len(phrase.split()) >= 15


# --- Client + Enrollment + Combined Live Voice Check tests ---


def test_verify_speaker_deepfake_error_marks_aletheia_unavailable(
    tmp_path: Path,
) -> None:
    wav = tmp_path / "clip.wav"
    _write_wav(wav)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/speakers/verify"
        assert request.headers["Authorization"] == "Bearer test-key"
        assert "Idempotency-Key" in request.headers
        return httpx.Response(
            200,
            json={
                "request_id": "req_1",
                "operation_id": "op_eidos_9",
                "result": {
                    "decision": "match",
                    "similarity": 0.88,
                    "duration_seconds": 5.1,
                },
                "deepfake": {"error": {"code": "service_unavailable", "message": "busy"}},
            },
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(base_url="https://platform.detectif.ai", transport=transport) as http_client:
        client = DetectifClient("test-key", client=http_client)
        res = client.verify_speaker(wav, "emp_001", detect_deepfake=True)

    assert res["aletheia_status"] == "unavailable"
    assert res["deepfake"] is None
    assert res["result"]["decision"] == "match"


def _documented_verify_response(deepfake: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "request_id": "req_docs_shape",
        "operation_id": "op_verify_docs",
        "object": "speaker.verification",
        "speaker_id": "emp_001",
        "result": {
            "decision": "match",
            "similarity": 0.7412,
            "threshold": 0.5,
            "similarity_semantics": "Higher means the voices are more alike.",
            "duration_seconds": 4.1,
        },
        "deepfake": deepfake,
        "model": {"name": "eidos", "version": "2026.10"},
        "credits": {"charged": 2, "balance": 98},
        "latency_ms": 1200,
        "idempotent_replay": False,
    }


def test_parsers_read_documented_combined_verify_shape() -> None:
    raw = _documented_verify_response(
        {
            "verdict": "real",
            "label": "genuine",
            "is_synthetic": False,
            "score": 0.08,
            "score_semantics": "Higher means more likely AI-generated.",
            "duration_seconds": 4.1,
            "scored_seconds": None,
            "segments": [{"start": 0.0, "end": 2.0, "score": 0.07, "verdict": "real"}],
            "operation_id": "op_deepfake_docs",
            "credits_charged": 1,
            "model": {"name": "aletheia", "version": "2026.10"},
        }
    )

    assert voice_check._parse_eidos_response(raw) == {
        "decision": "match",
        "similarity": 0.7412,
        "duration_seconds": 4.1,
        "operation_id": "op_verify_docs",
    }
    assert voice_check._parse_aletheia_from_verify(raw) == {
        "verdict": "real",
        "score": 0.08,
        "duration_seconds": 4.1,
        "operation_id": "op_deepfake_docs",
    }


def test_normalizer_and_parsers_fail_closed_on_documented_deepfake_error() -> None:
    raw = _documented_verify_response(
        {
            "error": {
                "type": "internal",
                "code": "service_unavailable",
                "message": "busy",
            },
            "operation_id": "op_deepfake_error",
            "credits_charged": 0,
        }
    )

    normalized = _normalize_verify_response(raw)
    assert normalized["aletheia_status"] == "unavailable"
    assert normalized["deepfake"] is None
    assert voice_check._parse_aletheia_from_verify(normalized) is None
    assert voice_check._parse_eidos_response(normalized) == {
        "decision": "match",
        "similarity": 0.7412,
        "duration_seconds": 4.1,
        "operation_id": "op_verify_docs",
    }


def test_enroll_employee_local_validation_and_409_cap(tmp_path: Path) -> None:
    bad_ext = tmp_path / "clip.txt"
    bad_ext.write_text("not audio")
    with pytest.raises(enrollment.EnrollmentError, match="Unsupported audio format"):
        enrollment.enroll_employee("emp_001", [bad_ext])

    valid_files = []
    for i in range(6):
        p = tmp_path / f"sample_{i}.wav"
        _write_wav(p)
        valid_files.append(p)

    with pytest.raises(enrollment.EnrollmentError, match="maximum of 5"):
        enrollment.enroll_employee("emp_001", valid_files)

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, json={"request_id": "r1", "operation_id": "op1"})
        return httpx.Response(
            409,
            json={
                "request_id": "r2",
                "error": {
                    "type": "conflict",
                    "code": "too_many_samples",
                    "message": "Speaker already has 5 samples.",
                },
            },
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(base_url="https://platform.detectif.ai", transport=transport) as http_client:
        client = DetectifClient("test-key", client=http_client)
        summary = enrollment.enroll_employee("emp_001", valid_files[:2], client=client)

    assert summary["cap_reached"] is True
    assert len(summary["enrolled"]) == 1
    assert len(summary["skipped"]) == 1
    assert "maximum of 5" in summary["message"]


def test_verify_voice_live_single_combined_call_and_deepfake_error_fail_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    wav = tmp_path / "live.wav"
    _write_wav(wav)

    monkeypatch.setenv("AI_MODE", "live")
    monkeypatch.setenv("DETECTIF_API_KEY", "dummy-key")

    call_count = 0

    def fake_verify_speaker(
        self: DetectifClient,
        path: str | Path,
        speaker_id: str,
        detect_deepfake: bool = True,
    ) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        assert speaker_id == "emp_001"
        assert detect_deepfake is True
        return {
            "request_id": "req_live",
            "operation_id": "op_live_1",
            "result": {
                "decision": "match",
                "similarity": 0.91,
                "duration_seconds": 5.4,
            },
            "deepfake": None,
            "aletheia_status": "unavailable",
        }

    monkeypatch.setattr(DetectifClient, "verify_speaker", fake_verify_speaker)

    out = verify_voice(str(wav), "emp_001")
    assert call_count == 1
    assert set(out.keys()) == {"aletheia", "eidos", "risk", "reason"}
    assert out["aletheia"] is None
    assert out["eidos"] == {
        "decision": "match",
        "similarity": 0.91,
        "duration_seconds": 5.4,
        "operation_id": "op_live_1",
    }
    assert out["risk"] == "BORDERLINE"
    _assert_reason_quality(out["reason"])


@pytest.mark.parametrize(
    ("scenario", "expected_risk"),
    [
        ("pass", "PASS"),
        ("borderline", "BORDERLINE"),
        ("fail", "FAIL"),
        ("error", "BORDERLINE"),
        ("does_not_exist", "BORDERLINE"),
    ],
)
def test_verify_voice_fixture_mode(
    monkeypatch: pytest.MonkeyPatch, scenario: str, expected_risk: str
) -> None:
    monkeypatch.setenv("AI_MODE", "fixture")
    monkeypatch.setenv("FIXTURE_SCENARIO", scenario)
    monkeypatch.delenv("DETECTIF_API_KEY", raising=False)

    out = verify_voice("unused.wav", "emp_001")
    assert set(out.keys()) == {"aletheia", "eidos", "risk", "reason"}
    assert out["risk"] == expected_risk
    _assert_reason_quality(out["reason"])


def test_stored_fixture_risks_match_policy_classification() -> None:
    fixture_paths = sorted(voice_check.FIXTURES_DIR.glob("verify_*.json"))
    assert fixture_paths

    for fixture_path in fixture_paths:
        data = json.loads(fixture_path.read_text(encoding="utf-8"))
        assert set(data) == voice_check.REQUIRED_OUTPUT_KEYS
        risk, _reason = policy.classify(data["aletheia"], data["eidos"])
        assert data["risk"] == risk, fixture_path.name


@pytest.mark.parametrize(
    ("status", "payload", "expected_code", "expected_fragment"),
    [
        (
            402,
            {"error": {"code": "payment_required", "message": "no credits"}},
            "no_credits",
            "no credits",
        ),
        (
            429,
            {"error": {"code": "rate_limited", "message": "slow down"}},
            "rate_limited",
            "rate limit reached",
        ),
        (
            404,
            {"error": {"code": "speaker_not_found", "message": "missing"}},
            "speaker_not_enrolled",
            "enrolled speaker",
        ),
    ],
)
def test_client_maps_live_api_errors_to_clear_messages(
    tmp_path: Path,
    status: int,
    payload: dict[str, Any],
    expected_code: str,
    expected_fragment: str,
) -> None:
    wav = _write_wav(tmp_path / "clip.wav")
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, json=payload)

    transport = httpx.MockTransport(handler)
    with httpx.Client(base_url="https://platform.detectif.ai", transport=transport) as http_client:
        client = DetectifClient("test-key", client=http_client, backoff_seconds=())
        with pytest.raises(DetectifError) as exc_info:
            client.verify_speaker(wav, "emp_001")

    assert calls == 1
    assert exc_info.value.code == expected_code
    assert expected_fragment in str(exc_info.value).lower()
    assert "test-key" not in str(exc_info.value)


def test_client_timeout_fails_closed_with_clear_message(tmp_path: Path) -> None:
    wav = _write_wav(tmp_path / "clip.wav")
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.TimeoutException("timed out", request=request)

    transport = httpx.MockTransport(handler)
    with httpx.Client(base_url="https://platform.detectif.ai", transport=transport) as http_client:
        client = DetectifClient("test-key", client=http_client, backoff_seconds=())
        with pytest.raises(DetectifError) as exc_info:
            client.verify_speaker(wav, "emp_001")

    assert calls == 1
    assert exc_info.value.code == "timeout"
    assert "timed out" in str(exc_info.value).lower()
    assert "test-key" not in str(exc_info.value)


def test_client_rejects_webm_before_upload(tmp_path: Path) -> None:
    webm = tmp_path / "clip.webm"
    webm.write_bytes(b"fake webm")

    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("webm should be rejected before any API call")

    transport = httpx.MockTransport(handler)
    with httpx.Client(base_url="https://platform.detectif.ai", transport=transport) as http_client:
        client = DetectifClient("test-key", client=http_client, backoff_seconds=())
        with pytest.raises(DetectifError) as exc_info:
            client.verify_speaker(webm, "emp_001")

    assert exc_info.value.code == "unsupported_format"
    assert "unsupported audio format '.webm'" in str(exc_info.value).lower()


@pytest.mark.parametrize(
    ("seconds", "expected_code", "expected_fragment"),
    [
        (3.5, "audio_too_short", "too short"),
        (121.0, "audio_too_long", "too long"),
    ],
)
def test_client_rejects_wav_outside_4_to_120_seconds(
    tmp_path: Path,
    seconds: float,
    expected_code: str,
    expected_fragment: str,
) -> None:
    wav = _write_wav(tmp_path / "clip.wav", seconds=seconds)

    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("invalid-duration WAV should be rejected before any API call")

    transport = httpx.MockTransport(handler)
    with httpx.Client(base_url="https://platform.detectif.ai", transport=transport) as http_client:
        client = DetectifClient("test-key", client=http_client, backoff_seconds=())
        with pytest.raises(DetectifError) as exc_info:
            client.verify_speaker(wav, "emp_001")

    assert exc_info.value.code == expected_code
    assert expected_fragment in str(exc_info.value).lower()


@pytest.mark.parametrize(
    ("error", "expected_fragment"),
    [
        (
            DetectifError(
                "Detectif account has no credits or its plan quota is exhausted; request failed closed.",
                code="no_credits",
                status=402,
            ),
            "credit",
        ),
        (
            DetectifError(
                "Detectif rate limit reached; wait for the limit to reset before retrying.",
                code="rate_limited",
                status=429,
            ),
            "rate limit",
        ),
        (
            DetectifError("Detectif request timed out; request failed closed.", code="timeout"),
            "timed out",
        ),
        (
            DetectifError(
                "Detectif could not find the enrolled speaker; confirm the speaker_id is enrolled before retrying.",
                code="speaker_not_enrolled",
                status=404,
            ),
            "enrolled",
        ),
        (
            DetectifError("Unsupported audio format '.webm'.", code="unsupported_format"),
            "unsupported audio format",
        ),
        (
            DetectifError("Audio is too short (3.5s).", code="audio_too_short"),
            "too short",
        ),
        (
            DetectifError("Audio is too long (121.0s).", code="audio_too_long"),
            "too long",
        ),
    ],
)
def test_verify_voice_api_errors_fail_closed_with_clear_reason(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    error: DetectifError,
    expected_fragment: str,
) -> None:
    wav = _write_wav(tmp_path / "live.wav")
    monkeypatch.setenv("AI_MODE", "live")
    monkeypatch.setenv("DETECTIF_API_KEY", "dummy-key")
    calls = 0

    def fake_verify_speaker(
        self: DetectifClient,
        path: str | Path,
        speaker_id: str,
        detect_deepfake: bool = True,
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        assert str(path) == str(wav)
        assert speaker_id == "emp_001"
        assert detect_deepfake is True
        raise error

    monkeypatch.setattr(DetectifClient, "verify_speaker", fake_verify_speaker)

    out = verify_voice(str(wav), "emp_001")

    assert calls == 1
    assert set(out.keys()) == {"aletheia", "eidos", "risk", "reason"}
    assert out["aletheia"] is None
    assert out["eidos"] is None
    assert out["risk"] == "BORDERLINE"
    assert expected_fragment in out["reason"].lower()
    assert "dummy-key" not in out["reason"]
    _assert_reason_quality(out["reason"])
