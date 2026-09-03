"""Persistent recording and transcript storage for completed live sessions."""

from __future__ import annotations

import json
import os
import re
import shutil
import statistics
import uuid
from collections import Counter
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
SESSION_TITLE_REQUIRED_MESSAGE = "Enter a session name."
SESSION_TITLE_CONFLICT_MESSAGE = (
    "A saved session already uses this name. Choose a different name."
)


class SessionTitleValidationError(ValueError):
    """Reject an invalid archive title with a browser-safe error code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


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


def validate_new_session_title(
    value: Any,
    root: Path | None = None,
) -> str:
    """Return a required title that is unique among saved sessions."""

    if not isinstance(value, str) or not value.strip():
        raise SessionTitleValidationError(
            "SESSION_TITLE_REQUIRED",
            SESSION_TITLE_REQUIRED_MESSAGE,
        )

    title = clean_title(value)
    title_key = title.casefold()
    for session in list_sessions(root):
        existing_title = session.get("title")
        if (
            isinstance(existing_title, str)
            and clean_title(existing_title).casefold() == title_key
        ):
            raise SessionTitleValidationError(
                "SESSION_TITLE_CONFLICT",
                SESSION_TITLE_CONFLICT_MESSAGE,
            )
    return title


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


def _safe_diagnostic(
    event: dict[str, Any],
    recorded_at: str,
) -> dict[str, Any]:
    """Keep operational error fields without transcript or credential data."""

    diagnostic = {
        key: event[key]
        for key in (
            "stage",
            "message",
            "status",
            "code",
            "opc_request_id",
            "request_number",
            "latency_ms",
        )
        if event.get(key) is not None
    }
    diagnostic["recorded_at"] = recorded_at
    return diagnostic


def _latency_summary(values: list[float]) -> dict[str, int | float | None]:
    if not values:
        return {
            "count": 0,
            "minimum": None,
            "average": None,
            "median": None,
            "maximum": None,
        }

    return {
        "count": len(values),
        "minimum": min(values),
        "average": round(sum(values) / len(values), 1),
        "median": round(statistics.median(values), 1),
        "maximum": max(values),
    }


class SessionArchive:
    """Write one microphone stream and its OCI results to a session folder."""

    def __init__(
        self,
        title: str = "Live session",
        root: Path | None = None,
        *,
        session_id: str | None = None,
        started_at: datetime | None = None,
    ):
        self.started_at = started_at or utc_now()
        self.session_id = (
            validate_session_id(session_id)
            if session_id is not None
            else create_session_id(self.started_at)
        )
        self.root = (root or session_storage_root()).resolve()
        self.title = validate_new_session_title(title, self.root)
        self.directory = self.root / self.session_id
        self.directory.mkdir(parents=True, exist_ok=False)

        self.audio_path = self.directory / "session.mp3"
        self.english_path = self.directory / "english.txt"
        self.french_path = self.directory / "french.txt"
        self.diagnostics_path = self.directory / "diagnostics.json"
        self.report_path = self.directory / "session_report.json"
        self.metadata_path = self.directory / "metadata.json"

        self.english_segments: list[str] = []
        self.french_segments: list[str] = []
        self.diagnostics: list[dict[str, Any]] = []
        self.audio_bytes = 0
        self.audio_chunks = 0
        self.speech_connections_attempted = 0
        self.speech_connections_ready = 0
        self.transcript_updates = 0
        self.final_transcript_segments = 0
        self.speech_errors = 0
        self.translation_requests = 0
        self.translation_successes = 0
        self.translation_failures = 0
        self.translation_status_counts: Counter[str] = Counter()
        self.translation_code_counts: Counter[str] = Counter()
        self.translation_latencies_ms: list[float] = []
        self.error_count = 0
        self.error_stage_counts: Counter[str] = Counter()
        self.error_status_counts: Counter[str] = Counter()
        self.error_code_counts: Counter[str] = Counter()
        self.first_error: dict[str, Any] | None = None
        self.last_error: dict[str, Any] | None = None
        self._finalized = False
        self._audio_file = soundfile.SoundFile(
            str(self.audio_path),
            mode="w",
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            format="MP3",
            subtype="MPEG_LAYER_III",
        )

    def write_audio(self, pcm_audio: bytes) -> None:
        if self._finalized:
            return
        if len(pcm_audio) % PCM_SAMPLE_BYTES:
            raise ValueError("PCM recording data must contain complete samples.")
        self._audio_file.buffer_write(pcm_audio, dtype="int16")
        self.audio_bytes += len(pcm_audio)
        self.audio_chunks += 1

    def _record_translation_result(
        self,
        event: dict[str, Any],
        succeeded: bool,
    ) -> None:
        self.translation_requests += 1
        if succeeded:
            self.translation_successes += 1
        else:
            self.translation_failures += 1

        status = event.get("status")
        if status is not None:
            self.translation_status_counts[str(status)] += 1
        code = event.get("code")
        if code is not None:
            self.translation_code_counts[str(code)] += 1
        latency_ms = event.get("latency_ms")
        if (
            isinstance(latency_ms, (int, float))
            and not isinstance(latency_ms, bool)
            and latency_ms >= 0
        ):
            self.translation_latencies_ms.append(latency_ms)

    def _record_error(self, event: dict[str, Any]) -> None:
        recorded_at = utc_string(utc_now())
        diagnostic = _safe_diagnostic(event, recorded_at)
        self.error_count += 1

        stage = str(event.get("stage") or "unknown")
        self.error_stage_counts[stage] += 1
        if stage.startswith("speech"):
            self.speech_errors += 1

        status = event.get("status")
        if status is not None:
            self.error_status_counts[str(status)] += 1
        code = event.get("code")
        if code is not None:
            self.error_code_counts[str(code)] += 1

        if self.first_error is None:
            self.first_error = diagnostic
        self.last_error = diagnostic
        if len(self.diagnostics) < MAX_DIAGNOSTICS:
            self.diagnostics.append(diagnostic)

    def record_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "session_status" and event.get("state") == "connecting":
            self.speech_connections_attempted += 1
        elif event_type == "session_ready":
            self.speech_connections_ready += 1
        elif event_type == "transcript":
            self.transcript_updates += 1
            if event.get("is_final"):
                text = str(event.get("text") or "").strip()
                if text:
                    self.english_segments.append(text)
                    self.final_transcript_segments += 1
        elif event_type == "translation":
            text = str(event.get("french") or "").strip()
            if text:
                self.french_segments.append(text)
            self._record_translation_result(event, succeeded=True)
        elif event_type == "error":
            if event.get("stage") == "translation":
                self._record_translation_result(event, succeeded=False)
            self._record_error(event)

    def _build_report(
        self,
        status: str,
        ended_at: datetime,
        duration_seconds: float,
    ) -> dict[str, Any]:
        """Build a transcript-free operational summary for one session."""

        return {
            "schema_version": 1,
            "session_id": self.session_id,
            "title": self.title,
            "session_status": status,
            "started_at": utc_string(self.started_at),
            "ended_at": utc_string(ended_at),
            "duration_seconds": duration_seconds,
            "summary": {
                "speech_realtime_connections_attempted": (
                    self.speech_connections_attempted
                ),
                "speech_realtime_connections_ready": (
                    self.speech_connections_ready
                ),
                "language_requests_total": self.translation_requests,
                "errors_total": self.error_count,
            },
            "speech_realtime": {
                "connections_attempted": self.speech_connections_attempted,
                "connections_ready": self.speech_connections_ready,
                "audio_chunks_received": self.audio_chunks,
                "audio_bytes_received": self.audio_bytes,
                "transcript_updates_total": self.transcript_updates,
                "final_transcript_segments": self.final_transcript_segments,
                "errors_total": self.speech_errors,
            },
            "language_translation": {
                "requests_total": self.translation_requests,
                "succeeded": self.translation_successes,
                "failed": self.translation_failures,
                "status_counts": dict(self.translation_status_counts),
                "code_counts": dict(self.translation_code_counts),
                "latency_ms": _latency_summary(
                    self.translation_latencies_ms
                ),
            },
            "errors": {
                "total": self.error_count,
                "saved_details": len(self.diagnostics),
                "omitted_details": max(
                    self.error_count - len(self.diagnostics),
                    0,
                ),
                "by_stage": dict(self.error_stage_counts),
                "by_status": dict(self.error_status_counts),
                "by_code": dict(self.error_code_counts),
                "first": self.first_error,
                "last": self.last_error,
            },
        }

    def finalize(self, status: str) -> dict[str, Any]:
        """Close the MP3 and atomically publish the session metadata."""

        if self._finalized:
            return read_session_metadata(self.directory)
        self._finalized = True

        self._audio_file.close()
        if self.audio_bytes == 0 and self.audio_path.exists():
            self.audio_path.unlink()

        ended_at = utc_now()
        duration_seconds = round(
            self.audio_bytes / (SAMPLE_RATE * PCM_SAMPLE_BYTES),
            1,
        )
        english_text = " ".join(self.english_segments).strip()
        french_text = " ".join(self.french_segments).strip()
        self.english_path.write_text(english_text, encoding="utf-8")
        self.french_path.write_text(french_text, encoding="utf-8")
        _write_json(self.diagnostics_path, self.diagnostics)
        _write_json(
            self.report_path,
            self._build_report(status, ended_at, duration_seconds),
        )

        metadata = {
            "session_id": self.session_id,
            "title": self.title,
            "status": status,
            "started_at": utc_string(self.started_at),
            "ended_at": utc_string(ended_at),
            "duration_seconds": duration_seconds,
            "audio_available": self.audio_bytes > 0 and self.audio_path.is_file(),
            "english_available": bool(english_text),
            "french_available": bool(french_text),
            "english_characters": len(english_text),
            "french_characters": len(french_text),
            "diagnostic_count": len(self.diagnostics),
            "error_count": self.error_count,
            "session_report_available": True,
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
    if metadata.get("session_report_available"):
        result["report_url"] = (
            f"/api/sessions/{session_id}/session-report.json"
        )
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
    report_path = directory / "session_report.json"
    metadata["session_report"] = (
        json.loads(report_path.read_text(encoding="utf-8"))
        if report_path.is_file()
        else None
    )
    return metadata


def get_session_file(
    session_id: str,
    filename: str,
    root: Path | None = None,
) -> Path:
    allowed_files = {
        "session.mp3",
        "english.txt",
        "french.txt",
        "session_report.json",
    }
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
