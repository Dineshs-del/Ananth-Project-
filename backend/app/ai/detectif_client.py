"""Minimal HTTP client for the Detectif API with idempotent retries."""

from __future__ import annotations

import mimetypes
import os
import re
import threading
import time
import uuid
import wave
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable

import httpx

SUPPORTED_AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".flac", ".ogg"}
SPEAKER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
RETRY_AFTER_STATUSES = {429, 503}
BACKOFF_STATUSES = {500, 502, 504}
DEFAULT_BACKOFF_SECONDS = (1.0, 2.0, 4.0)
MAX_RETRY_AFTER_SECONDS = 60.0
MIN_AUDIO_SECONDS = 4.0
MAX_AUDIO_SECONDS = 120.0
DEFAULT_MAX_LIVE_CALLS = 100
MAX_LIVE_CALLS_CEILING = 100_000
_live_call_lock = threading.Lock()
_live_calls_used = 0
NO_CREDITS_CODES = {
    "no_credits",
    "insufficient_credits",
    "insufficient_quota",
    "payment_required",
    "quota_exceeded",
}
RATE_LIMIT_CODES = {"rate_limit", "rate_limited", "rate_limit_exceeded", "too_many_requests"}
SPEAKER_NOT_FOUND_CODES = {
    "no_such_speaker",
    "speaker_missing",
    "speaker_not_enrolled",
    "speaker_not_found",
    "unknown_speaker",
}


class DetectifError(Exception):
    """Typed error raised on Detectif API or upload failures."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: str | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.request_id = request_id

    def __str__(self) -> str:
        parts = [self.message]
        if self.status is not None:
            parts.append(f"status={self.status}")
        if self.code:
            parts.append(f"code={self.code}")
        if self.request_id:
            parts.append(f"request_id={self.request_id}")
        return " | ".join(parts)


def _max_live_calls() -> int:
    raw = os.getenv("MAX_LIVE_CALLS", "").strip()
    if not raw:
        return DEFAULT_MAX_LIVE_CALLS
    try:
        return max(0, min(int(raw), MAX_LIVE_CALLS_CEILING))
    except ValueError:
        return DEFAULT_MAX_LIVE_CALLS


def remaining_live_calls() -> int:
    with _live_call_lock:
        return max(0, _max_live_calls() - _live_calls_used)


def reset_live_call_counter() -> None:
    global _live_calls_used
    with _live_call_lock:
        _live_calls_used = 0


def _consume_live_call() -> None:
    global _live_calls_used
    with _live_call_lock:
        limit = _max_live_calls()
        if _live_calls_used >= limit:
            raise DetectifError(
                f"Detectif call limit reached (MAX_LIVE_CALLS={limit}) for this process; "
                "no further live calls are permitted. Restart the service to reset the budget.",
                code="live_call_limit_reached",
            )
        _live_calls_used += 1


def validate_speaker_id(speaker_id: str) -> str:
    """Validate speaker_id against Detectif's documented pattern."""
    if not isinstance(speaker_id, str) or not SPEAKER_ID_PATTERN.match(speaker_id):
        raise DetectifError(
            "Invalid speaker_id: must be 1-64 characters matching [A-Za-z0-9_.:-].",
            code="invalid_speaker_id",
        )
    return speaker_id


def _parse_retry_after(header_value: str | None, fallback: float) -> float:
    """Parse Retry-After header (seconds or HTTP-date), bounded to a safe cap."""
    if not header_value:
        return fallback
    value = header_value.strip()
    try:
        seconds = float(value)
        return max(0.0, min(seconds, MAX_RETRY_AFTER_SECONDS))
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = (dt - datetime.now(timezone.utc)).total_seconds()
        return max(0.0, min(delta, MAX_RETRY_AFTER_SECONDS))
    except (TypeError, ValueError):
        return fallback


def _extract_error(response: httpx.Response) -> DetectifError:
    """Parse Detectif error payload without exposing headers or credentials."""
    code: str | None = None
    message = f"Detectif API request failed with HTTP {response.status_code}."
    request_id: str | None = None

    try:
        payload = response.json()
    except ValueError:
        payload = None

    if isinstance(payload, dict):
        req_id = payload.get("request_id")
        if isinstance(req_id, str) and req_id:
            request_id = req_id
        err = payload.get("error")
        if isinstance(err, dict):
            if isinstance(err.get("code"), str):
                code = err["code"]
            if isinstance(err.get("message"), str) and err["message"]:
                message = err["message"]

    normalized_code = code.lower() if code else ""
    if response.status_code == 402 or normalized_code in NO_CREDITS_CODES:
        return DetectifError(
            message="Detectif account has no credits or its plan quota is exhausted; request failed closed.",
            status=response.status_code,
            code="no_credits",
            request_id=request_id,
        )
    if response.status_code == 429 or normalized_code in RATE_LIMIT_CODES:
        return DetectifError(
            message="Detectif rate limit reached; wait for the limit to reset before retrying.",
            status=response.status_code,
            code="rate_limited",
            request_id=request_id,
        )
    if response.status_code == 404 or normalized_code in SPEAKER_NOT_FOUND_CODES:
        return DetectifError(
            message="Detectif could not find the enrolled speaker; confirm the speaker_id is enrolled before retrying.",
            status=response.status_code,
            code="speaker_not_enrolled",
            request_id=request_id,
        )
    return DetectifError(
        message=message,
        status=response.status_code,
        code=code,
        request_id=request_id,
    )


def _normalize_verify_response(body: dict[str, Any]) -> dict[str, Any]:
    """Handle deepfake.error inside a 200 verify response as 'aletheia unavailable'."""
    normalized = dict(body)

    # Detectif may return deepfake at the top level or nested inside result.
    deepfake = normalized.get("deepfake")
    nested_in_result = False
    if deepfake is None and isinstance(normalized.get("result"), dict):
        deepfake = normalized["result"].get("deepfake")
        nested_in_result = deepfake is not None

    if isinstance(deepfake, dict) and deepfake.get("error") is not None:
        normalized["deepfake"] = None
        if nested_in_result and isinstance(normalized.get("result"), dict):
            result_copy = dict(normalized["result"])
            result_copy["deepfake"] = None
            normalized["result"] = result_copy
        normalized["aletheia_status"] = "unavailable"
    elif deepfake is None:
        normalized["aletheia_status"] = "unavailable"
    else:
        normalized["aletheia_status"] = "available"

    return normalized


def _validate_audio_length(path: Path) -> None:
    if path.suffix.lower() != ".wav":
        return
    try:
        with wave.open(str(path), "rb") as wav_file:
            frames = wav_file.getnframes()
            frame_rate = wav_file.getframerate()
    except (OSError, EOFError, wave.Error) as exc:
        raise DetectifError(
            "WAV audio could not be read for duration validation.",
            code="invalid_audio",
        ) from exc
    if frame_rate <= 0:
        raise DetectifError(
            "WAV audio has an invalid frame rate.",
            code="invalid_audio",
        )
    duration = frames / float(frame_rate)
    if duration < MIN_AUDIO_SECONDS:
        raise DetectifError(
            f"Audio is too short ({duration:.1f}s); Detectif requires at least "
            f"{MIN_AUDIO_SECONDS:.0f} seconds.",
            code="audio_too_short",
        )
    if duration > MAX_AUDIO_SECONDS:
        raise DetectifError(
            f"Audio is too long ({duration:.1f}s); Detectif allows at most "
            f"{MAX_AUDIO_SECONDS:.0f} seconds.",
            code="audio_too_long",
        )


class DetectifClient:
    """Thin Detectif API client. Never logs headers or API keys."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://platform.detectif.ai",
        timeout: float = 30.0,
        backoff_seconds: tuple[float, ...] = DEFAULT_BACKOFF_SECONDS,
        sleep_fn: Callable[[float], None] = time.sleep,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key or not api_key.strip():
            raise DetectifError("Missing Detectif API key.", code="missing_api_key")
        self._api_key = api_key.strip()
        self._base_url = base_url.rstrip("/")
        self._backoff_seconds = backoff_seconds
        self._sleep = sleep_fn
        self._owns_client = client is None
        self._client = client or httpx.Client(base_url=self._base_url, timeout=timeout)

    def __enter__(self) -> DetectifClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _post(
        self,
        endpoint: str,
        *,
        file_path: str | Path,
        data: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """POST a multipart audio upload with a stable Idempotency-Key across retries."""
        path = Path(file_path)
        if not path.is_file():
            raise DetectifError("Audio file not found or unreadable.", code="invalid_audio")
        if path.suffix.lower() not in SUPPORTED_AUDIO_EXTENSIONS:
            raise DetectifError(
                f"Unsupported audio format '{path.suffix.lower()}'.",
                code="unsupported_format",
            )
        _validate_audio_length(path)

        audio_bytes = path.read_bytes()
        if not audio_bytes:
            raise DetectifError("Audio file is empty.", code="empty_audio")

        mime_type, _ = mimetypes.guess_type(path.name)
        content_type = mime_type or "application/octet-stream"

        # Reuse the exact same Idempotency-Key across all retries for this upload.
        idempotency_key = str(uuid.uuid4())
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Idempotency-Key": idempotency_key,
        }

        max_attempts = len(self._backoff_seconds) + 1
        last_error: DetectifError | None = None

        for attempt in range(max_attempts):
            _consume_live_call()
            try:
                response = self._client.post(
                    endpoint,
                    headers=headers,
                    data=data or {},
                    files={"file": (path.name, audio_bytes, content_type)},
                )
            except httpx.TimeoutException as exc:
                last_error = DetectifError(
                    "Detectif request timed out; request failed closed.",
                    code="timeout",
                )
                if attempt < len(self._backoff_seconds):
                    self._sleep(self._backoff_seconds[attempt])
                    continue
                raise last_error from exc
            except httpx.HTTPError as exc:
                last_error = DetectifError(
                    f"Network error contacting Detectif API: {exc.__class__.__name__}.",
                    code="network_error",
                )
                if attempt < len(self._backoff_seconds):
                    self._sleep(self._backoff_seconds[attempt])
                    continue
                raise last_error from exc

            if 200 <= response.status_code < 300:
                try:
                    body = response.json()
                except ValueError as exc:
                    raise DetectifError(
                        "Detectif API returned non-JSON response.",
                        status=response.status_code,
                        code="invalid_json",
                    ) from exc
                if not isinstance(body, dict):
                    raise DetectifError(
                        "Detectif API returned unexpected response shape.",
                        status=response.status_code,
                        code="invalid_response",
                    )
                return body

            error = _extract_error(response)
            last_error = error

            if response.status_code in RETRY_AFTER_STATUSES:
                if attempt < len(self._backoff_seconds):
                    delay = _parse_retry_after(
                        response.headers.get("Retry-After"),
                        self._backoff_seconds[attempt],
                    )
                    self._sleep(delay)
                    continue
                raise error

            if response.status_code in BACKOFF_STATUSES:
                if attempt < len(self._backoff_seconds):
                    self._sleep(self._backoff_seconds[attempt])
                    continue
                raise error

            # All other 4xx/non-retryable statuses fail immediately.
            raise error

        assert last_error is not None
        raise last_error

    def detect_deepfake(self, path: str | Path) -> dict[str, Any]:
        """Run Aletheia deepfake detection on an audio file."""
        return self._post("/v1/deepfake/detect", file_path=path)

    def enroll_speaker(
        self,
        path: str | Path,
        speaker_id: str,
        display_name: str | None = None,
    ) -> dict[str, Any]:
        """Enroll a voice sample for a speaker (15-60s audio, >=10s speech, max 5 samples)."""
        validated_id = validate_speaker_id(speaker_id)
        data: dict[str, str] = {"speaker_id": validated_id}
        if display_name is not None and display_name.strip():
            data["display_name"] = display_name.strip()
        return self._post("/v1/speakers/enroll", file_path=path, data=data)

    def verify_speaker(
        self,
        path: str | Path,
        speaker_id: str,
        detect_deepfake: bool = True,
    ) -> dict[str, Any]:
        """Verify speaker identity and optionally run Aletheia deepfake detection in one call."""
        validated_id = validate_speaker_id(speaker_id)
        data: dict[str, str] = {"speaker_id": validated_id}
        if detect_deepfake:
            data["detect_deepfake"] = "true"
        body = self._post("/v1/speakers/verify", file_path=path, data=data)
        return _normalize_verify_response(body)
