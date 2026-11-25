"""Audio download, ffmpeg 16 kHz mono WAV conversion, and duration/size guards."""

from __future__ import annotations

import asyncio
import subprocess
import wave
from pathlib import Path
from typing import Protocol

# Documented Aletheia duration bounds and upload safety limits (single source of truth).
MIN_DURATION_SECONDS: float = 4.0
MAX_DURATION_SECONDS: float = 120.0
MAX_AUDIO_BYTES: int = 20 * 1024 * 1024  # 20 MB Telegram bot download cap
TARGET_SAMPLE_RATE: int = 16000
TARGET_CHANNELS: int = 1
FFMPEG_TIMEOUT_SECONDS: int = 30


class AudioError(Exception):
    """Base error for Telegram audio validation or conversion issues."""


class AudioSizeError(AudioError):
    """Raised when an audio file is empty or exceeds MAX_AUDIO_BYTES."""


class AudioDurationError(AudioError):
    """Raised when audio duration is outside MIN_DURATION_SECONDS..MAX_DURATION_SECONDS."""


class AudioConversionError(AudioError):
    """Raised when ffmpeg is unavailable, fails, or times out."""


class TelegramDownloadable(Protocol):
    """Minimal protocol matching python-telegram-bot's File object."""

    async def download_to_drive(self, custom_path: str | Path) -> Path: ...


def validate_duration(duration_seconds: float) -> float:
    """Ensure audio duration falls within Aletheia's 4 to 120 second window."""
    if duration_seconds < MIN_DURATION_SECONDS:
        raise AudioDurationError(
            f"This voice clip is too short ({duration_seconds:.1f}s). "
            f"Please send a clip between {int(MIN_DURATION_SECONDS)} and "
            f"{int(MAX_DURATION_SECONDS)} seconds long."
        )
    if duration_seconds > MAX_DURATION_SECONDS:
        raise AudioDurationError(
            f"This voice clip is too long ({duration_seconds:.1f}s). "
            f"Please send a clip between {int(MIN_DURATION_SECONDS)} and "
            f"{int(MAX_DURATION_SECONDS)} seconds long."
        )
    return float(duration_seconds)


def read_wav_duration(wav_path: Path) -> float:
    """Read exact duration in seconds from a PCM WAV file using stdlib wave."""
    try:
        with wave.open(str(wav_path), "rb") as wf:
            frame_rate = wf.getframerate()
            frame_count = wf.getnframes()
    except (wave.Error, OSError) as exc:
        raise AudioConversionError("Converted audio could not be read as WAV.") from exc

    if frame_rate <= 0 or frame_count <= 0:
        raise AudioConversionError("Converted WAV file contains no audio frames.")
    return frame_count / float(frame_rate)


def convert_to_wav_16k_mono(input_path: Path, output_path: Path) -> float:
    """Convert any Telegram audio file to 16 kHz mono WAV via ffmpeg (no shell=True).

    Returns the verified duration in seconds.
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-nostdin",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
        "-ac",
        str(TARGET_CHANNELS),
        "-ar",
        str(TARGET_SAMPLE_RATE),
        "-f",
        "wav",
        str(output_path),
    ]
    try:
        completed = subprocess.run(
            cmd,
            shell=False,
            check=False,
            capture_output=True,
            timeout=FFMPEG_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise AudioConversionError(
            "Audio converter (ffmpeg) is not installed on the server."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise AudioConversionError("Audio conversion timed out.") from exc

    if completed.returncode != 0 or not output_path.is_file():
        raise AudioConversionError("Could not decode or convert that audio file.")

    duration = read_wav_duration(output_path)
    return validate_duration(duration)


async def download_and_prepare_wav(
    tg_file: TelegramDownloadable,
    *,
    temp_dir: Path,
    declared_size: int | None = None,
    declared_duration: int | float | None = None,
) -> tuple[Path, float]:
    """Download a Telegram voice/audio file into temp_dir and convert to 16 kHz mono WAV.

    Caller owns `temp_dir` (e.g. `tempfile.TemporaryDirectory`) so all files are
    deleted automatically after analysis.
    """
    if declared_size is not None and declared_size > MAX_AUDIO_BYTES:
        max_mb = MAX_AUDIO_BYTES // (1024 * 1024)
        raise AudioSizeError(f"Audio file is too large (limit is {max_mb} MB).")

    if declared_duration is not None:
        validate_duration(float(declared_duration))

    # Fixed local filenames inside temp_dir avoid trusting user-supplied filenames.
    raw_path = temp_dir / "input_audio.bin"
    wav_path = temp_dir / "normalized_16k_mono.wav"

    await tg_file.download_to_drive(custom_path=raw_path)

    if not raw_path.is_file():
        raise AudioSizeError("Downloaded audio file was missing.")

    actual_size = raw_path.stat().st_size
    if actual_size == 0:
        raise AudioSizeError("Audio file is empty.")
    if actual_size > MAX_AUDIO_BYTES:
        max_mb = MAX_AUDIO_BYTES // (1024 * 1024)
        raise AudioSizeError(f"Audio file is too large (limit is {max_mb} MB).")

    duration = await asyncio.to_thread(convert_to_wav_16k_mono, raw_path, wav_path)
    return wav_path, duration
