"""Tests for the server-owned host and listener live-session model."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from websockets.datastructures import Headers
from websockets.http11 import Request

from live_session_manager import (
    LIVE_SESSION_ACTIVE_MESSAGE,
    LiveSessionError,
    LiveSessionManager,
)
from session_schedule import SessionCodeCatalog
from speech_web_server import process_http_request


class FakeArchive:
    def __init__(
        self,
        title: str,
        *,
        session_id=None,
        started_at=None,
        session_code=None,
        session_label=None,
    ):
        if not str(title or "").strip():
            from session_store import SessionTitleValidationError

            raise SessionTitleValidationError(
                "SESSION_TITLE_REQUIRED",
                "Enter a session name.",
            )
        self.session_id = session_id or "20260902T120000Z-1234abcd"
        self.started_at = started_at
        self.title = str(title).strip()
        self.session_code = session_code
        self.session_label = session_label
        self.events: list[dict[str, object]] = []
        self.audio: list[bytes] = []
        self.finalize_calls: list[str] = []

    def record_event(self, event: dict[str, object]) -> None:
        self.events.append(event)

    def write_audio(self, chunk: bytes) -> None:
        self.audio.append(chunk)

    def finalize(self, status: str) -> dict[str, object]:
        self.finalize_calls.append(status)
        return {
            "session_id": self.session_id,
            "title": self.title,
            "session_code": self.session_code,
            "session_label": self.session_label,
            "status": status,
            "started_at": "2026-09-02T12:00:00Z",
            "ended_at": "2026-09-02T12:01:00Z",
            "duration_seconds": 60,
            "audio_available": bool(self.audio),
            "english_available": True,
            "french_available": True,
            "session_report_available": True,
        }


class FakeOciSession:
    def __init__(self, settings, event_sink, session_id=None):
        self.settings = settings
        self.event_sink = event_sink
        self.session_id = session_id
        self.audio: list[bytes] = []
        self.stop_calls: list[bool] = []

    async def start(self) -> None:
        self.event_sink(
            {
                "type": "session_ready",
                "sample_rate": 16000,
                "encoding": "pcm_s16le",
            }
        )

    async def send_audio(self, chunk: bytes) -> None:
        self.audio.append(chunk)

    async def stop(self, request_final_result: bool = True) -> None:
        self.stop_calls.append(request_final_result)


class DelayedOciSession(FakeOciSession):
    def __init__(self, settings, event_sink, session_id=None):
        super().__init__(settings, event_sink, session_id)
        self.continue_start = asyncio.Event()

    async def start(self) -> None:
        await self.continue_start.wait()
        await super().start()


def manager(**overrides) -> LiveSessionManager:
    options = {
        "archive_factory": FakeArchive,
        "settings_factory": lambda: SimpleNamespace(),
        "session_factory": FakeOciSession,
        "title_validator": lambda title: str(title).strip(),
        "session_catalog": SessionCodeCatalog(),
        "reconnect_grace_seconds": 0.05,
        "prepared_timeout_seconds": 1,
        "audio_queue_size": 2,
        "outbound_queue_size": 16,
    }
    options.update(overrides)
    return LiveSessionManager(**options)


def drain(subscriber) -> list[dict[str, object]]:
    events = []
    while not subscriber.queue.empty():
        event = subscriber.queue.get_nowait()
        subscriber.queue.task_done()
        if event is not None:
            events.append(event)
    return events


class LiveSessionManagerTest(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_exposes_four_reusable_event_codes(self) -> None:
        coordinator = manager()

        payload = coordinator.schedule()

        self.assertEqual(
            ["DAY1-AM", "DAY1-PM", "DAY2-AM", "DAY2-PM"],
            [slot["code"] for slot in payload["slots"]],
        )
        self.assertEqual(
            ["available", "available", "available", "available"],
            [slot["status"] for slot in payload["slots"]],
        )
        self.assertIsNone(payload["active_code"])

    async def test_listener_joins_only_after_the_matching_host_prepares(self) -> None:
        coordinator = manager()
        listener = coordinator.subscriber("listener")

        with self.assertRaises(LiveSessionError) as inactive:
            await coordinator.join_listener("day1-am", listener)
        self.assertEqual("SESSION_NOT_ACTIVE", inactive.exception.code)

        host = coordinator.subscriber("host")
        live = await coordinator.prepare_host(
            "Morning session",
            host,
            "DAY1-AM",
        )
        await coordinator.join_listener("DAY1-AM", listener)
        snapshot = next(event for event in drain(listener) if event["type"] == "session_snapshot")
        self.assertEqual("DAY1-AM", snapshot["session"]["session_code"])
        self.assertEqual(1, live.listener_count)
        await coordinator.cancel_prepared(host.subscriber_id)

    async def test_host_title_is_derived_from_the_selected_schedule_slot(self) -> None:
        coordinator = manager()
        host = coordinator.subscriber("host")

        live = await coordinator.prepare_host(None, host, "DAY1-AM")

        self.assertEqual("DAY1-AM — September 15 · Morning", live.title)
        prepared = next(
            event
            for event in drain(host)
            if event["type"] == "session_prepared"
        )
        self.assertEqual("DAY1-AM — September 15 · Morning", prepared["title"])
        await coordinator.cancel_prepared(host.subscriber_id)

    async def test_completed_code_can_be_reused_for_another_run(self) -> None:
        coordinator = manager()
        host = coordinator.subscriber("host")
        live = await coordinator.start_host("First run", host, "DAY1-AM")
        await live.finalize("completed")

        retry_host = coordinator.subscriber("host")
        retry = await coordinator.prepare_host(None, retry_host, "DAY1-AM")

        self.assertEqual("DAY1-AM", retry.session_slot.code)
        self.assertEqual("DAY1-AM — September 15 · Morning", retry.title)
        await coordinator.cancel_prepared(retry_host.subscriber_id)

    async def test_listener_wrong_code_reports_the_active_code(self) -> None:
        coordinator = manager()
        host = coordinator.subscriber("host")
        await coordinator.prepare_host(None, host, "DAY1-PM")

        with self.assertRaises(LiveSessionError) as mismatch:
            await coordinator.join_listener(
                "DAY1-AM",
                coordinator.subscriber("listener"),
            )

        self.assertEqual("SESSION_CODE_MISMATCH", mismatch.exception.code)
        self.assertIn("DAY1-PM", str(mismatch.exception))
        await coordinator.cancel_prepared(host.subscriber_id)

    async def test_interrupted_slot_remains_available(self) -> None:
        coordinator = manager()
        host = coordinator.subscriber("host")
        live = await coordinator.start_host("Interrupted", host, "DAY1-AM")

        await live.finalize("interrupted")

        payload = coordinator.schedule()
        self.assertIsNone(payload["active_code"])
        self.assertTrue(all(slot["status"] == "available" for slot in payload["slots"]))

    async def test_prepare_creates_waiting_room_without_archive_or_oci(self) -> None:
        archives: list[FakeArchive] = []
        oci_sessions: list[FakeOciSession] = []

        def archive_factory(title: str, **kwargs) -> FakeArchive:
            archive = FakeArchive(title, **kwargs)
            archives.append(archive)
            return archive

        def session_factory(*args, **kwargs) -> FakeOciSession:
            session = FakeOciSession(*args, **kwargs)
            oci_sessions.append(session)
            return session

        coordinator = manager(
            archive_factory=archive_factory,
            session_factory=session_factory,
        )
        host = coordinator.subscriber("host")
        waiting = await coordinator.prepare_host("Prepared session", host)

        self.assertEqual("prepared", waiting.state)
        self.assertIsNone(waiting.archive)
        self.assertIsNone(waiting.audio_queue)
        self.assertIsNone(waiting.oci_session)
        self.assertEqual([], archives)
        self.assertEqual([], oci_sessions)
        prepared = next(
            event
            for event in drain(host)
            if event["type"] == "session_prepared"
        )
        self.assertEqual(waiting.join_code, prepared["join_code"])

        listener = coordinator.subscriber("listener")
        await coordinator.join_listener(waiting.join_code, listener)
        snapshot = next(
            event
            for event in drain(listener)
            if event["type"] == "session_snapshot"
        )
        self.assertEqual("prepared", snapshot["session"]["state"])

        await coordinator.cancel_prepared(host.subscriber_id)
        ended = next(
            event
            for event in drain(listener)
            if event["type"] == "session_ended"
        )
        self.assertFalse(ended["archived"])
        self.assertEqual([], archives)
        self.assertFalse(coordinator.active)

    async def test_activation_creates_archive_and_oci_resources(self) -> None:
        coordinator = manager()
        host = coordinator.subscriber("host")
        live = await coordinator.prepare_host("Activation", host)

        await coordinator.activate_host(host.subscriber_id)

        self.assertEqual("live", live.state)
        self.assertIsNotNone(live.archive)
        self.assertIsNotNone(live.audio_queue)
        self.assertIsInstance(live.oci_session, FakeOciSession)
        await live.finalize("completed")

    async def test_prepared_waiting_room_expires_without_archive(self) -> None:
        archives: list[FakeArchive] = []

        def archive_factory(title: str, **kwargs) -> FakeArchive:
            archive = FakeArchive(title, **kwargs)
            archives.append(archive)
            return archive

        coordinator = manager(
            archive_factory=archive_factory,
            prepared_timeout_seconds=0.01,
        )
        host = coordinator.subscriber("host")
        await coordinator.prepare_host("Expiring room", host)
        await asyncio.sleep(0.04)

        self.assertFalse(coordinator.active)
        self.assertEqual([], archives)
        ended = next(
            event
            for event in drain(host)
            if event["type"] == "session_ended"
        )
        self.assertEqual("expired", ended["reason"])
        self.assertFalse(ended["archived"])

    async def test_prepared_host_refresh_restores_waiting_room(self) -> None:
        coordinator = manager()
        first_host = coordinator.subscriber("host")
        waiting = await coordinator.prepare_host("Refresh waiting", first_host)
        await coordinator.disconnect(first_host)

        replacement = coordinator.subscriber("host")
        resumed = await coordinator.resume_host(
            waiting.session_id,
            waiting.host_token,
            replacement,
        )
        snapshot = next(
            event
            for event in drain(replacement)
            if event["type"] == "session_snapshot"
        )

        self.assertIs(waiting, resumed)
        self.assertEqual("prepared", resumed.state)
        self.assertEqual("prepared", snapshot["session"]["state"])
        self.assertIsNone(resumed.archive)
        await coordinator.cancel_prepared(replacement.subscriber_id)

    async def test_host_creates_one_logical_session_and_listener_joins(self) -> None:
        coordinator = manager()
        host = coordinator.subscriber("host")
        live = await coordinator.start_host("Customer session", host)
        host_events = drain(host)
        created = next(event for event in host_events if event["type"] == "session_prepared")

        self.assertTrue(coordinator.active)
        self.assertEqual(live.session_id, created["session_id"])
        self.assertEqual(live.join_code, created["join_code"])
        self.assertTrue(created["resume_token"])

        listener = coordinator.subscriber("listener")
        await coordinator.join_listener(live.join_code.lower(), listener)
        listener_events = drain(listener)
        snapshot = next(event for event in listener_events if event["type"] == "session_snapshot")

        self.assertEqual("listener", snapshot["role"])
        self.assertIn("french_segments", snapshot)
        self.assertNotIn("english_segments", snapshot)
        await live.finalize("completed")

    async def test_role_topics_only_deliver_the_needed_transcript(self) -> None:
        coordinator = manager()
        host = coordinator.subscriber("host")
        live = await coordinator.start_host("Role routing", host)
        listener = coordinator.subscriber("listener")
        await coordinator.join_listener(live.join_code, listener)
        drain(host)
        drain(listener)

        live.accept_oci_event(
            {"type": "transcript", "text": "Hello everyone.", "is_final": True}
        )
        live.accept_oci_event(
            {"type": "translation", "english": "Hello everyone.", "french": "Bonjour à tous."}
        )

        host_types = [event["type"] for event in drain(host)]
        listener_types = [event["type"] for event in drain(listener)]
        self.assertIn("transcript", host_types)
        self.assertNotIn("translation", host_types)
        self.assertIn("translation", listener_types)
        self.assertNotIn("transcript", listener_types)
        await live.finalize("completed")

    async def test_second_host_is_rejected_but_listener_is_allowed(self) -> None:
        coordinator = manager()
        host = coordinator.subscriber("host")
        live = await coordinator.start_host("Single host", host)

        with self.assertRaises(LiveSessionError) as context:
            await coordinator.start_host(
                "Duplicate host",
                coordinator.subscriber("host"),
            )
        self.assertEqual("LIVE_SESSION_ACTIVE", context.exception.code)
        self.assertEqual(LIVE_SESSION_ACTIVE_MESSAGE, str(context.exception))

        await coordinator.join_listener(
            live.join_code,
            coordinator.subscriber("listener"),
        )
        await live.finalize("completed")

    async def test_host_refresh_resumes_same_archive_with_private_token(self) -> None:
        coordinator = manager()
        original_host = coordinator.subscriber("host")
        live = await coordinator.start_host("Resume session", original_host)
        live.accept_oci_event(
            {"type": "transcript", "text": "Before refresh.", "is_final": True}
        )
        await coordinator.disconnect(original_host)
        self.assertEqual("host_reconnecting", live.state)

        wrong_host = coordinator.subscriber("host")
        with self.assertRaises(LiveSessionError):
            await coordinator.resume_host(live.session_id, "wrong-token", wrong_host)

        resumed_host = coordinator.subscriber("host")
        resumed = await coordinator.resume_host(
            live.session_id,
            live.host_token,
            resumed_host,
        )
        snapshot = next(
            event for event in drain(resumed_host) if event["type"] == "session_snapshot"
        )
        self.assertIs(live, resumed)
        self.assertEqual(["Before refresh."], snapshot["english_segments"])
        self.assertEqual("live", live.state)
        await live.finalize("completed")

    async def test_host_grace_expiry_finalizes_interrupted_once(self) -> None:
        coordinator = manager(reconnect_grace_seconds=0.01)
        host = coordinator.subscriber("host")
        live = await coordinator.start_host("Interrupted session", host)
        await coordinator.disconnect(host)
        await asyncio.sleep(0.04)

        self.assertFalse(coordinator.active)
        self.assertEqual(["interrupted"], live.archive.finalize_calls)

    async def test_disconnect_while_speech_connects_still_expires_host_lease(
        self,
    ) -> None:
        coordinator = manager(
            session_factory=DelayedOciSession,
            reconnect_grace_seconds=0.02,
        )
        host = coordinator.subscriber("host")
        start_task = asyncio.create_task(
            coordinator.start_host("Slow connection", host)
        )
        while coordinator.current is None:
            await asyncio.sleep(0)
        live = coordinator.current
        assert live is not None

        await coordinator.disconnect(host)
        self.assertEqual("host_reconnecting", live.state)
        assert isinstance(live.oci_session, DelayedOciSession)
        live.oci_session.continue_start.set()
        await start_task
        self.assertEqual("host_reconnecting", live.state)
        await asyncio.sleep(0.04)

        self.assertFalse(coordinator.active)
        self.assertEqual(["interrupted"], live.archive.finalize_calls)

    async def test_session_factory_failure_releases_slot_and_archive(self) -> None:
        archives: list[FakeArchive] = []

        def archive_factory(title: str, **kwargs) -> FakeArchive:
            archive = FakeArchive(title, **kwargs)
            archives.append(archive)
            return archive

        def failed_session_factory(*args, **kwargs):
            raise RuntimeError("client creation failed")

        coordinator = manager(
            archive_factory=archive_factory,
            session_factory=failed_session_factory,
        )
        with self.assertRaisesRegex(RuntimeError, "client creation failed"):
            await coordinator.start_host(
                "Initialization failure",
                coordinator.subscriber("host"),
            )

        self.assertFalse(coordinator.active)
        self.assertEqual(["failed"], archives[0].finalize_calls)

    async def test_audio_is_queued_and_only_host_can_send_it(self) -> None:
        coordinator = manager()
        host = coordinator.subscriber("host")
        live = await coordinator.start_host("Audio queue", host)
        listener = coordinator.subscriber("listener")
        await coordinator.join_listener(live.join_code, listener)

        await live.queue_audio(host.subscriber_id, b"\x00\x00")
        await live.audio_queue.join()
        self.assertEqual([b"\x00\x00"], live.archive.audio)
        self.assertEqual([b"\x00\x00"], live.oci_session.audio)
        with self.assertRaises(LiveSessionError):
            await live.queue_audio(listener.subscriber_id, b"\x00\x00")
        await live.finalize("completed")

    async def test_status_endpoint_reports_role_aware_state(self) -> None:
        coordinator = manager()
        request = Request("/api/live-session", Headers())
        with patch("speech_web_server.LIVE_SESSION_MANAGER", coordinator):
            response = process_http_request(SimpleNamespace(), request)
            self.assertEqual({"active": False, "state": "idle"}, json.loads(response.body))

            host = coordinator.subscriber("host")
            live = await coordinator.start_host("Status session", host)
            response = process_http_request(SimpleNamespace(), request)
            payload = json.loads(response.body)
            self.assertTrue(payload["active"])
            self.assertEqual("live", payload["state"])
            self.assertEqual("live", payload["resume_state"])
            self.assertTrue(payload["host_connected"])
            await live.finalize("completed")

    async def test_schedule_endpoint_reports_fixed_slot_state(self) -> None:
        coordinator = manager()
        request = Request("/api/session-slots", Headers())

        with patch("speech_web_server.LIVE_SESSION_MANAGER", coordinator):
            response = process_http_request(SimpleNamespace(), request)
            payload = json.loads(response.body)

        self.assertIsNone(payload["active_code"])
        self.assertEqual(4, len(payload["slots"]))


if __name__ == "__main__":
    unittest.main()
