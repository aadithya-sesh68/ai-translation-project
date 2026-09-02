"""Server-owned live session coordination for host and listener browsers."""

from __future__ import annotations

import asyncio
import os
import secrets
import string
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from oci_speech_service import (
    OciSpeechSettings,
    SpeechTranslationSession,
    safe_error_details,
)
from session_store import SessionArchive, public_session


BrowserRole = Literal["host", "listener"]
Event = dict[str, Any]
SessionFactory = Callable[..., SpeechTranslationSession]
ArchiveFactory = Callable[[str], SessionArchive]
SettingsFactory = Callable[[], OciSpeechSettings]

LIVE_SESSION_ACTIVE_MESSAGE = "Another OraTranslate session is already active."
INVALID_JOIN_CODE_MESSAGE = "The session code is invalid or has expired."
HOST_ALREADY_CONNECTED_MESSAGE = "The host session is already open in another browser."


class LiveSessionError(RuntimeError):
    """A browser-safe live-session protocol failure."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class BrowserSubscriber:
    """One browser's bounded outbound event stream."""

    role: BrowserRole
    queue: asyncio.Queue[Event | None]
    subscriber_id: str = field(default_factory=lambda: uuid.uuid4().hex)


def _join_code() -> str:
    alphabet = string.ascii_uppercase + string.digits
    raw = "".join(secrets.choice(alphabet) for _ in range(6))
    return f"{raw[:3]}-{raw[3:]}"


def normalize_join_code(value: Any) -> str:
    raw = "".join(character for character in str(value or "").upper() if character.isalnum())
    return f"{raw[:3]}-{raw[3:]}" if len(raw) == 6 else raw


class ManagedLiveSession:
    """One logical session that outlives individual browser connections."""

    def __init__(
        self,
        manager: "LiveSessionManager",
        archive: SessionArchive,
        host: BrowserSubscriber,
        audio_queue_size: int,
    ) -> None:
        self.manager = manager
        self.archive = archive
        self.session_id = archive.session_id
        self.title = archive.title
        self.join_code = _join_code()
        self.host_token = secrets.token_urlsafe(32)
        self.state = "connecting"
        self.sequence = 0
        self.host_subscriber_id: str | None = host.subscriber_id
        self.subscribers: dict[str, BrowserSubscriber] = {
            host.subscriber_id: host
        }
        self.english_segments: list[str] = []
        self.french_segments: list[str] = []
        self.partial_english = ""
        self.latest_french = ""
        self.latest_audio_level = 0
        self.speech_ready = False
        self.oci_session: SpeechTranslationSession | None = None
        self.audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue(
            maxsize=audio_queue_size
        )
        self.audio_worker_task: asyncio.Task[None] | None = None
        self.reconnect_task: asyncio.Task[None] | None = None
        self.finalize_task: asyncio.Task[dict[str, Any] | None] | None = None
        self._finalize_lock = asyncio.Lock()

    @property
    def listener_count(self) -> int:
        return sum(
            subscriber.role == "listener"
            for subscriber in self.subscribers.values()
        )

    @property
    def host_connected(self) -> bool:
        return self.host_subscriber_id in self.subscribers

    def snapshot(self, role: BrowserRole, reason: str = "joined") -> Event:
        payload: Event = {
            "type": "session_snapshot",
            "sequence": self.sequence,
            "reason": reason,
            "role": role,
            "session": {
                "session_id": self.session_id,
                "title": self.title,
                "state": self.state,
                "listener_count": self.listener_count,
                "host_connected": self.host_connected,
            },
            "latest_audio_level": self.latest_audio_level,
        }
        if role == "host":
            payload["english_segments"] = list(self.english_segments)
            payload["partial_english"] = self.partial_english
            payload["join_code"] = self.join_code
        else:
            payload["french_segments"] = list(self.french_segments)
            payload["latest_french"] = self.latest_french
        return payload

    def _enqueue(self, subscriber: BrowserSubscriber, event: Event) -> None:
        try:
            subscriber.queue.put_nowait(event)
        except asyncio.QueueFull:
            while True:
                try:
                    subscriber.queue.get_nowait()
                    subscriber.queue.task_done()
                except asyncio.QueueEmpty:
                    break
            subscriber.queue.put_nowait(
                self.snapshot(subscriber.role, reason="outbound_backlog")
            )

    def send_to_role(self, event: Event, roles: set[BrowserRole]) -> None:
        for subscriber in tuple(self.subscribers.values()):
            if subscriber.role in roles:
                self._enqueue(subscriber, event)

    def send_to(self, subscriber: BrowserSubscriber, event: Event) -> None:
        self._enqueue(subscriber, event)

    def publish(
        self,
        event: Event,
        roles: set[BrowserRole] | None = None,
        record: bool = False,
    ) -> Event:
        self.sequence += 1
        sequenced = dict(event)
        sequenced["sequence"] = self.sequence
        if record:
            self.archive.record_event(sequenced)
        self.send_to_role(sequenced, roles or {"host", "listener"})
        return sequenced

    def accept_oci_event(self, event: Event) -> None:
        event_type = event.get("type")
        if event_type == "transcript":
            text = str(event.get("text") or "").strip()
            if event.get("is_final"):
                if text:
                    self.english_segments.append(text)
                self.partial_english = ""
            else:
                self.partial_english = text
            roles: set[BrowserRole] = {"host"}
        elif event_type == "translation":
            text = str(event.get("french") or "").strip()
            if text:
                self.french_segments.append(text)
                self.latest_french = text
            roles = {"listener"}
        elif event_type == "session_ready":
            self.speech_ready = True
            if self.host_connected:
                self.state = "live"
            roles = {"host", "listener"}
        elif event_type == "error":
            roles = {"host", "listener"}
        else:
            roles = {"host", "listener"}
        self.publish(event, roles=roles, record=True)

    async def audio_worker(self) -> None:
        while True:
            chunk = await self.audio_queue.get()
            try:
                if chunk is None:
                    return
                self.archive.write_audio(chunk)
                if not self.oci_session:
                    raise RuntimeError("The OCI Speech session isn't ready.")
                await self.oci_session.send_audio(chunk)
            except Exception as error:
                self.accept_oci_event(safe_error_details(error, "audio"))
            finally:
                self.audio_queue.task_done()

    async def queue_audio(self, subscriber_id: str, chunk: bytes) -> None:
        if subscriber_id != self.host_subscriber_id:
            raise LiveSessionError(
                "HOST_LEASE_REQUIRED",
                "Only the connected host can send microphone audio.",
            )
        try:
            await asyncio.wait_for(self.audio_queue.put(chunk), timeout=1.0)
        except TimeoutError as error:
            gap = {
                "type": "error",
                "stage": "audio_queue",
                "code": "AUDIO_BACKPRESSURE",
                "message": "Audio processing fell behind and a short section may be missing.",
            }
            self.accept_oci_event(gap)
            raise LiveSessionError(
                "AUDIO_BACKPRESSURE",
                gap["message"],
            ) from error

    def publish_audio_level(self, subscriber_id: str, value: Any) -> None:
        if subscriber_id != self.host_subscriber_id:
            return
        try:
            level = max(0, min(100, round(float(value))))
        except (TypeError, ValueError):
            return
        self.latest_audio_level = level
        self.publish(
            {"type": "audio_level", "level": level},
            roles={"listener"},
        )

    async def finalize(self, status: str) -> dict[str, Any] | None:
        async with self._finalize_lock:
            if self.state == "ended":
                return None
            self.state = "finalizing"
            self.publish(
                {
                    "type": "session_status",
                    "state": "finalizing",
                    "message": "Finalizing the recording and remaining captions...",
                }
            )
            if (
                self.reconnect_task
                and self.reconnect_task is not asyncio.current_task()
                and not self.reconnect_task.done()
            ):
                self.reconnect_task.cancel()

            await self.audio_queue.put(None)
            await self.audio_queue.join()
            if self.audio_worker_task:
                await self.audio_worker_task

            if self.oci_session:
                try:
                    await self.oci_session.stop(
                        request_final_result=status == "completed"
                    )
                except Exception as error:
                    self.accept_oci_event(
                        safe_error_details(error, "session_cleanup")
                    )

            saved_session: dict[str, Any] | None = None
            try:
                saved_session = public_session(self.archive.finalize(status))
                self.publish(
                    {"type": "session_saved", "session": saved_session}
                )
            except Exception as error:
                self.publish(
                    safe_error_details(error, "session_storage"),
                    record=True,
                )

            self.state = "ended"
            self.publish(
                {
                    "type": "session_ended",
                    "state": "ended",
                    "message": (
                        "Session ended and outputs were saved."
                        if saved_session
                        else "Session ended, but its outputs could not be saved."
                    ),
                    "session": saved_session,
                }
            )
            for subscriber in tuple(self.subscribers.values()):
                try:
                    subscriber.queue.put_nowait(None)
                except asyncio.QueueFull:
                    while True:
                        try:
                            subscriber.queue.get_nowait()
                            subscriber.queue.task_done()
                        except asyncio.QueueEmpty:
                            break
                    subscriber.queue.put_nowait(None)
            await self.manager.session_finished(self)
            return saved_session


class LiveSessionManager:
    """Own the single live session and all host/listener browser leases."""

    def __init__(
        self,
        *,
        archive_factory: ArchiveFactory = SessionArchive,
        settings_factory: SettingsFactory = OciSpeechSettings.from_environment,
        session_factory: SessionFactory = SpeechTranslationSession,
        reconnect_grace_seconds: float | None = None,
        audio_queue_size: int | None = None,
        outbound_queue_size: int | None = None,
    ) -> None:
        self.archive_factory = archive_factory
        self.settings_factory = settings_factory
        self.session_factory = session_factory
        self.reconnect_grace_seconds = (
            reconnect_grace_seconds
            if reconnect_grace_seconds is not None
            else float(os.environ.get("HOST_RECONNECT_GRACE_SECONDS", "60"))
        )
        self.audio_queue_size = (
            audio_queue_size
            if audio_queue_size is not None
            else int(os.environ.get("AUDIO_QUEUE_MAX_CHUNKS", "64"))
        )
        self.outbound_queue_size = (
            max(2, outbound_queue_size)
            if outbound_queue_size is not None
            else max(
                2,
                int(os.environ.get("CLIENT_EVENT_QUEUE_MAX_ITEMS", "128")),
            )
        )
        self.current: ManagedLiveSession | None = None
        self._create_lock = asyncio.Lock()

    @property
    def active(self) -> bool:
        return self.current is not None and self.current.state != "ended"

    def status(self) -> Event:
        current = self.current
        if not current:
            return {"active": False, "state": "idle"}
        return {
            "active": True,
            "state": current.state,
            "session_id": current.session_id,
            "title": current.title,
            "host_connected": current.host_connected,
            "listener_count": current.listener_count,
        }

    def subscriber(self, role: BrowserRole) -> BrowserSubscriber:
        return BrowserSubscriber(
            role=role,
            queue=asyncio.Queue(maxsize=self.outbound_queue_size),
        )

    async def start_host(
        self,
        title: Any,
        subscriber: BrowserSubscriber,
    ) -> ManagedLiveSession:
        async with self._create_lock:
            if self.active:
                raise LiveSessionError(
                    "LIVE_SESSION_ACTIVE",
                    LIVE_SESSION_ACTIVE_MESSAGE,
                )
            settings = self.settings_factory()
            archive = self.archive_factory(title)
            managed = ManagedLiveSession(
                self,
                archive,
                subscriber,
                self.audio_queue_size,
            )
            self.current = managed
            try:
                managed.oci_session = self.session_factory(
                    settings,
                    managed.accept_oci_event,
                    session_id=managed.session_id,
                )
                managed.send_to(
                    subscriber,
                    {
                        "type": "session_created",
                        "session_id": managed.session_id,
                        "title": managed.title,
                        "join_code": managed.join_code,
                        "resume_token": managed.host_token,
                    },
                )
                managed.publish(
                    {
                        "type": "session_status",
                        "state": "connecting",
                        "message": "Connecting to OCI Speech Realtime...",
                    },
                    record=True,
                )
                managed.audio_worker_task = asyncio.create_task(
                    managed.audio_worker()
                )
            except Exception:
                self.current = None
                archive.finalize("failed")
                raise

        try:
            await managed.oci_session.start()
            if managed.host_connected:
                managed.state = "live"
                managed.publish(
                    {
                        "type": "session_status",
                        "state": "live",
                        "message": "Live session in progress.",
                    }
                )
            return managed
        except Exception as error:
            managed.accept_oci_event(safe_error_details(error, "session"))
            await managed.finalize("failed")
            raise

    async def resume_host(
        self,
        session_id: Any,
        resume_token: Any,
        subscriber: BrowserSubscriber,
    ) -> ManagedLiveSession:
        current = self.current
        if (
            not current
            or current.session_id != session_id
            or current.state != "host_reconnecting"
            or not secrets.compare_digest(
                current.host_token,
                str(resume_token or ""),
            )
        ):
            raise LiveSessionError(
                "HOST_RESUME_DENIED",
                "The host session could not be resumed.",
            )
        if current.host_connected:
            raise LiveSessionError(
                "HOST_ALREADY_CONNECTED",
                HOST_ALREADY_CONNECTED_MESSAGE,
            )
        if current.reconnect_task and not current.reconnect_task.done():
            current.reconnect_task.cancel()
        current.subscribers[subscriber.subscriber_id] = subscriber
        current.host_subscriber_id = subscriber.subscriber_id
        current.state = "live" if current.speech_ready else "connecting"
        current.send_to(subscriber, current.snapshot("host", reason="resumed"))
        current.publish(
            {
                "type": "session_status",
                "state": current.state,
                "message": (
                    "The host reconnected. Live captions have resumed."
                    if current.speech_ready
                    else "The host reconnected. Speech is still connecting."
                ),
            }
        )
        return current

    async def join_listener(
        self,
        join_code: Any,
        subscriber: BrowserSubscriber,
    ) -> ManagedLiveSession:
        current = self.current
        if (
            not current
            or normalize_join_code(join_code) != current.join_code
            or current.state not in {
                "connecting",
                "live",
                "host_reconnecting",
            }
        ):
            raise LiveSessionError(
                "INVALID_JOIN_CODE",
                INVALID_JOIN_CODE_MESSAGE,
            )
        current.subscribers[subscriber.subscriber_id] = subscriber
        current.send_to(subscriber, current.snapshot("listener"))
        current.publish(
            {
                "type": "listener_count",
                "count": current.listener_count,
            },
            roles={"host"},
        )
        return current

    async def disconnect(self, subscriber: BrowserSubscriber) -> None:
        current = self.current
        if not current or subscriber.subscriber_id not in current.subscribers:
            return
        current.subscribers.pop(subscriber.subscriber_id, None)
        if subscriber.role == "listener":
            current.publish(
                {
                    "type": "listener_count",
                    "count": current.listener_count,
                },
                roles={"host"},
            )
            return
        if current.host_subscriber_id != subscriber.subscriber_id:
            return
        current.host_subscriber_id = None
        if current.state not in {"connecting", "live", "host_reconnecting"}:
            return
        current.state = "host_reconnecting"
        current.publish(
            {
                "type": "session_status",
                "state": "host_reconnecting",
                "message": "Speaker reconnecting. Captions are temporarily paused.",
                "grace_seconds": self.reconnect_grace_seconds,
            },
            roles={"listener"},
        )
        current.reconnect_task = asyncio.create_task(
            self._expire_host_lease(current)
        )

    async def _expire_host_lease(self, managed: ManagedLiveSession) -> None:
        try:
            await asyncio.sleep(self.reconnect_grace_seconds)
            if (
                self.current is managed
                and not managed.host_connected
                and managed.state == "host_reconnecting"
            ):
                await managed.finalize("interrupted")
        except asyncio.CancelledError:
            pass

    async def stop_host(self, subscriber_id: str) -> dict[str, Any] | None:
        current = self.current
        if not current or subscriber_id != current.host_subscriber_id:
            raise LiveSessionError(
                "HOST_LEASE_REQUIRED",
                "Only the connected host can end this session.",
            )
        if current.finalize_task is None:
            current.finalize_task = asyncio.create_task(
                current.finalize("completed")
            )
        return await current.finalize_task

    async def session_finished(self, managed: ManagedLiveSession) -> None:
        if self.current is managed:
            self.current = None
