"""Reusable OCI Speech Realtime and OCI Language API-key support."""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import oci
from oci.ai_language import AIServiceLanguageClient
from oci.ai_speech.models import RealtimeParameters
from oci_ai_speech_realtime import (
    RealtimeSpeechClient,
    RealtimeSpeechClientListener,
)

from structured_logging import log_event
from translation_service import TranslationService, safe_error_details


EventSink = Callable[[dict[str, Any]], None]
LOGGER = logging.getLogger("oci_speech_service")


@dataclass(frozen=True)
class OciSpeechSettings:
    """Non-secret runtime settings for an OCI speech and translation session."""

    config_file: str
    profile_name: str
    compartment_id: str
    region: str
    translation_buffer_seconds: float = 1.5
    translation_queue_max_items: int = 120
    speech_ready_timeout_seconds: float = 15.0
    final_result_wait_seconds: float = 2.0

    @classmethod
    def from_environment(cls) -> "OciSpeechSettings":
        config_file = os.environ.get(
            "OCI_CONFIG_FILE",
            str(Path.home() / ".oci" / "config"),
        )
        profile_name = os.environ.get(
            "OCI_CONFIG_PROFILE",
            "DEFAULT",
        )

        config = load_api_key_config(config_file, profile_name)

        compartment_id = os.environ.get("OCI_COMPARTMENT_ID", "").strip()
        if not compartment_id:
            raise ValueError(
                "OCI_COMPARTMENT_ID must be set to the compartment OCID."
            )

        return cls(
            config_file=config_file,
            profile_name=profile_name,
            compartment_id=compartment_id,
            region=os.environ.get(
                "OCI_REGION",
                config.get("region", "us-chicago-1"),
            ),
            translation_buffer_seconds=float(
                os.environ.get("TRANSLATION_BUFFER_SECONDS", "1.5")
            ),
            translation_queue_max_items=int(
                os.environ.get("TRANSLATION_QUEUE_MAX_ITEMS", "120")
            ),
        )


def load_api_key_config(
    config_file: str,
    profile_name: str,
    region: str | None = None,
) -> dict[str, str]:
    """Load and validate a persistent OCI API-key profile."""

    config = oci.config.from_file(
        file_location=config_file,
        profile_name=profile_name,
    )

    if config.get("security_token_file") or config.get(
        "authentication_type"
    ):
        raise ValueError(
            f"OCI profile '{profile_name}' is not an API-key profile. "
            "Select a profile containing tenancy, user, fingerprint, "
            "key_file, and region without session-token settings."
        )

    if region:
        config["region"] = region

    oci.config.validate_config(config)
    return config


def create_api_key_signer(config: dict[str, str]) -> oci.signer.Signer:
    """Create one explicit OCI request signer for a single service client."""

    return oci.signer.Signer(
        tenancy=config["tenancy"],
        user=config["user"],
        fingerprint=config["fingerprint"],
        private_key_file_location=config.get("key_file"),
        pass_phrase=oci.config.get_config_value_or_default(
            config,
            "pass_phrase",
        ),
        private_key_content=config.get("key_content"),
    )


def create_api_key_language_client(
    settings: OciSpeechSettings,
) -> AIServiceLanguageClient:
    """Create an OCI Language client with the explicit API-key signer."""

    config = load_api_key_config(
        settings.config_file,
        settings.profile_name,
        settings.region,
    )
    signer = create_api_key_signer(config)
    return AIServiceLanguageClient(config=config, signer=signer)


def create_api_key_realtime_client(
    settings: OciSpeechSettings,
    config: dict[str, str],
    signer: oci.signer.Signer,
    parameters: RealtimeParameters,
    listener: RealtimeSpeechClientListener,
) -> RealtimeSpeechClient:
    """Create Realtime Speech with its dedicated API-key signer."""

    return RealtimeSpeechClient(
        config=config,
        realtime_speech_parameters=parameters,
        listener=listener,
        service_endpoint=(
            f"wss://realtime.aiservice.{settings.region}."
            "oci.oraclecloud.com"
        ),
        signer=signer,
        compartment_id=settings.compartment_id,
    )


async def run_translation_reliability_test(
    settings: OciSpeechSettings,
    sentences: list[str],
    concurrency: int,
    delay_ms: int,
    event_sink: EventSink,
) -> None:
    """Translate each sentence once and stream request-level diagnostics."""

    config = load_api_key_config(
        settings.config_file,
        settings.profile_name,
        settings.region,
    )
    test_id = f"translation-test-{uuid.uuid4().hex[:12]}"
    work_queue: asyncio.Queue[tuple[int, str] | None] = asyncio.Queue()
    result_codes: Counter[str] = Counter()
    test_started = time.perf_counter()

    for index, sentence in enumerate(sentences, start=1):
        work_queue.put_nowait((index, sentence))
    for _ in range(concurrency):
        work_queue.put_nowait(None)

    event_sink(
        {
            "type": "translation_test_started",
            "test_id": test_id,
            "total": len(sentences),
            "concurrency": concurrency,
            "delay_ms": delay_ms,
        }
    )

    log_event(
        LOGGER,
        logging.INFO,
        "translation_test_started",
        "OCI Language reliability test started",
        session_id=test_id,
        total=len(sentences),
        concurrency=concurrency,
        delay_ms=delay_ms,
    )

    async def translate_one(
        service: TranslationService,
        index: int,
        english_text: str,
    ) -> None:
        result = await service.translate(
            english_text,
            document_key=f"reliability-test-{index}",
            stage="translation_test",
        )

        if result.get("french") is not None:
            result_codes["success"] += 1
            event_sink(
                {
                    "type": "translation_test_result",
                    "index": index,
                    "english": english_text,
                    **result,
                }
            )
            return

        event = {
            **result,
            "type": "translation_test_result",
            "index": index,
            "english": english_text,
        }
        result_key = str(event.get("status") or event.get("code") or "error")
        result_codes[result_key] += 1
        event_sink(event)

    async def worker() -> None:
        # Each concurrent worker owns an independent Language signer/client.
        language_signer = create_api_key_signer(config)
        service = TranslationService.from_config(
            dict(config),
            language_signer,
            settings.compartment_id,
            test_id,
        )
        while True:
            item = await work_queue.get()
            try:
                if item is None:
                    return
                await translate_one(service, *item)
                if delay_ms:
                    await asyncio.sleep(delay_ms / 1000)
            finally:
                work_queue.task_done()

    workers = [asyncio.create_task(worker()) for _ in range(concurrency)]
    try:
        await work_queue.join()
        await asyncio.gather(*workers)
    except asyncio.CancelledError:
        for worker_task in workers:
            worker_task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        raise

    event_sink(
        {
            "type": "translation_test_complete",
            "test_id": test_id,
            "total": len(sentences),
            "counts": dict(result_codes),
            "elapsed_ms": round(
                (time.perf_counter() - test_started) * 1000
            ),
        }
    )
    log_event(
        LOGGER,
        logging.INFO,
        "translation_test_completed",
        "OCI Language reliability test completed",
        session_id=test_id,
        total=len(sentences),
        counts=dict(result_codes),
        elapsed_ms=round((time.perf_counter() - test_started) * 1000),
    )


class SpeechTranslationListener(RealtimeSpeechClientListener):
    """Receive Speech results and queue final English segments for translation."""

    def __init__(
        self,
        translation_service: TranslationService,
        event_sink: EventSink,
        event_loop: asyncio.AbstractEventLoop,
        translation_buffer_seconds: float,
        translation_queue_max_items: int = 120,
        session_id: str | None = None,
    ) -> None:
        self.translation_service = translation_service
        self.event_sink = event_sink
        self.event_loop = event_loop
        self.translation_buffer_seconds = translation_buffer_seconds
        self.session_id = session_id

        self.pending_segments: list[str] = []
        self.translation_queue: asyncio.Queue[str | None] = asyncio.Queue(
            maxsize=max(1, translation_queue_max_items)
        )
        self.translation_worker_task: asyncio.Task[None] | None = None
        self.buffer_timer_task: asyncio.Task[None] | None = None

        self.ready = asyncio.Event()
        self.failed = asyncio.Event()
        self.last_speech_error: dict[str, Any] | None = None

    def start(self) -> None:
        self.translation_worker_task = asyncio.create_task(
            self.translation_worker()
        )

    def _publish(self, event: dict[str, Any]) -> None:
        try:
            if asyncio.get_running_loop() is self.event_loop:
                self.event_sink(event)
                return
            self.event_loop.call_soon_threadsafe(self.event_sink, event)
        except RuntimeError:
            pass

    def on_network_event(self, message: Any) -> None:
        pass

    def on_ack_message(self, message: Any) -> None:
        pass

    def on_connect(self) -> None:
        log_event(
            LOGGER,
            logging.INFO,
            "speech_websocket_connected",
            "OCI Speech Realtime WebSocket connected",
            session_id=self.session_id,
        )
        self._publish(
            {
                "type": "session_status",
                "state": "connected",
                "message": "Connected to OCI Speech Realtime.",
            }
        )

    def on_connect_message(self, message: Any) -> None:
        log_event(
            LOGGER,
            logging.INFO,
            "speech_session_ready",
            "OCI Speech Realtime session is ready",
            session_id=self.session_id,
        )
        self.event_loop.call_soon_threadsafe(self.ready.set)

    def on_error(self, error: Exception) -> None:
        details = safe_error_details(error, "speech")
        log_event(
            LOGGER,
            logging.ERROR,
            "speech_error",
            "OCI Speech Realtime reported an error",
            session_id=self.session_id,
            stage="speech",
            status=details.get("status"),
            code=details.get("code"),
            opc_request_id=details.get("opc_request_id"),
            error_type=type(error).__name__,
            error_message=details.get("message"),
        )

        def record_error() -> None:
            self.last_speech_error = details
            self.failed.set()
            self.event_sink(details)

        self.event_loop.call_soon_threadsafe(record_error)

    def on_result(self, result: dict[str, Any]) -> None:
        for item in result.get("transcriptions", []):
            self.event_loop.call_soon_threadsafe(
                self._handle_transcription,
                item,
            )

    def _handle_transcription(self, item: dict[str, Any]) -> None:
        transcript = item.get("transcription", "").strip()
        is_final = bool(item.get("isFinal"))

        if not transcript:
            return

        self.event_sink(
            {
                "type": "transcript",
                "text": transcript,
                "is_final": is_final,
            }
        )

        if not is_final:
            return

        self.pending_segments.append(transcript)

        if self.buffer_timer_task and not self.buffer_timer_task.done():
            self.buffer_timer_task.cancel()

        self.buffer_timer_task = asyncio.create_task(
            self.flush_after_short_pause()
        )

    async def flush_after_short_pause(self) -> None:
        try:
            await asyncio.sleep(self.translation_buffer_seconds)
            self.queue_pending_transcript()
        except asyncio.CancelledError:
            pass

    def queue_pending_transcript(self) -> None:
        english_text = " ".join(self.pending_segments).strip()
        self.pending_segments.clear()

        if not english_text:
            return

        if self.translation_queue.full():
            queued_passages: list[str] = []
            while True:
                try:
                    queued = self.translation_queue.get_nowait()
                    self.translation_queue.task_done()
                    if queued:
                        queued_passages.append(queued)
                except asyncio.QueueEmpty:
                    break
            queued_passages.append(english_text)
            english_text = " ".join(queued_passages)
            self._publish(
                {
                    "type": "queue_status",
                    "queue": "translation",
                    "state": "coalesced",
                    "message": (
                        "Translation processing fell behind; adjacent English "
                        "segments were grouped to preserve order."
                    ),
                }
            )

        self.translation_queue.put_nowait(english_text)

    async def translation_worker(self) -> None:
        while True:
            english_text = await self.translation_queue.get()

            try:
                if english_text is None:
                    return

                result = await self.translation_service.translate(
                    english_text,
                    document_key="live-speech",
                )

                if result.get("french") is not None:
                    self._publish(
                        {
                            "type": "translation",
                            "english": english_text,
                            "french": result["french"],
                            "request_number": result.get("request_number"),
                            "latency_ms": result.get("latency_ms"),
                            "status": result.get("status"),
                            "code": result.get("code"),
                            "opc_request_id": result.get("opc_request_id"),
                        }
                    )
                else:
                    result["english"] = english_text
                    self._publish(result)
            except Exception as error:
                event = safe_error_details(error, "translation")
                event["english"] = english_text
                log_event(
                    LOGGER,
                    logging.ERROR,
                    "translation_worker_failed",
                    "Translation worker failed unexpectedly",
                    session_id=self.session_id,
                    stage="translation",
                    error_type=type(error).__name__,
                    error_message=event.get("message"),
                )
                self._publish(event)
            finally:
                self.translation_queue.task_done()

    async def finish(self) -> None:
        if self.buffer_timer_task and not self.buffer_timer_task.done():
            self.buffer_timer_task.cancel()
            await asyncio.gather(
                self.buffer_timer_task,
                return_exceptions=True,
            )

        self.queue_pending_transcript()
        await self.translation_queue.join()

        if self.translation_worker_task:
            await self.translation_queue.put(None)
            await self.translation_worker_task

    def on_close(self, error_code: int, error_message: str) -> None:
        log_event(
            LOGGER,
            logging.INFO if error_code == 1000 else logging.ERROR,
            "speech_websocket_closed",
            "OCI Speech Realtime WebSocket closed",
            session_id=self.session_id,
            stage="speech",
            status=error_code,
            code="NORMAL_CLOSURE" if error_code == 1000 else "WEBSOCKET_CLOSED",
            error_message=error_message or None,
        )
        status_event = {
            "type": "session_status",
            "state": "speech_closed",
            "message": f"OCI Speech connection closed ({error_code}).",
        }
        self._publish(status_event)

        if error_code == 1000:
            return

        details = {
            "type": "error",
            "stage": "speech",
            "status": error_code,
            "code": "WEBSOCKET_CLOSED",
            "message": (
                f"OCI Speech connection closed ({error_code}): "
                f"{error_message or 'no reason returned'}"
            ),
        }

        def record_close_error() -> None:
            self.last_speech_error = details
            self.failed.set()
            self.event_sink(details)

        self.event_loop.call_soon_threadsafe(record_close_error)


class SpeechTranslationSession:
    """Own one browser client's OCI Speech and Language resources."""

    def __init__(
        self,
        settings: OciSpeechSettings,
        event_sink: EventSink,
        session_id: str | None = None,
    ) -> None:
        self.settings = settings
        self.event_sink = event_sink
        self.session_id = session_id
        self.listener: SpeechTranslationListener | None = None
        self.client: RealtimeSpeechClient | None = None
        self.connection_task: asyncio.Task[None] | None = None
        self._stop_lock = asyncio.Lock()
        self._stopped = False

    async def start(self) -> None:
        config = load_api_key_config(
            self.settings.config_file,
            self.settings.profile_name,
            self.settings.region,
        )
        speech_signer = create_api_key_signer(config)
        language_signer = create_api_key_signer(config)
        translation_service = TranslationService.from_config(
            dict(config),
            language_signer,
            self.settings.compartment_id,
            self.session_id,
        )

        log_event(
            LOGGER,
            logging.INFO,
            "oci_clients_created",
            "Created independent Speech and Language clients",
            session_id=self.session_id,
            region=self.settings.region,
            profile=self.settings.profile_name,
            authentication="api_key",
        )

        self.listener = SpeechTranslationListener(
            translation_service=translation_service,
            event_sink=self.event_sink,
            event_loop=asyncio.get_running_loop(),
            translation_buffer_seconds=(
                self.settings.translation_buffer_seconds
            ),
            translation_queue_max_items=(
                self.settings.translation_queue_max_items
            ),
            session_id=self.session_id,
        )
        self.listener.start()

        parameters = RealtimeParameters()
        parameters.model_type = "WHISPER"
        parameters.language_code = "en"
        parameters.model_domain = RealtimeParameters.MODEL_DOMAIN_GENERIC
        parameters.punctuation = RealtimeParameters.PUNCTUATION_AUTO
        parameters.encoding = "audio/raw;rate=16000"
        parameters.is_ack_enabled = False

        self.client = create_api_key_realtime_client(
            settings=self.settings,
            config=config,
            signer=speech_signer,
            parameters=parameters,
            listener=self.listener,
        )

        self.connection_task = asyncio.create_task(self.client.connect())
        ready_task = asyncio.create_task(self.listener.ready.wait())
        failed_task = asyncio.create_task(self.listener.failed.wait())

        try:
            done, _ = await asyncio.wait(
                {ready_task, failed_task, self.connection_task},
                timeout=self.settings.speech_ready_timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )

            if ready_task in done and self.listener.ready.is_set():
                log_event(
                    LOGGER,
                    logging.INFO,
                    "live_session_ready",
                    "Live OCI Speech and Language session is ready",
                    session_id=self.session_id,
                    region=self.settings.region,
                )
                self.event_sink(
                    {
                        "type": "session_ready",
                        "sample_rate": 16000,
                        "encoding": "pcm_s16le",
                    }
                )
                return

            if failed_task in done and self.listener.last_speech_error:
                raise RuntimeError(
                    self.listener.last_speech_error["message"]
                )

            if self.connection_task in done:
                exception = self.connection_task.exception()
                if exception:
                    raise exception
                raise RuntimeError(
                    "OCI Speech connection ended before it became ready."
                )

            raise TimeoutError(
                "Timed out while waiting for the OCI Speech session."
            )
        finally:
            for task in (ready_task, failed_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(
                ready_task,
                failed_task,
                return_exceptions=True,
            )

    async def send_audio(self, audio_chunk: bytes) -> None:
        if not self.client:
            raise RuntimeError("The OCI Speech session isn't ready.")
        await self.client.send_data(audio_chunk)

    async def stop(self, request_final_result: bool = True) -> None:
        async with self._stop_lock:
            if self._stopped:
                return
            self._stopped = True

            if self.client:
                if (
                    request_final_result
                    and self.listener
                    and self.listener.ready.is_set()
                ):
                    try:
                        await self.client.request_final_result()
                        await asyncio.sleep(
                            self.settings.final_result_wait_seconds
                        )
                    except Exception as error:
                        self.event_sink(
                            safe_error_details(error, "speech_finalize")
                        )

                self.client.close()

            if self.connection_task:
                await asyncio.gather(
                    self.connection_task,
                    return_exceptions=True,
                )

            if self.listener:
                await self.listener.finish()

            log_event(
                LOGGER,
                logging.INFO,
                "live_session_stopped",
                "Live OCI Speech and Language session stopped",
                session_id=self.session_id,
                final_result_requested=request_final_result,
            )
