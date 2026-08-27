"""Tests for the API-key-only OCI client configuration."""

from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import AsyncMock, Mock, patch

import oci

from oci_language_404_repro import (
    create_language_client as create_diagnostic_language_client,
)
from oci_speech_service import (
    OciSpeechSettings,
    SpeechTranslationListener,
    SpeechTranslationSession,
    create_api_key_language_client,
    create_api_key_realtime_client,
    create_api_key_signer,
    load_api_key_config,
)
from translation_service import TranslationService


API_CONFIG = {
    "tenancy": "tenancy-ocid",
    "user": "user-ocid",
    "fingerprint": "fingerprint",
    "key_file": "private-key.pem",
    "region": "us-ashburn-1",
}


class OciApiKeyAuthenticationTest(unittest.TestCase):
    def test_api_key_profile_is_loaded_and_region_is_overridden(self) -> None:
        with (
            patch(
                "oci_speech_service.oci.config.from_file",
                return_value=dict(API_CONFIG),
            ),
            patch("oci_speech_service.oci.config.validate_config") as validate,
        ):
            config = load_api_key_config(
                "config-file",
                "API-USER",
                "us-phoenix-1",
            )

        self.assertEqual("us-phoenix-1", config["region"])
        validate.assert_called_once_with(config)

    def test_session_token_profile_is_rejected(self) -> None:
        session_config = dict(API_CONFIG)
        session_config["security_token_file"] = "temporary-token"

        with patch(
            "oci_speech_service.oci.config.from_file",
            return_value=session_config,
        ):
            with self.assertRaisesRegex(ValueError, "not an API-key profile"):
                load_api_key_config("config-file", "TEMPORARY-SESSION")

    def test_explicit_signer_uses_api_key_fields(self) -> None:
        signer = Mock(name="signer")
        with (
            patch(
                "oci_speech_service.oci.config.get_config_value_or_default",
                return_value=None,
            ),
            patch(
                "oci_speech_service.oci.signer.Signer",
                return_value=signer,
            ) as signer_type,
        ):
            actual = create_api_key_signer(dict(API_CONFIG))

        self.assertIs(signer, actual)
        signer_type.assert_called_once_with(
            tenancy="tenancy-ocid",
            user="user-ocid",
            fingerprint="fingerprint",
            private_key_file_location="private-key.pem",
            pass_phrase=None,
            private_key_content=None,
        )

    def test_language_client_receives_the_explicit_signer(self) -> None:
        settings = OciSpeechSettings(
            config_file="config-file",
            profile_name="API-USER",
            compartment_id="compartment-ocid",
            region="us-phoenix-1",
        )
        signer = Mock(name="signer")
        language_client = Mock(name="language-client")

        with (
            patch(
                "oci_speech_service.load_api_key_config",
                return_value=dict(API_CONFIG),
            ),
            patch(
                "oci_speech_service.create_api_key_signer",
                return_value=signer,
            ),
            patch(
                "oci_speech_service.AIServiceLanguageClient",
                return_value=language_client,
            ) as client_type,
        ):
            actual = create_api_key_language_client(settings)

        self.assertIs(language_client, actual)
        client_type.assert_called_once_with(
            config=API_CONFIG,
            signer=signer,
        )

    def test_realtime_client_receives_the_explicit_signer(self) -> None:
        settings = OciSpeechSettings(
            config_file="config-file",
            profile_name="API-USER",
            compartment_id="compartment-ocid",
            region="us-phoenix-1",
        )
        signer = Mock(name="signer")
        parameters = Mock(name="parameters")
        listener = Mock(name="listener")
        realtime_client = Mock(name="realtime-client")

        with patch(
            "oci_speech_service.RealtimeSpeechClient",
            return_value=realtime_client,
        ) as client_type:
            actual = create_api_key_realtime_client(
                settings=settings,
                config=API_CONFIG,
                signer=signer,
                parameters=parameters,
                listener=listener,
            )

        self.assertIs(realtime_client, actual)
        client_type.assert_called_once_with(
            config=API_CONFIG,
            realtime_speech_parameters=parameters,
            listener=listener,
            service_endpoint=(
                "wss://realtime.aiservice.us-phoenix-1.oci.oraclecloud.com"
            ),
            signer=signer,
            compartment_id="compartment-ocid",
        )

    def test_environment_defaults_to_default_api_key_profile(self) -> None:
        environment = {
            "HOME": "C:\\Users\\test-user",
            "USERPROFILE": "C:\\Users\\test-user",
            "OCI_COMPARTMENT_ID": "compartment-ocid",
        }
        with (
            patch.dict(os.environ, environment, clear=True),
            patch(
                "oci_speech_service.load_api_key_config",
                return_value=dict(API_CONFIG),
            ),
        ):
            settings = OciSpeechSettings.from_environment()

        self.assertEqual("DEFAULT", settings.profile_name)
        self.assertEqual("us-ashburn-1", settings.region)

    def test_realtime_auth_close_reason_is_preserved(self) -> None:
        event_loop = asyncio.new_event_loop()
        events: list[dict[str, object]] = []
        listener = SpeechTranslationListener(
            translation_service=Mock(spec=TranslationService),
            event_sink=events.append,
            event_loop=event_loop,
            translation_buffer_seconds=1.5,
        )
        try:
            listener.on_close(
                1008,
                "AUTHENTICATION_FAILURE: Could not authenticate",
            )
            event_loop.run_until_complete(asyncio.sleep(0))
        finally:
            event_loop.close()

        self.assertTrue(listener.failed.is_set())
        self.assertEqual(1008, listener.last_speech_error["status"])
        self.assertIn(
            "AUTHENTICATION_FAILURE",
            listener.last_speech_error["message"],
        )

    def test_live_session_uses_distinct_speech_and_language_signers(self) -> None:
        settings = OciSpeechSettings(
            config_file="config-file",
            profile_name="API-USER",
            compartment_id="compartment-ocid",
            region="us-phoenix-1",
        )
        speech_signer = Mock(name="speech-signer")
        language_signer = Mock(name="language-signer")
        language_client = Mock(name="language-client")
        realtime_client = Mock(name="realtime-client")
        realtime_client.close = Mock()

        async def exercise_session() -> None:
            captured_listener: SpeechTranslationListener | None = None

            def create_realtime_client(**kwargs: object) -> Mock:
                nonlocal captured_listener
                captured_listener = kwargs["listener"]  # type: ignore[assignment]

                async def connect() -> None:
                    assert captured_listener is not None
                    captured_listener.on_connect_message(None)

                realtime_client.connect = AsyncMock(side_effect=connect)
                return realtime_client

            with (
                patch(
                    "oci_speech_service.load_api_key_config",
                    return_value=dict(API_CONFIG),
                ),
                patch(
                    "oci_speech_service.create_api_key_signer",
                    side_effect=[speech_signer, language_signer],
                ) as signer_factory,
                patch(
                    "translation_service.AIServiceLanguageClient",
                    return_value=language_client,
                ) as language_client_type,
                patch(
                    "oci_speech_service.create_api_key_realtime_client",
                    side_effect=create_realtime_client,
                ) as realtime_client_factory,
            ):
                session = SpeechTranslationSession(
                    settings,
                    Mock(),
                    session_id="session-123",
                )
                await session.start()
                await session.stop(request_final_result=False)

            self.assertEqual(2, signer_factory.call_count)
            language_client_type.assert_called_once_with(
                config=API_CONFIG,
                signer=language_signer,
            )
            self.assertIs(
                speech_signer,
                realtime_client_factory.call_args.kwargs["signer"],
            )
            self.assertIsNot(speech_signer, language_signer)

        asyncio.run(exercise_session())

    def test_translation_service_returns_existing_browser_event_fields(
        self,
    ) -> None:
        response = Mock(status=200, headers={"opc-request-id": "opc-123"})
        response.data.documents = [Mock(translated_text="Bonjour.")]
        language_client = Mock()
        language_client.batch_language_translation.return_value = response
        service = TranslationService(
            language_client,
            "compartment-ocid",
            "session-123",
        )

        result = asyncio.run(
            service.translate("Hello.", document_key="live-speech")
        )

        self.assertEqual("Bonjour.", result["french"])
        self.assertEqual(200, result["status"])
        self.assertEqual("OK", result["code"])
        self.assertEqual("opc-123", result["opc_request_id"])
        self.assertEqual(1, result["request_number"])
        self.assertIn("latency_ms", result)

    def test_translation_service_preserves_oci_failure_diagnostics(self) -> None:
        language_client = Mock()
        language_client.batch_language_translation.side_effect = (
            oci.exceptions.ServiceError(
                401,
                "NotAuthenticated",
                {"opc-request-id": "opc-error-123"},
                "Authentication failed.",
            )
        )
        service = TranslationService(
            language_client,
            "compartment-ocid",
            "session-123",
        )

        result = asyncio.run(
            service.translate("Hello.", document_key="live-speech")
        )

        self.assertEqual("error", result["type"])
        self.assertEqual("translation", result["stage"])
        self.assertEqual(401, result["status"])
        self.assertEqual("NotAuthenticated", result["code"])
        self.assertEqual("opc-error-123", result["opc_request_id"])
        self.assertEqual(1, result["request_number"])
        self.assertIn("latency_ms", result)

    def test_diagnostic_uses_explicit_api_key_signer(self) -> None:
        settings = Mock(
            config_file="config-file",
            profile_name="API-USER",
            region="us-phoenix-1",
            tenancy_id="tenancy-ocid",
        )
        signer = Mock(name="signer")
        language_client = Mock(name="language-client")

        with (
            patch(
                "oci_language_404_repro.oci.config.from_file",
                return_value=dict(API_CONFIG),
            ),
            patch("oci_language_404_repro.oci.config.validate_config"),
            patch(
                "oci_language_404_repro.oci.config.get_config_value_or_default",
                return_value=None,
            ),
            patch(
                "oci_language_404_repro.oci.signer.Signer",
                return_value=signer,
            ) as signer_type,
            patch(
                "oci_language_404_repro.AIServiceLanguageClient",
                return_value=language_client,
            ) as client_type,
        ):
            actual_client, tenancy_matches = create_diagnostic_language_client(
                settings
            )

        self.assertIs(language_client, actual_client)
        self.assertTrue(tenancy_matches)
        signer_type.assert_called_once_with(
            tenancy="tenancy-ocid",
            user="user-ocid",
            fingerprint="fingerprint",
            private_key_file_location="private-key.pem",
            pass_phrase=None,
            private_key_content=None,
        )
        client_type.assert_called_once_with(
            config={**API_CONFIG, "region": "us-phoenix-1"},
            signer=signer,
        )

    def test_diagnostic_rejects_session_token_profile(self) -> None:
        settings = Mock(
            config_file="config-file",
            profile_name="TEMPORARY-SESSION",
            region="us-phoenix-1",
            tenancy_id=None,
        )
        session_config = dict(API_CONFIG)
        session_config["security_token_file"] = "temporary-token"

        with patch(
            "oci_language_404_repro.oci.config.from_file",
            return_value=session_config,
        ):
            with self.assertRaisesRegex(ValueError, "not an API-key profile"):
                create_diagnostic_language_client(settings)


if __name__ == "__main__":
    unittest.main()
