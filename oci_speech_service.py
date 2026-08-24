"""Reusable OCI Speech Realtime and OCI Language session support."""

from __future__ import annotations

import asyncio
import os
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import oci
from oci.ai_language import AIServiceLanguageClient
from oci.ai_language.models import (
    BatchLanguageTranslationDetails,
    TextDocument,
)
from oci.ai_speech.models import RealtimeParameters
from oci.auth.signers.security_token_signer import SecurityTokenSigner
from oci_ai_speech_realtime import (
    RealtimeSpeechClient,
    RealtimeSpeechClientListener,
)


EventSink = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class OciSpeechSettings:
    """Non-secret runtime settings for an OCI speech and translation session."""

    config_file: str
    profile_name: str
    compartment_id: str
    region: str
    translation_buffer_seconds: float = 1.5
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
            "SpeechRealtime",
        )

        config = oci.config.from_file(
            file_location=config_file,
            profile_name=profile_name,
        )

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
        )


def create_security_token_signer(config: dict[str, str]) -> SecurityTokenSigner:
    """Load the configured token and key without logging either value."""

    with open(
        config["security_token_file"],
        "r",
        encoding="utf-8",
    ) as token_file:
        token = token_file.readline().strip()

    private_key = oci.signer.load_private_key_from_file(config["key_file"])

    return SecurityTokenSigner(
        token=token,
        private_key=private_key,
    )


def create_session_language_client(
    settings: OciSpeechSettings,
) -> AIServiceLanguageClient:
    """Create an OCI Language client with the session-token profile."""

    config = oci.config.from_file(
        file_location=settings.config_file,
        profile_name=settings.profile_name,
    )
    config["region"] = settings.region
    signer = create_security_token_signer(config)
    return AIServiceLanguageClient(config=config, signer=signer)


def safe_error_details(error: Exception, stage: str) -> dict[str, Any]:
    """Return useful OCI diagnostics without credentials or request headers."""

    details: dict[str, Any] = {
        "type": "error",
        "stage": stage,
        "message": str(error),
    }

    if isinstance(error, oci.exceptions.ServiceError):
        details.update(
            {
                "status": error.status,
                "code": error.code,
                "message": error.message,
                "opc_request_id": error.request_id,
            }
        )

    return {key: value for key, value in details.items() if value is not None}


async def run_translation_reliability_test(
    settings: OciSpeechSettings,
    sentences: list[str],
    concurrency: int,
    delay_ms: int,
    event_sink: EventSink,
) -> None:
    """Translate each sentence once and stream request-level diagnostics."""

    language_client = create_session_language_client(settings)
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
            "total": len(sentences),
            "concurrency": concurrency,
            "delay_ms": delay_ms,
        }
    )

    async def translate_one(index: int, english_text: str) -> None:
        request_started = time.perf_counter()
        try:
            details = BatchLanguageTranslationDetails(
                compartment_id=settings.compartment_id,
                target_language_code="fr",
                documents=[
                    TextDocument(
                        key=f"reliability-test-{index}",
                        text=english_text,
                        language_code="en",
                    )
                ],
            )
            response = await asyncio.to_thread(
                language_client.batch_language_translation,
                details,
                retry_strategy=oci.retry.NoneRetryStrategy(),
            )
            latency_ms = round(
                (time.perf_counter() - request_started) * 1000
            )
            result_codes["success"] += 1
            event_sink(
                {
                    "type": "translation_test_result",
                    "index": index,
                    "english": english_text,
                    "french": response.data.documents[0].translated_text,
                    "latency_ms": latency_ms,
                    "status": getattr(response, "status", 200),
                    "code": "OK",
                    "opc_request_id": response.headers.get(
                        "opc-request-id"
                    ),
                }
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            latency_ms = round(
                (time.perf_counter() - request_started) * 1000
            )
            event = safe_error_details(error, "translation_test")
            event.update(
                {
                    "type": "translation_test_result",
                    "index": index,
                    "english": english_text,
                    "latency_ms": latency_ms,
                }
            )
            headers = getattr(error, "headers", None) or {}
            retry_after = headers.get("retry-after")
            if retry_after:
                event["retry_after"] = retry_after

            result_key = str(event.get("status") or event.get("code") or "error")
            result_codes[result_key] += 1
            event_sink(event)

    async def worker() -> None:
        while True:
            item = await work_queue.get()
            try:
                if item is None:
                    return
                await translate_one(*item)
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
            "total": len(sentences),
            "counts": dict(result_codes),
            "elapsed_ms": round(
                (time.perf_counter() - test_started) * 1000
            ),
        }
    )


class SpeechTranslationListener(RealtimeSpeechClientListener):
    """Receive Speech results and translate buffered final English segments."""

    def __init__(
        self,
        language_client: AIServiceLanguageClient,
        compartment_id: str,
        event_sink: EventSink,
        event_loop: asyncio.AbstractEventLoop,
        translation_buffer_seconds: float,
    ) -> None:
        self.language_client = language_client
        self.compartment_id = compartment_id
        self.event_sink = event_sink
        self.event_loop = event_loop
        self.translation_buffer_seconds = translation_buffer_seconds

        self.pending_segments: list[str] = []
        self.translation_queue: asyncio.Queue[str | None] = asyncio.Queue()
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
        self._publish(
            {
                "type": "session_status",
                "state": "connected",
                "message": "Connected to OCI Speech Realtime.",
            }
        )

    def on_connect_message(self, message: Any) -> None:
        self.event_loop.call_soon_threadsafe(self.ready.set)

    def on_error(self, error: Exception) -> None:
        details = safe_error_details(error, "speech")

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

        if english_text:
            self.translation_queue.put_nowait(english_text)

    async def translation_worker(self) -> None:
        while True:
            english_text = await self.translation_queue.get()

            try:
                if english_text is None:
                    return

                details = BatchLanguageTranslationDetails(
                    compartment_id=self.compartment_id,
                    target_language_code="fr",
                    documents=[
                        TextDocument(
                            key="live-speech",
                            text=english_text,
                            language_code="en",
                        )
                    ],
                )

                response = await asyncio.to_thread(
                    self.language_client.batch_language_translation,
                    details,
                    # Authorization failures aren't transient. Make one request
                    # and report OCI's diagnostic and OPC request ID as-is.
                    retry_strategy=oci.retry.NoneRetryStrategy(),
                )

                french_text = response.data.documents[0].translated_text
                self._publish(
                    {
                        "type": "translation",
                        "english": english_text,
                        "french": french_text,
                        "opc_request_id": response.headers.get(
                            "opc-request-id"
                        ),
                    }
                )
            except Exception as error:
                event = safe_error_details(error, "translation")
                event["english"] = english_text
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
        self._publish(
            {
                "type": "session_status",
                "state": "speech_closed",
                "message": f"OCI Speech connection closed ({error_code}).",
            }
        )


class SpeechTranslationSession:
    """Own one browser client's OCI Speech and Language resources."""

    def __init__(
        self,
        settings: OciSpeechSettings,
        event_sink: EventSink,
    ) -> None:
        self.settings = settings
        self.event_sink = event_sink
        self.listener: SpeechTranslationListener | None = None
        self.client: RealtimeSpeechClient | None = None
        self.connection_task: asyncio.Task[None] | None = None
        self._stop_lock = asyncio.Lock()
        self._stopped = False

    async def start(self) -> None:
        config = oci.config.from_file(
            file_location=self.settings.config_file,
            profile_name=self.settings.profile_name,
        )
        config["region"] = self.settings.region
        signer = create_security_token_signer(config)

        language_client = AIServiceLanguageClient(
            config=config,
            signer=signer,
        )

        self.listener = SpeechTranslationListener(
            language_client=language_client,
            compartment_id=self.settings.compartment_id,
            event_sink=self.event_sink,
            event_loop=asyncio.get_running_loop(),
            translation_buffer_seconds=(
                self.settings.translation_buffer_seconds
            ),
        )
        self.listener.start()

        parameters = RealtimeParameters()
        parameters.model_type = "WHISPER"
        parameters.language_code = "en"
        parameters.model_domain = RealtimeParameters.MODEL_DOMAIN_GENERIC
        parameters.punctuation = RealtimeParameters.PUNCTUATION_AUTO
        parameters.encoding = "audio/raw;rate=16000"
        parameters.is_ack_enabled = False

        self.client = RealtimeSpeechClient(
            config=config,
            realtime_speech_parameters=parameters,
            listener=self.listener,
            service_endpoint=(
                f"wss://realtime.aiservice.{self.settings.region}."
                "oci.oraclecloud.com"
            ),
            signer=signer,
            compartment_id=self.settings.compartment_id,
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
