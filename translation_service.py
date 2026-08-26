"""Dedicated OCI Language translation service for OraTranslate."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import oci
from oci.ai_language import AIServiceLanguageClient
from oci.ai_language.models import (
    BatchLanguageTranslationDetails,
    TextDocument,
)

from structured_logging import log_event


LOGGER = logging.getLogger("translation_service")


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


class TranslationService:
    """Own an OCI Language client and translate English text to French."""

    def __init__(
        self,
        language_client: AIServiceLanguageClient,
        compartment_id: str,
        session_id: str | None = None,
    ) -> None:
        self.language_client = language_client
        self.compartment_id = compartment_id
        self.session_id = session_id
        self.request_count = 0

    @classmethod
    def from_config(
        cls,
        config: dict[str, str],
        signer: oci.signer.Signer,
        compartment_id: str,
        session_id: str | None = None,
    ) -> "TranslationService":
        """Create a Language client with its dedicated API-key signer."""

        language_client = AIServiceLanguageClient(
            config=config,
            signer=signer,
        )
        return cls(language_client, compartment_id, session_id)

    async def translate(
        self,
        english_text: str,
        document_key: str,
        stage: str = "translation",
    ) -> dict[str, Any]:
        """Perform one non-retried translation and return a safe result."""

        self.request_count += 1
        request_number = self.request_count
        request_started = time.perf_counter()
        log_fields = {
            "session_id": self.session_id,
            "stage": stage,
            "request_number": request_number,
            "text_characters": len(english_text),
        }

        log_event(
            LOGGER,
            logging.INFO,
            "translation_request_started",
            "OCI Language translation request started",
            **log_fields,
        )

        try:
            details = BatchLanguageTranslationDetails(
                compartment_id=self.compartment_id,
                target_language_code="fr",
                documents=[
                    TextDocument(
                        key=document_key,
                        text=english_text,
                        language_code="en",
                    )
                ],
            )
            response = await asyncio.to_thread(
                self.language_client.batch_language_translation,
                details,
                retry_strategy=oci.retry.NoneRetryStrategy(),
            )
            latency_ms = round(
                (time.perf_counter() - request_started) * 1000
            )
            status = getattr(response, "status", 200)
            opc_request_id = response.headers.get("opc-request-id")
            french_text = response.data.documents[0].translated_text

            log_event(
                LOGGER,
                logging.INFO,
                "translation_request_succeeded",
                "OCI Language translation request succeeded",
                **log_fields,
                status=status,
                code="OK",
                opc_request_id=opc_request_id,
                latency_ms=latency_ms,
            )
            return {
                "french": french_text,
                "latency_ms": latency_ms,
                "status": status,
                "code": "OK",
                "opc_request_id": opc_request_id,
            }
        except asyncio.CancelledError:
            raise
        except Exception as error:
            latency_ms = round(
                (time.perf_counter() - request_started) * 1000
            )
            result = safe_error_details(error, stage)
            result["latency_ms"] = latency_ms

            headers = getattr(error, "headers", None) or {}
            retry_after = headers.get("retry-after")
            if retry_after:
                result["retry_after"] = retry_after

            log_event(
                LOGGER,
                logging.ERROR,
                "translation_request_failed",
                "OCI Language translation request failed",
                **log_fields,
                status=result.get("status"),
                code=result.get("code"),
                opc_request_id=result.get("opc_request_id"),
                latency_ms=latency_ms,
                error_type=type(error).__name__,
                error_message=result.get("message"),
                retry_after=retry_after,
            )
            return result
