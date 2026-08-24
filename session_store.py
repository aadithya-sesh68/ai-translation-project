"""Persistent recording and transcript storage for completed live sessions."""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import soundfile


SAMPLE_RATE = 16_000
CHANNELS = 1
PCM_SAMPLE_BYTES = 2
SESSION_ID_PATTERN = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")
MAX_TITLE_LENGTH = 120
MAX_DIAGNOSTICS = 100


def session_storage_root() -> Path:
    """Return the configured non-secret session output directory."""

    configured = os.environ.get("SESSION_STORAGE_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path(__file__).parent / "recorded_sessions").resolve()


def clean_title(value: Any) -> str:
    """Normalize a browser-provided display title without using it as a path."""

    if not isinstance(value, str):
        return "Live session"
    title = " ".join(value.split()).strip()
    return title[:MAX_TITLE_LENGTH] or "Live session"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_string(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def create_session_id(started_at: datetime) -> str:
    timestamp = started_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{uuid.uuid4().hex[:8]}"


def _write_json(path: Path, payload: Any) -> None:
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary_path.replace(path)


class SessionArchive:
    """Write one microphone stream and its OCI results to a session folder."""

    def __init__(self, title: str = "Live session", root: Path | None = None):
        self.started_at = utc_now()
        self.session_id = create_session_id(self.started_at)
        self.title = clean_title(title)
        self.root = (root or session_storage_root()).resolve()
        self.directory = self.root / self.session_id
        self.directory.mkdir(parents=True, exist_ok=False)

        self.audio_path = self.directory / "session.mp3"
        self.english_path = self.directory / "english.txt"
        self.french_path = self.directory / "french.txt"
        self.diagnostics_path = self.directory / "diagnostics.json"
        self.metadata_path = self.directory / "metadata.json"

        self.english_segments: list[str] = []
        self.french_segments: list[str] = []
        self.diagnostics: list[dict[str, Any]] = []
        self.audio_bytes = 0
        self._finalized = False
        self._audio_file = soundfile.SoundFile(
            str(self.audio_path),
            mode="w",
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            format="MP3",
            subtype="MPEG_LAYER_III",
        )

    def set_title(self, title: Any) -> None:
        self.title = clean_title(title)

    def write_audio(self, pcm_audio: bytes) -> None:
        if self._finalized:
            return
        if len(pcm_audio) % PCM_SAMPLE_BYTES:
            raise ValueError("PCM recording data must contain complete samples.")
        self._audio_file.buffer_write(pcm_audio, dtype="int16")
        self.audio_bytes += len(pcm_audio)

    def record_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "transcript" and event.get("is_final"):
            text = str(event.get("text") or "").strip()
            if text:
                self.english_segments.append(text)
        elif event_type == "translation":
            text = str(event.get("french") or "").strip()
            if text:
                self.french_segments.append(text)
        elif event_type == "error" and len(self.diagnostics) < MAX_DIAGNOSTICS:
            self.diagnostics.append(
                {
                    key: event[key]
                    for key in (
                        "stage",
                        "message",
                        "status",
                        "code",
                        "opc_request_id",
                    )
                    if event.get(key) is not None
                }
            )

    def finalize(self, status: str) -> dict[str, Any]:
        """Close the MP3 and atomically publish the session metadata."""

        if self._finalized:
            return read_session_metadata(self.directory)
        self._finalized = True

        self._audio_file.close()
        if self.audio_bytes == 0 and self.audio_path.exists():
            self.audio_path.unlink()

        ended_at = utc_now()
        english_text = " ".join(self.english_segments).strip()
        french_text = " ".join(self.french_segments).strip()
        self.english_path.write_text(english_text, encoding="utf-8")
        self.french_path.write_text(french_text, encoding="utf-8")
        _write_json(self.diagnostics_path, self.diagnostics)

        metadata = {
            "session_id": self.session_id,
            "title": self.title,
            "status": status,
            "started_at": utc_string(self.started_at),
            "ended_at": utc_string(ended_at),
            "duration_seconds": round(
                self.audio_bytes / (SAMPLE_RATE * PCM_SAMPLE_BYTES), 1
            ),
            "audio_available": self.audio_bytes > 0 and self.audio_path.is_file(),
            "english_available": bool(english_text),
            "french_available": bool(french_text),
            "english_characters": len(english_text),
            "french_characters": len(french_text),
            "diagnostic_count": len(self.diagnostics),
        }
        _write_json(self.metadata_path, metadata)
        return metadata


def validate_session_id(session_id: str) -> str:
    if not SESSION_ID_PATTERN.fullmatch(session_id):
        raise ValueError("Invalid session identifier.")
    return session_id


def session_directory(session_id: str, root: Path | None = None) -> Path:
    validate_session_id(session_id)
    storage_root = (root or session_storage_root()).resolve()
    directory = (storage_root / session_id).resolve()
    if directory.parent != storage_root:
        raise ValueError("Invalid session path.")
    return directory


def read_session_metadata(directory: Path) -> dict[str, Any]:
    metadata_path = directory / "metadata.json"
    return json.loads(metadata_path.read_text(encoding="utf-8"))


def public_session(metadata: dict[str, Any]) -> dict[str, Any]:
    session_id = metadata["session_id"]
    result = dict(metadata)
    result["detail_url"] = f"/api/sessions/{session_id}"
    if metadata.get("audio_available"):
        result["audio_url"] = f"/api/sessions/{session_id}/audio.mp3"
    result["english_url"] = f"/api/sessions/{session_id}/english.txt"
    result["french_url"] = f"/api/sessions/{session_id}/french.txt"
    return result


def list_sessions(root: Path | None = None) -> list[dict[str, Any]]:
    storage_root = (root or session_storage_root()).resolve()
    if not storage_root.is_dir():
        return []

    sessions: list[dict[str, Any]] = []
    for directory in storage_root.iterdir():
        if not directory.is_dir() or not SESSION_ID_PATTERN.fullmatch(
            directory.name
        ):
            continue
        try:
            sessions.append(public_session(read_session_metadata(directory)))
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            continue
    sessions.sort(key=lambda item: item.get("started_at", ""), reverse=True)
    return sessions


def get_session(session_id: str, root: Path | None = None) -> dict[str, Any]:
    directory = session_directory(session_id, root)
    metadata = public_session(read_session_metadata(directory))
    metadata["english_text"] = (directory / "english.txt").read_text(
        encoding="utf-8"
    )
    metadata["french_text"] = (directory / "french.txt").read_text(
        encoding="utf-8"
    )
    diagnostics_path = directory / "diagnostics.json"
    metadata["diagnostics"] = (
        json.loads(diagnostics_path.read_text(encoding="utf-8"))
        if diagnostics_path.is_file()
        else []
    )
    return metadata


def get_session_file(
    session_id: str,
    filename: str,
    root: Path | None = None,
) -> Path:
    allowed_files = {"session.mp3", "english.txt", "french.txt"}
    if filename not in allowed_files:
        raise ValueError("Unsupported session file.")
    path = session_directory(session_id, root) / filename
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def delete_session(
    session_id: str,
    root: Path | None = None,
) -> dict[str, Any]:
    """Permanently remove one validated, finalized session directory."""

    directory = session_directory(session_id, root)
    if not directory.is_dir():
        raise FileNotFoundError(directory)
    metadata = read_session_metadata(directory)
    if metadata.get("session_id") != session_id:
        raise ValueError("Session metadata does not match its directory.")
    shutil.rmtree(directory)
    return public_session(metadata)
