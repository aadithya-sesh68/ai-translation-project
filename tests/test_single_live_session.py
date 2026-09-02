"""Tests for the single authoritative live audio-capture session."""

from __future__ import annotations

import asyncio
import json
import tempfile
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from websockets.datastructures import Headers
from websockets.http11 import Request

from speech_web_server import (
    LIVE_SESSION_ACTIVE_MESSAGE,
    LiveSessionCoordinator,
    handle_live_session,
    process_http_request,
    session_start_title,
)
from session_store import SessionArchive, SessionTitleValidationError


class LiveSessionCoordinatorTest(unittest.IsolatedAsyncioTestCase):
    async def test_first_websocket_command_supplies_the_session_title(self) -> None:
        self.assertEqual(
            "Customer session",
            session_start_title(
                json.dumps(
                    {"type": "start", "title": "Customer session"}
                )
            ),
        )

    async def test_audio_before_start_is_rejected(self) -> None:
        with self.assertRaises(SessionTitleValidationError) as context:
            session_start_title(b"audio")

        self.assertEqual("SESSION_START_REQUIRED", context.exception.code)

    async def test_duplicate_title_is_rejected_before_oci_client_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            archive = SessionArchive("Customer session", root)
            archive.finalize("completed")
            coordinator = LiveSessionCoordinator()
            websocket = SimpleNamespace(
                request=SimpleNamespace(path="/ws/live"),
                recv=AsyncMock(
                    return_value=json.dumps(
                        {"type": "start", "title": "customer SESSION"}
                    )
                ),
                send=AsyncMock(),
                close=AsyncMock(),
            )

            with (
                patch.dict(
                    "os.environ",
                    {"SESSION_STORAGE_DIR": str(root)},
                ),
                patch(
                    "speech_web_server.LIVE_SESSION_COORDINATOR",
                    coordinator,
                ),
                patch(
                    "speech_web_server.SpeechTranslationSession"
                ) as speech_session,
            ):
                await handle_live_session(websocket)

            payload = json.loads(websocket.send.await_args.args[0])
            self.assertEqual("session_rejected", payload["type"])
            self.assertEqual("SESSION_TITLE_CONFLICT", payload["code"])
            speech_session.assert_not_called()
            self.assertEqual(1, len(list(root.iterdir())))

    async def test_only_one_owner_can_hold_the_live_session(self) -> None:
        coordinator = LiveSessionCoordinator()
        first_owner = asyncio.create_task(asyncio.sleep(60))
        second_owner = asyncio.create_task(asyncio.sleep(60))

        try:
            self.assertTrue(coordinator.try_acquire(first_owner))
            self.assertTrue(coordinator.active)
            self.assertFalse(coordinator.try_acquire(second_owner))

            coordinator.release(first_owner)

            self.assertFalse(coordinator.active)
            self.assertTrue(coordinator.try_acquire(second_owner))
        finally:
            first_owner.cancel()
            second_owner.cancel()
            await asyncio.gather(
                first_owner,
                second_owner,
                return_exceptions=True,
            )

    async def test_completed_owner_releases_the_live_session(self) -> None:
        coordinator = LiveSessionCoordinator()
        owner = asyncio.create_task(asyncio.sleep(0))

        self.assertTrue(coordinator.try_acquire(owner))
        await owner
        await asyncio.sleep(0)

        self.assertFalse(coordinator.active)

    async def test_second_websocket_receives_clear_rejection(self) -> None:
        coordinator = LiveSessionCoordinator()
        owner = asyncio.create_task(asyncio.sleep(60))
        self.assertTrue(coordinator.try_acquire(owner))
        websocket = SimpleNamespace(
            request=SimpleNamespace(path="/ws/live"),
            send=AsyncMock(),
            close=AsyncMock(),
        )

        try:
            with patch(
                "speech_web_server.LIVE_SESSION_COORDINATOR",
                coordinator,
            ):
                await handle_live_session(websocket)

            payload = json.loads(websocket.send.await_args.args[0])
            self.assertEqual("session_rejected", payload["type"])
            self.assertEqual("LIVE_SESSION_ACTIVE", payload["code"])
            self.assertEqual(LIVE_SESSION_ACTIVE_MESSAGE, payload["message"])
            websocket.close.assert_awaited_once_with(
                code=1013,
                reason=LIVE_SESSION_ACTIVE_MESSAGE,
            )
        finally:
            owner.cancel()
            await asyncio.gather(owner, return_exceptions=True)

    async def test_status_endpoint_reports_the_authoritative_state(self) -> None:
        coordinator = LiveSessionCoordinator()
        request = Request("/api/live-session", Headers())

        with patch(
            "speech_web_server.LIVE_SESSION_COORDINATOR",
            coordinator,
        ):
            response = process_http_request(SimpleNamespace(), request)
            self.assertIsNotNone(response)
            self.assertEqual({"active": False}, json.loads(response.body))

            owner = asyncio.create_task(asyncio.sleep(60))
            try:
                coordinator.try_acquire(owner)
                response = process_http_request(SimpleNamespace(), request)
                self.assertEqual({"active": True}, json.loads(response.body))
            finally:
                owner.cancel()
                await asyncio.gather(owner, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
