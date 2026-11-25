"""Mocked tests for the one-shot live Detectif smoke script."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

try:
    from backend.scripts import live_smoke
except ModuleNotFoundError:
    from scripts import live_smoke  # type: ignore[no-redef]


@pytest.fixture()
def live_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("AI_MODE", "live")
    monkeypatch.setenv("DETECTIF_API_KEY", "dummy-key-for-tests")
    monkeypatch.setattr(live_smoke, "FIXTURES_DIR", tmp_path)
    return tmp_path


def test_live_smoke_makes_one_verify_and_saves_sanitized_raw(
    live_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    instances: list[object] = []

    class FakeClient:
        def __init__(self, api_key: str, **kwargs: object) -> None:
            self.verify_calls: list[tuple[str, str, bool]] = []
            instances.append(self)

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def close(self) -> None:
            return None

        def enroll_speaker(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("live_smoke must not enroll in this scenario")

        def verify_speaker(self, path: Path, speaker_id: str, detect_deepfake: bool = True) -> dict:
            self.verify_calls.append((str(path), speaker_id, detect_deepfake))
            return {
                "result": {
                    "deepfake": {"verdict": "real"},
                    "headers": {"Authorization": "Bearer secret"},
                },
                "api_key": "secret",
            }

    monkeypatch.setattr(live_smoke, "DetectifClient", FakeClient)
    monkeypatch.setattr(live_smoke, "_is_enrolled_locally", lambda _speaker: True)
    monkeypatch.setattr(live_smoke, "_parse_aletheia_from_verify", lambda raw: {"verdict": "real"})
    monkeypatch.setattr(live_smoke, "_parse_eidos_response", lambda raw: {"decision": "match"})
    monkeypatch.setattr(live_smoke, "classify", lambda aletheia, eidos: ("PASS", "ok"))

    wav = live_env / "sample.wav"
    wav.write_bytes(b"RIFFdemo")

    assert live_smoke.main([str(wav), "--label", "smoke"]) == 0
    assert len(instances) == 1
    verify_calls = getattr(instances[0], "verify_calls")
    assert len(verify_calls) == 1
    assert verify_calls[0][2] is True

    saved = json.loads((live_env / "raw_smoke.json").read_text())
    assert "api_key" not in saved
    assert "headers" not in saved["result"]

    out = capsys.readouterr().out
    assert json.loads(out) == {
        "aletheia": {"verdict": "real"},
        "eidos": {"decision": "match"},
        "risk": "PASS",
    }
    assert "secret" not in out


def test_live_smoke_rejects_fixture_mode(
    live_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AI_MODE", "fixture")
    wav = live_env / "sample.wav"
    wav.write_bytes(b"RIFFdemo")

    assert live_smoke.main([str(wav), "--label", "nope"]) == 1
    assert "AI_MODE" in capsys.readouterr().err
