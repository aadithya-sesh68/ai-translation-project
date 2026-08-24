"""Local tests for persisted audio and transcript session outputs."""

from __future__ import annotations

import array
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import soundfile
from websockets.datastructures import Headers
from websockets.http11 import Request

from session_store import SessionArchive, get_session, list_sessions
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
            {"type": "transcript", "text": "Hello world.", "is_final": True}
        )
        archive.record_event(
            {"type": "translation", "french": "Bonjour le monde."}
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
        self.assertEqual(1, len(list_sessions(self.root)))

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
