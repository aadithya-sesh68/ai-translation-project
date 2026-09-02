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
from live_session_manager import (
    LIVE_SESSION_ACTIVE_MESSAGE,
    BrowserSubscriber,
    LiveSessionError,
    LiveSessionManager,
)
from session_store import (
    SessionTitleValidationError,
    delete_session,
    get_session,
    get_session_file,
    list_sessions,
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


def static_file_for_path(path: str) -> Path | None:
    """Resolve a page asset without allowing traversal outside web/assets/."""

    known_file = STATIC_FILES.get(path)
    if known_file is not None:
        return known_file
    if not path.startswith("/assets/"):
        return None

    assets_root = (WEB_ROOT / "assets").resolve()
    relative_path = unquote(path).removeprefix("/assets/")
    candidate = (assets_root / relative_path).resolve()
    try:
        candidate.relative_to(assets_root)
    except ValueError:
        return None
    return candidate


LIVE_SESSION_MANAGER = LiveSessionManager()


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
            LIVE_SESSION_MANAGER.status(),
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

    file_path = static_file_for_path(path)
    if not file_path or not file_path.is_file():
        return response(
            HTTPStatus.NOT_FOUND,
            b"Not found",
            "text/plain; charset=utf-8",
        )

    content_type = mimetypes.guess_type(file_path.name)[0]
    content_type = content_type or "application/octet-stream"
    if content_type.startswith("text/") or content_type in {
        "application/javascript",
        "application/json",
        "image/svg+xml",
    }:
        content_type = f"{content_type}; charset=utf-8"
    return response(
        HTTPStatus.OK,
        file_path.read_bytes(),
        content_type,
    )


async def send_events(
    websocket: ServerConnection,
    event_queue: asyncio.Queue[dict[str, Any] | None],
) -> None:
    while True:
        event = await event_queue.get()
        try:
            if event is None:
                await websocket.close()
                return
            await websocket.send(json.dumps(event))
        except ConnectionClosed:
            return
        finally:
            event_queue.task_done()


async def handle_live_session(websocket: ServerConnection) -> None:
    if (
        not websocket.request
        or urlsplit(websocket.request.path).path != "/ws/live"
    ):
        await websocket.close(code=1008, reason="Unsupported WebSocket path")
        return
    subscriber: BrowserSubscriber | None = None
    sender_task: asyncio.Task[None] | None = None
    managed = None

    try:
        first_message = await websocket.recv()
        if isinstance(first_message, bytes):
            raise LiveSessionError(
                "SESSION_COMMAND_REQUIRED",
                "Choose Host or Listener before sending audio.",
            )
        try:
            command = json.loads(first_message)
        except json.JSONDecodeError as error:
            raise LiveSessionError(
                "SESSION_COMMAND_REQUIRED",
                "The session request was not valid.",
            ) from error
        if not isinstance(command, dict):
            raise LiveSessionError(
                "SESSION_COMMAND_REQUIRED",
                "The session request was not valid.",
            )

        command_type = command.get("type")
        role = "listener" if command_type == "join" else "host"
        subscriber = LIVE_SESSION_MANAGER.subscriber(role)
        sender_task = asyncio.create_task(
            send_events(websocket, subscriber.queue)
        )

        if command_type == "start":
            managed = await LIVE_SESSION_MANAGER.start_host(
                command.get("title"),
                subscriber,
            )
            log_event(
                LOGGER,
                logging.INFO,
                "live_session_created",
                "Server-owned live session created",
                session_id=managed.session_id,
                role="host",
            )
        elif command_type == "resume":
            managed = await LIVE_SESSION_MANAGER.resume_host(
                command.get("session_id"),
                command.get("resume_token"),
                subscriber,
            )
        elif command_type == "join":
            managed = await LIVE_SESSION_MANAGER.join_listener(
                command.get("join_code"),
                subscriber,
            )
        else:
            raise LiveSessionError(
                "SESSION_COMMAND_REQUIRED",
                "Choose Host or Listener to continue.",
            )

        async for message in websocket:
            if isinstance(message, bytes):
                if subscriber.role != "host":
                    continue
                if len(message) > 128 * 1024:
                    managed.accept_oci_event(
                        {
                            "type": "error",
                            "stage": "audio",
                            "code": "AUDIO_CHUNK_TOO_LARGE",
                            "message": "Audio chunk exceeded 128 KiB.",
                        }
                    )
                    continue
                if len(message) % 2:
                    managed.accept_oci_event(
                        {
                            "type": "error",
                            "stage": "audio",
                            "code": "INVALID_PCM_LENGTH",
                            "message": "PCM audio chunk had an invalid length.",
                        }
                    )
                    continue
                await managed.queue_audio(subscriber.subscriber_id, message)
                continue

            try:
                browser_command = json.loads(message)
            except json.JSONDecodeError:
                continue
            if not isinstance(browser_command, dict):
                continue
            if (
                browser_command.get("type") == "audio_level"
                and subscriber.role == "host"
            ):
                managed.publish_audio_level(
                    subscriber.subscriber_id,
                    browser_command.get("level"),
                )
            elif (
                browser_command.get("type") == "stop"
                and subscriber.role == "host"
            ):
                await LIVE_SESSION_MANAGER.stop_host(
                    subscriber.subscriber_id
                )
                break

    except (LiveSessionError, SessionTitleValidationError) as error:
        code = getattr(error, "code", "SESSION_REJECTED")
        log_event(
            LOGGER,
            logging.WARNING,
            "live_session_rejected",
            str(error),
            stage="session",
            code=code,
        )
        rejection = {
            "type": "session_rejected",
            "stage": "session",
            "code": code,
            "message": str(error),
        }
        if subscriber:
            subscriber.queue.put_nowait(rejection)
        else:
            await websocket.send(json.dumps(rejection))
    except ConnectionClosed as error:
        log_event(
            LOGGER,
            logging.INFO,
            "browser_websocket_closed",
            "Browser WebSocket closed",
            session_id=managed.session_id if managed else None,
            role=subscriber.role if subscriber else None,
            status=getattr(error, "code", None),
            close_reason=getattr(error, "reason", None),
        )
    except Exception as error:
        details = safe_error_details(error, "session")
        LOGGER.exception(
            "Live session browser connection failed",
            extra={
                "event": "live_session_connection_failed",
                "session_id": managed.session_id if managed else None,
                "role": subscriber.role if subscriber else None,
                "error_type": type(error).__name__,
            },
        )
        if subscriber and managed and managed.state != "ended":
            managed.accept_oci_event(details)
        elif subscriber:
            subscriber.queue.put_nowait(details)
    finally:
        if subscriber:
            await LIVE_SESSION_MANAGER.disconnect(subscriber)
            if sender_task and not sender_task.done():
                await subscriber.queue.put(None)
        if sender_task:
            await sender_task
        else:
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

    path = (
        urlsplit(websocket.request.path).path if websocket.request else ""
    )
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
