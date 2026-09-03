"""Local tests for persisted audio and transcript session outputs."""

from __future__ import annotations

import array
import json
import math
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import soundfile
from websockets.datastructures import Headers
from websockets.http11 import Request

from session_store import (
    SESSION_TITLE_CONFLICT_MESSAGE,
    SESSION_TITLE_REQUIRED_MESSAGE,
    SessionArchive,
    SessionTitleValidationError,
    get_session,
    list_sessions,
)
from speech_web_server import session_api_response


class SessionStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def create_saved_session(self) -> dict[str, object]:
        archive = SessionArchive("Customer session", self.root)
        samples = array.array(
            "h",
            (
                int(8_000 * math.sin(2 * math.pi * 440 * index / 16_000))
                for index in range(16_000)
            ),
        )
        archive.write_audio(samples.tobytes())
        archive.record_event(
            {
                "type": "session_status",
                "state": "connecting",
            }
        )
        archive.record_event(
            {
                "type": "session_ready",
                "sample_rate": 16_000,
            }
        )
        archive.record_event(
            {"type": "transcript", "text": "Hello", "is_final": False}
        )
        archive.record_event(
            {"type": "transcript", "text": "Hello world.", "is_final": True}
        )
        archive.record_event(
            {
                "type": "translation",
                "french": "Bonjour le monde.",
                "request_number": 1,
                "latency_ms": 125,
                "status": 200,
                "code": "OK",
                "opc_request_id": "opc-success-1",
            }
        )
        return archive.finalize("completed")

    def test_mp3_and_text_round_trip(self) -> None:
        metadata = self.create_saved_session()
        session = get_session(str(metadata["session_id"]), self.root)
        audio_path = self.root / str(metadata["session_id"]) / "session.mp3"
        audio_info = soundfile.info(str(audio_path))

        self.assertEqual("MP3", audio_info.format)
        self.assertAlmostEqual(1.0, audio_info.duration, places=1)
        self.assertEqual("Hello world.", session["english_text"])
        self.assertEqual("Bonjour le monde.", session["french_text"])
        self.assertTrue(metadata["session_report_available"])
        report = session["session_report"]
        serialized_report = json.dumps(report)
        self.assertNotIn("Hello world.", serialized_report)
        self.assertNotIn("Bonjour le monde.", serialized_report)
        self.assertEqual(1, report["summary"]["language_requests_total"])
        self.assertEqual(
            1,
            report["speech_realtime"]["connections_attempted"],
        )
        self.assertEqual(1, report["speech_realtime"]["connections_ready"])
        self.assertEqual(2, report["speech_realtime"]["transcript_updates_total"])
        self.assertEqual(1, report["speech_realtime"]["final_transcript_segments"])
        self.assertEqual(1, report["language_translation"]["succeeded"])
        self.assertEqual(0, report["language_translation"]["failed"])
        self.assertEqual(
            125,
            report["language_translation"]["latency_ms"]["median"],
        )
        self.assertEqual(0, report["errors"]["total"])
        self.assertEqual(1, len(list_sessions(self.root)))

    def test_session_title_is_required_before_archive_creation(self) -> None:
        with self.assertRaises(SessionTitleValidationError) as context:
            SessionArchive("   ", self.root)

        self.assertEqual("SESSION_TITLE_REQUIRED", context.exception.code)
        self.assertEqual(SESSION_TITLE_REQUIRED_MESSAGE, str(context.exception))
        self.assertEqual([], list(self.root.iterdir()))

    def test_saved_session_titles_are_unique_after_normalization(self) -> None:
        archive = SessionArchive("Customer Session", self.root)
        archive.finalize("completed")

        with self.assertRaises(SessionTitleValidationError) as context:
            SessionArchive("  customer   session  ", self.root)

        self.assertEqual("SESSION_TITLE_CONFLICT", context.exception.code)
        self.assertEqual(SESSION_TITLE_CONFLICT_MESSAGE, str(context.exception))
        self.assertEqual(1, len(list_sessions(self.root)))

    def test_archive_can_use_prepared_session_identity_at_activation(self) -> None:
        session_id = "20260903T090000Z-abcdef12"
        started_at = datetime(2026, 9, 3, 9, 15, tzinfo=timezone.utc)

        archive = SessionArchive(
            "Prepared session",
            self.root,
            session_id=session_id,
            started_at=started_at,
        )
        metadata = archive.finalize("completed")

        self.assertEqual(session_id, metadata["session_id"])
        self.assertEqual("2026-09-03T09:15:00Z", metadata["started_at"])

    def test_session_list_api_returns_public_urls(self) -> None:
        metadata = self.create_saved_session()
        request = Request("/api/sessions", Headers())

        with patch.dict(
            "os.environ", {"SESSION_STORAGE_DIR": str(self.root)}
        ):
            result = session_api_response(request, "/api/sessions")

        self.assertIsNotNone(result)
        self.assertEqual(200, result.status_code)
        payload = json.loads(result.body)
        self.assertEqual(metadata["session_id"], payload["sessions"][0]["session_id"])
        self.assertTrue(payload["sessions"][0]["audio_url"].endswith("audio.mp3"))
        self.assertTrue(
            payload["sessions"][0]["report_url"].endswith(
                "session-report.json"
            )
        )

    def test_session_report_download_is_json(self) -> None:
        metadata = self.create_saved_session()
        session_id = str(metadata["session_id"])
        path = f"/api/sessions/{session_id}/session-report.json"
        request = Request(path, Headers())

        with patch.dict(
            "os.environ", {"SESSION_STORAGE_DIR": str(self.root)}
        ):
            result = session_api_response(request, path)

        self.assertIsNotNone(result)
        self.assertEqual(200, result.status_code)
        self.assertEqual(
            "application/json; charset=utf-8",
            result.headers["Content-Type"],
        )
        report = json.loads(result.body)
        self.assertEqual(session_id, report["session_id"])

    def test_report_counts_errors_beyond_saved_detail_limit(self) -> None:
        archive = SessionArchive("Error-heavy session", self.root)
        for request_number in range(1, 106):
            archive.record_event(
                {
                    "type": "error",
                    "stage": "translation",
                    "message": "Authorization failed.",
                    "status": 404,
                    "code": "NotAuthorizedOrNotFound",
                    "opc_request_id": f"opc-{request_number}",
                    "request_number": request_number,
                    "latency_ms": request_number,
                }
            )

        metadata = archive.finalize("completed")
        session = get_session(str(metadata["session_id"]), self.root)
        report = session["session_report"]

        self.assertEqual(100, len(session["diagnostics"]))
        self.assertEqual(105, metadata["error_count"])
        self.assertEqual(105, report["errors"]["total"])
        self.assertEqual(100, report["errors"]["saved_details"])
        self.assertEqual(5, report["errors"]["omitted_details"])
        self.assertEqual(1, report["errors"]["first"]["request_number"])
        self.assertEqual(105, report["errors"]["last"]["request_number"])
        self.assertEqual(105, report["language_translation"]["requests_total"])
        self.assertEqual(105, report["language_translation"]["failed"])
        self.assertEqual(
            53,
            report["language_translation"]["latency_ms"]["median"],
        )

    def test_delete_api_removes_complete_session_directory(self) -> None:
        metadata = self.create_saved_session()
        session_id = str(metadata["session_id"])
        request = Request(
            f"/api/sessions/{session_id}",
            Headers(),
            method="DELETE",
        )

        with patch.dict(
            "os.environ", {"SESSION_STORAGE_DIR": str(self.root)}
        ):
            result = session_api_response(
                request,
                f"/api/sessions/{session_id}",
            )

        self.assertIsNotNone(result)
        self.assertEqual(200, result.status_code)
        payload = json.loads(result.body)
        self.assertEqual(session_id, payload["deleted_session"]["session_id"])
        self.assertFalse((self.root / session_id).exists())
        self.assertEqual([], list_sessions(self.root))


if __name__ == "__main__":
    unittest.main()
