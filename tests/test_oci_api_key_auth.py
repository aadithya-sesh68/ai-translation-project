"""Tests for the API-key-only OCI client configuration."""

from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import Mock, patch

from oci_speech_service import (
    OciSpeechSettings,
    SpeechTranslationListener,
    create_api_key_language_client,
    create_api_key_realtime_client,
    create_api_key_signer,
    load_api_key_config,
)


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
                load_api_key_config("config-file", "SpeechRealtime")

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
            language_client=Mock(),
            compartment_id="compartment-ocid",
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


if __name__ == "__main__":
    unittest.main()
