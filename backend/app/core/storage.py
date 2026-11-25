"""Safe, confined temporary storage for uploaded voice clips."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import BinaryIO

ALLOWED_AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".webm"}
CHUNK_SIZE = 64 * 1024


class UploadValidationError(ValueError):
    """Raised when an uploaded audio file fails validation."""


def save_audio_upload(
    *,
    filename: str | None,
    stream: BinaryIO,
    upload_dir: Path,
    max_bytes: int,
) -> Path:
    """Validate extension & size and save upload under a UUID filename inside upload_dir."""
    if not filename:
        raise UploadValidationError("Uploaded audio file is missing a filename.")

    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_AUDIO_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_AUDIO_EXTENSIONS))
        raise UploadValidationError(
            f"Unsupported audio format '{ext or '(none)'}'. Supported: {allowed}."
        )

    base_dir = upload_dir.resolve()
    base_dir.mkdir(parents=True, exist_ok=True)

    target = (base_dir / f"{uuid.uuid4().hex}{ext}").resolve()
    if not target.is_relative_to(base_dir):
        raise UploadValidationError("Invalid upload destination path.")

    total = 0
    try:
        with target.open("wb") as handle:
            while True:
                chunk = stream.read(CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise UploadValidationError(
                        f"Audio file exceeds the {max_bytes // (1024 * 1024)} MB limit."
                    )
                handle.write(chunk)
        if total == 0:
            raise UploadValidationError("Uploaded audio file is empty.")
    except Exception:
        if target.exists():
            target.unlink(missing_ok=True)
        raise

    return target
