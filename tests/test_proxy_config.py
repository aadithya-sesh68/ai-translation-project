"""Tests for reverse-proxy browser origin configuration."""

from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

from speech_web_server import allowed_websocket_origins


class ProxyConfigurationTest(unittest.TestCase):
    def test_local_and_public_origins_are_allowed(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "SPEECH_WEB_ALLOWED_ORIGINS": (
                    "http://localhost:8080,https://speech.example.com/"
                )
            },
        ):
            origins = allowed_websocket_origins(8765)

        self.assertEqual(
            [
                "http://localhost:8765",
                "http://127.0.0.1:8765",
                "http://localhost:8080",
                "https://speech.example.com",
            ],
            origins,
        )

    def test_origin_with_path_is_rejected(self) -> None:
        with patch.dict(
            "os.environ",
            {"SPEECH_WEB_ALLOWED_ORIGINS": "https://speech.example.com/app"},
        ):
            with self.assertRaises(ValueError):
                allowed_websocket_origins(8765)

    def test_venus_proxy_preserves_long_websocket_sessions(self) -> None:
        config_path = (
            Path(__file__).parents[1]
            / "deploy"
            / "venus"
            / "oratranslate.nginx.conf"
        )
        config = config_path.read_text(encoding="utf-8")

        self.assertIn("proxy_pass http://127.0.0.1:8010/;", config)
        self.assertIn("proxy_set_header Upgrade $http_upgrade;", config)
        self.assertIn('proxy_set_header Connection "upgrade";', config)
        self.assertIn("proxy_read_timeout 24h;", config)
        self.assertIn("proxy_send_timeout 24h;", config)


if __name__ == "__main__":
    unittest.main()
