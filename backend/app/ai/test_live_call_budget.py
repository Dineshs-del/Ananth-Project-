"""Mocked tests for the per-process Detectif call budget."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

try:
    from backend.app.ai.detectif_client import (
        DEFAULT_MAX_LIVE_CALLS,
        DetectifClient,
        DetectifError,
        remaining_live_calls,
        reset_live_call_counter,
    )
except ModuleNotFoundError:
    from app.ai.detectif_client import (  # type: ignore[no-redef]
        DEFAULT_MAX_LIVE_CALLS,
        DetectifClient,
        DetectifError,
        remaining_live_calls,
        reset_live_call_counter,
    )


@pytest.fixture(autouse=True)
def _clean_budget() -> None:
    reset_live_call_counter()
    yield
    reset_live_call_counter()


def test_default_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAX_LIVE_CALLS", raising=False)
    assert remaining_live_calls() == DEFAULT_MAX_LIVE_CALLS


def test_invalid_budget_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_LIVE_CALLS", "not-a-number")
    assert remaining_live_calls() == DEFAULT_MAX_LIVE_CALLS


def test_budget_blocks_second_call(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MAX_LIVE_CALLS", "1")
    http_calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal http_calls
        http_calls += 1
        return httpx.Response(200, json={"ok": True})

    audio = tmp_path / "sample.mp3"
    audio.write_bytes(b"audio")
    transport = httpx.MockTransport(handler)

    with DetectifClient(
        "dummy-key",
        client=httpx.Client(transport=transport, base_url="https://example.test"),
        backoff_seconds=(),
    ) as client:
        assert client.detect_deepfake(audio) == {"ok": True}
        assert remaining_live_calls() == 0
        with pytest.raises(DetectifError) as exc_info:
            client.detect_deepfake(audio)

    assert exc_info.value.code == "live_call_limit_reached"
    assert "MAX_LIVE_CALLS=1" in str(exc_info.value)
    assert http_calls == 1
