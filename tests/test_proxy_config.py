"""Tests for reverse-proxy browser origin configuration."""

from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

from speech_web_server import allowed_websocket_origins, static_file_for_path


class ProxyConfigurationTest(unittest.TestCase):
    def test_redwood_assets_are_resolved_inside_the_web_root(self) -> None:
        asset = static_file_for_path("/assets/images/oracle-o.svg")

        self.assertIsNotNone(asset)
        self.assertTrue(asset.is_file())
        self.assertEqual("oracle-o.svg", asset.name)

    def test_redwood_asset_path_cannot_traverse_outside_assets(self) -> None:
        self.assertIsNone(static_file_for_path("/assets/../../README.md"))

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

    def test_venus_user_scripts_launch_and_guard_the_live_server(self) -> None:
        deployment_dir = (
            Path(__file__).parents[1] / "deploy" / "venus"
        )
        start_script = (
            deployment_dir / "start-oratranslate.sh"
        ).read_text(encoding="utf-8")
        stop_script = (
            deployment_dir / "stop-oratranslate.sh"
        ).read_text(encoding="utf-8")

        expected_command = (
            'expected_command="$python_bin $server_script"'
        )
        self.assertIn(expected_command, start_script)
        self.assertIn(expected_command, stop_script)
        self.assertIn(
            'server_script="$project_dir/speech_web_server.py"',
            start_script,
        )
        self.assertIn(
            '/usr/bin/nohup "$python_bin" "$server_script"',
            start_script,
        )
        self.assertIn('source "$environment_file"', start_script)
        self.assertIn('kill -TERM "$running_pid"', stop_script)
        self.assertNotIn("uv run main.py", start_script)
        self.assertNotIn("uv run main.py", stop_script)


if __name__ == "__main__":
    unittest.main()
