"""Local browser server for live OCI transcription and translation."""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from websockets.asyncio.server import ServerConnection, serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

from oci_speech_service import (
    OciSpeechSettings,
    SpeechTranslationSession,
    run_translation_reliability_test,
    safe_error_details,
)
from session_store import (
    SessionArchive,
    delete_session,
    get_session,
    get_session_file,
    list_sessions,
    public_session,
)
from structured_logging import configure_structured_logging, log_event


LOGGER = logging.getLogger("speech_web_server")
WEB_ROOT = Path(__file__).parent / "web"
STATIC_FILES = {
    "/": WEB_ROOT / "index.html",
    "/app.js": WEB_ROOT / "app.js",
    "/audio-worklet.js": WEB_ROOT / "audio-worklet.js",
    "/styles.css": WEB_ROOT / "styles.css",
    "/url-utils.js": WEB_ROOT / "url-utils.js",
    "/translation-test.html": WEB_ROOT / "translation-test.html",
    "/translation-test.js": WEB_ROOT / "translation-test.js",
    "/translation-test.css": WEB_ROOT / "translation-test.css",
}


class LiveSessionCoordinator:
    """Allow one authoritative live audio-capture session per server process."""

    def __init__(self) -> None:
        self._owner: asyncio.Task[Any] | None = None

    @property
    def active(self) -> bool:
        return self._owner is not None and not self._owner.done()

    def try_acquire(self, owner: asyncio.Task[Any]) -> bool:
        """Atomically claim the single live-session slot on this event loop."""

        if self.active:
            return False

        self._owner = owner
        owner.add_done_callback(self.release)
        return True

    def release(self, owner: asyncio.Task[Any]) -> None:
        if self._owner is owner:
            self._owner = None


LIVE_SESSION_COORDINATOR = LiveSessionCoordinator()
LIVE_SESSION_ACTIVE_MESSAGE = "Another OraTranslate session is already active."


def allowed_websocket_origins(port: int) -> list[str]:
    """Return local and explicitly configured public browser origins."""

    origins = [
        f"http://localhost:{port}",
        f"http://127.0.0.1:{port}",
    ]
    configured = os.environ.get("SPEECH_WEB_ALLOWED_ORIGINS", "")
    for raw_origin in configured.split(","):
        origin = raw_origin.strip().rstrip("/")
        if not origin:
            continue
        parsed = urlsplit(origin)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise ValueError(
                "SPEECH_WEB_ALLOWED_ORIGINS must contain comma-separated "
                "HTTP(S) origins without paths, queries, or credentials."
            )
        if origin not in origins:
            origins.append(origin)
    return origins


def response(
    status: HTTPStatus,
    body: bytes,
    content_type: str,
    extra_headers: list[tuple[str, str]] | None = None,
) -> Response:
    header_values = [
        ("Content-Type", content_type),
        ("Content-Length", str(len(body))),
        ("Cache-Control", "no-store"),
        ("X-Content-Type-Options", "nosniff"),
        ("Referrer-Policy", "no-referrer"),
        (
            "Content-Security-Policy",
            "default-src 'self'; connect-src 'self' ws: wss:; "
            "script-src 'self'; style-src 'self'; "
            "worker-src 'self'",
        ),
    ]
    if extra_headers:
        header_values.extend(extra_headers)
    headers = Headers(header_values)
    return Response(status.value, status.phrase, headers, body)


def json_response(status: HTTPStatus, payload: Any) -> Response:
    return response(
        status,
        json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        "application/json; charset=utf-8",
    )


def session_api_response(request: Request, path: str) -> Response | None:
    """Serve session retrieval and explicit permanent deletion."""

    if path != "/api/sessions" and not path.startswith("/api/sessions/"):
        return None
    if request.method not in {"GET", "DELETE"}:
        return json_response(
            HTTPStatus.METHOD_NOT_ALLOWED,
            {"message": "Only GET and DELETE are supported for saved sessions."},
        )

    try:
        parts = [unquote(part) for part in path.strip("/").split("/")]
        if request.method == "DELETE":
            if len(parts) != 3:
                return json_response(
                    HTTPStatus.METHOD_NOT_ALLOWED,
                    {"message": "DELETE requires one saved session identifier."},
                )
            return json_response(
                HTTPStatus.OK,
                {"deleted_session": delete_session(parts[2])},
            )

        if path == "/api/sessions":
            return json_response(HTTPStatus.OK, {"sessions": list_sessions()})

        if len(parts) == 3:
            return json_response(HTTPStatus.OK, get_session(parts[2]))
        if len(parts) == 4:
            requested_name = {
                "audio.mp3": "session.mp3",
                "english.txt": "english.txt",
                "french.txt": "french.txt",
                "session-report.json": "session_report.json",
            }.get(parts[3])
            if not requested_name:
                raise FileNotFoundError(path)
            file_path = get_session_file(parts[2], requested_name)
            if requested_name == "session.mp3":
                content_type = "audio/mpeg"
            elif requested_name == "session_report.json":
                content_type = "application/json; charset=utf-8"
            else:
                content_type = "text/plain; charset=utf-8"
            return response(
                HTTPStatus.OK,
                file_path.read_bytes(),
                content_type,
                [("Content-Disposition", f'inline; filename="{parts[3]}"')],
            )
    except (FileNotFoundError, ValueError, KeyError, json.JSONDecodeError):
        return json_response(
            HTTPStatus.NOT_FOUND,
            {"message": "The saved session or output was not found."},
        )
    except OSError:
        LOGGER.exception("Saved session storage operation failed")
        return json_response(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            {"message": "The saved session storage operation failed."},
        )

    return json_response(HTTPStatus.NOT_FOUND, {"message": "Not found."})


def process_http_request(
    connection: ServerConnection,
    request: Request,
) -> Response | None:
    path = urlsplit(request.path).path
    if path in {"/ws/live", "/ws/translation-test"}:
        return None

    if path == "/api/live-session":
        if request.method != "GET":
            return json_response(
                HTTPStatus.METHOD_NOT_ALLOWED,
                {"message": "Only GET is supported for live-session status."},
            )
        return json_response(
            HTTPStatus.OK,
            {"active": LIVE_SESSION_COORDINATOR.active},
        )

    api_response = session_api_response(request, path)
    if api_response is not None:
        return api_response

    if path in {"/health", "/api/health"}:
        try:
            settings = OciSpeechSettings.from_environment()
            payload: dict[str, Any] = {
                "status": "ok",
                "authentication": "api_key",
                "region": settings.region,
                "profile": settings.profile_name,
            }
            status = HTTPStatus.OK
        except Exception as error:
            payload = {
                "status": "configuration_error",
                "message": str(error),
            }
            status = HTTPStatus.SERVICE_UNAVAILABLE

        return response(
            status,
            json.dumps(payload).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    if path == "/favicon.ico":
        return response(HTTPStatus.NO_CONTENT, b"", "image/x-icon")

    file_path = STATIC_FILES.get(path)
    if not file_path or not file_path.is_file():
        return response(
            HTTPStatus.NOT_FOUND,
            b"Not found",
            "text/plain; charset=utf-8",
        )

    content_type = mimetypes.guess_type(file_path.name)[0]
    return response(
        HTTPStatus.OK,
        file_path.read_bytes(),
        f"{content_type or 'application/octet-stream'}; charset=utf-8",
    )


async def send_events(
    websocket: ServerConnection,
    event_queue: asyncio.Queue[dict[str, Any] | None],
) -> None:
    while True:
        event = await event_queue.get()
        try:
            if event is None:
                return
            await websocket.send(json.dumps(event))
        except ConnectionClosed:
            return
        finally:
            event_queue.task_done()


async def handle_live_session(websocket: ServerConnection) -> None:
    if not websocket.request or websocket.request.path != "/ws/live":
        await websocket.close(code=1008, reason="Unsupported WebSocket path")
        return

    owner = asyncio.current_task()
    if owner is None or not LIVE_SESSION_COORDINATOR.try_acquire(owner):
        log_event(
            LOGGER,
            logging.WARNING,
            "live_session_rejected",
            LIVE_SESSION_ACTIVE_MESSAGE,
            stage="session",
            code="LIVE_SESSION_ACTIVE",
        )
        await websocket.send(
            json.dumps(
                {
                    "type": "session_rejected",
                    "stage": "session",
                    "code": "LIVE_SESSION_ACTIVE",
                    "message": LIVE_SESSION_ACTIVE_MESSAGE,
                }
            )
        )
        await websocket.close(code=1013, reason=LIVE_SESSION_ACTIVE_MESSAGE)
        return

    event_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    def publish(event: dict[str, Any]) -> None:
        event_queue.put_nowait(event)

    sender_task = asyncio.create_task(send_events(websocket, event_queue))
    session: SpeechTranslationSession | None = None
    archive: SessionArchive | None = None
    session_started = False
    stop_requested = False

    #Record event in Session Archive and send event to browser
    def publish_and_record(event: dict[str, Any]) -> None:
        if archive:
            archive.record_event(event)
        publish(event)

    try:
        archive = SessionArchive()
        settings = OciSpeechSettings.from_environment()
        session = SpeechTranslationSession(
            settings,
            publish_and_record,
            session_id=archive.session_id,
        )
        log_event(
            LOGGER,
            logging.INFO,
            "live_session_created",
            "Browser live session created",
            session_id=archive.session_id,
            region=settings.region,
            profile=settings.profile_name,
            authentication="api_key",
        )
        publish_and_record(
            {
                "type": "session_status",
                "state": "connecting",
                "message": "Connecting to OCI Speech Realtime...",
            }
        )
        await session.start()
        session_started = True

        async for message in websocket:
            if isinstance(message, bytes):
                if len(message) > 128 * 1024:
                    publish(
                        {
                            "type": "error",
                            "stage": "audio",
                            "message": "Audio chunk exceeded 128 KiB.",
                        }
                    )
                    continue

                if len(message) % 2:
                    publish_and_record(
                        {
                            "type": "error",
                            "stage": "audio",
                            "message": "PCM audio chunk had an invalid length.",
                        }
                    )
                    continue

                archive.write_audio(message)
                await session.send_audio(message)
                continue

            try:
                command = json.loads(message)
            except json.JSONDecodeError:
                continue

            if command.get("type") == "start":
                archive.set_title(command.get("title"))
            elif command.get("type") == "stop":
                stop_requested = True
                publish_and_record(
                    {
                        "type": "session_status",
                        "state": "finalizing",
                        "message": "Finalizing the last speech segment...",
                    }
                )
                break

    except ConnectionClosed as error:
        log_event(
            LOGGER,
            logging.INFO,
            "browser_websocket_closed",
            "Browser WebSocket closed",
            session_id=archive.session_id if archive else None,
            status=getattr(error, "code", None),
            close_reason=getattr(error, "reason", None),
        )
    except Exception as error:
        details = safe_error_details(error, "session")
        LOGGER.exception(
            "Live session failed",
            extra={
                "event": "live_session_failed",
                "session_id": archive.session_id if archive else None,
                "stage": "session",
                "status": details.get("status"),
                "code": details.get("code"),
                "opc_request_id": details.get("opc_request_id"),
                "error_type": type(error).__name__,
            },
        )
        publish_and_record(details)
    finally:
        if session:
            try:
                await session.stop(
                    request_final_result=session_started and stop_requested
                )
            except Exception as error:
                details = safe_error_details(error, "session_cleanup")
                LOGGER.exception(
                    "Live session cleanup failed",
                    extra={
                        "event": "live_session_cleanup_failed",
                        "session_id": archive.session_id if archive else None,
                        "stage": "session_cleanup",
                        "status": details.get("status"),
                        "code": details.get("code"),
                        "opc_request_id": details.get("opc_request_id"),
                        "error_type": type(error).__name__,
                    },
                )
                publish_and_record(details)

        saved_session: dict[str, Any] | None = None
        if archive:
            try:
                saved_session = public_session(
                    archive.finalize(
                        "completed"
                        if stop_requested
                        else "interrupted"
                        if session_started
                        else "failed"
                    )
                )
                publish(
                    {
                        "type": "session_saved",
                        "session": saved_session,
                    }
                )
                log_event(
                    LOGGER,
                    logging.INFO,
                    "session_archive_saved",
                    "Session recording and transcripts saved",
                    session_id=archive.session_id,
                    session_status=saved_session.get("status"),
                    duration_seconds=saved_session.get("duration_seconds"),
                    audio_available=saved_session.get("audio_available"),
                    english_available=saved_session.get("english_available"),
                    french_available=saved_session.get("french_available"),
                )
            except Exception as error:
                LOGGER.exception(
                    "Session output could not be saved",
                    extra={
                        "event": "session_archive_failed",
                        "session_id": archive.session_id,
                        "stage": "session_storage",
                        "error_type": type(error).__name__,
                    },
                )
                publish(safe_error_details(error, "session_storage"))

        if stop_requested:
            publish(
                {
                    "type": "session_stopped",
                    "message": (
                        "Session ended and outputs were saved."
                        if saved_session
                        else "Session ended, but its outputs could not be saved."
                    ),
                }
            )

        await event_queue.put(None)
        await sender_task
        LIVE_SESSION_COORDINATOR.release(owner)
        await websocket.close()


def validate_translation_test_command(
    command: dict[str, Any],
) -> tuple[list[str], int, int]:
    """Validate a bounded, controlled translation test request."""

    raw_sentences = command.get("sentences")
    if not isinstance(raw_sentences, list):
        raise ValueError("The test requires an array of sentences.")

    sentences = [
        sentence.strip()
        for sentence in raw_sentences
        if isinstance(sentence, str) and sentence.strip()
    ]
    if not 1 <= len(sentences) <= 50:
        raise ValueError("Provide between 1 and 50 non-empty sentences.")
    if any(len(sentence) > 5000 for sentence in sentences):
        raise ValueError("Each sentence must be 5,000 characters or fewer.")
    if sum(map(len, sentences)) > 20000:
        raise ValueError("The test is limited to 20,000 total characters.")

    concurrency = command.get("concurrency", 1)
    delay_ms = command.get("delay_ms", 250)
    if not isinstance(concurrency, int) or not 1 <= concurrency <= 5:
        raise ValueError("Concurrency must be an integer from 1 to 5.")
    if not isinstance(delay_ms, int) or not 0 <= delay_ms <= 5000:
        raise ValueError("Delay must be an integer from 0 to 5,000 ms.")

    return sentences, concurrency, delay_ms


async def handle_translation_test(websocket: ServerConnection) -> None:
    """Run controlled OCI Language calls for a separate browser tab."""

    event_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    def publish(event: dict[str, Any]) -> None:
        event_queue.put_nowait(event)

    sender_task = asyncio.create_task(send_events(websocket, event_queue))
    test_task: asyncio.Task[None] | None = None

    async def run_test(command: dict[str, Any]) -> None:
        try:
            sentences, concurrency, delay_ms = (
                validate_translation_test_command(command)
            )
            settings = OciSpeechSettings.from_environment()
            await run_translation_reliability_test(
                settings=settings,
                sentences=sentences,
                concurrency=concurrency,
                delay_ms=delay_ms,
                event_sink=publish,
            )
        except ValueError as error:
            publish(
                {
                    "type": "error",
                    "stage": "translation_test_configuration",
                    "message": str(error),
                }
            )
        except asyncio.CancelledError:
            publish(
                {
                    "type": "translation_test_stopped",
                    "message": "Translation test stopped.",
                }
            )
            raise
        except Exception as error:
            LOGGER.exception("Translation reliability test failed")
            publish(safe_error_details(error, "translation_test"))

    try:
        async for message in websocket:
            if isinstance(message, bytes):
                continue
            try:
                command = json.loads(message)
            except json.JSONDecodeError:
                continue

            if command.get("type") == "start":
                if test_task and not test_task.done():
                    publish(
                        {
                            "type": "error",
                            "stage": "translation_test",
                            "message": "A translation test is already running.",
                        }
                    )
                    continue
                test_task = asyncio.create_task(run_test(command))
            elif command.get("type") == "stop":
                if test_task and not test_task.done():
                    test_task.cancel()
                    await asyncio.gather(test_task, return_exceptions=True)
    except ConnectionClosed:
        pass
    finally:
        if test_task and not test_task.done():
            test_task.cancel()
            await asyncio.gather(test_task, return_exceptions=True)
        await event_queue.put(None)
        await sender_task
        await websocket.close()


async def handle_websocket(websocket: ServerConnection) -> None:
    """Dispatch each same-origin WebSocket path to its handler."""

    path = websocket.request.path if websocket.request else ""
    if path == "/ws/live":
        await handle_live_session(websocket)
    elif path == "/ws/translation-test":
        await handle_translation_test(websocket)
    else:
        await websocket.close(code=1008, reason="Unsupported WebSocket path")


async def main() -> None:
    host = os.environ.get(
        "SPEECH_WEB_HOST",
        os.environ.get("ORATRANSLATE_HOST", "127.0.0.1"),
    )
    port = int(
        os.environ.get(
            "SPEECH_WEB_PORT",
            os.environ.get("ORATRANSLATE_PORT", "8765"),
        )
    )
    allowed_origins = allowed_websocket_origins(port)

    log_event(
        LOGGER,
        logging.INFO,
        "server_starting",
        "OraTranslate server starting",
        host=host,
        port=port,
        allowed_origins=allowed_origins,
    )

    async with serve(
        handle_websocket,
        host,
        port,
        origins=allowed_origins,
        process_request=process_http_request,
        compression=None,
        max_size=128 * 1024,
    ):
        await asyncio.get_running_loop().create_future()


if __name__ == "__main__":
    configured_level = os.environ.get("ORATRANSLATE_LOG_LEVEL", "INFO").upper()
    configure_structured_logging(
        getattr(logging, configured_level, logging.INFO)
    )
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
