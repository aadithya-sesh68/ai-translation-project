"""Tests for credential-safe structured server logs."""

from __future__ import annotations

import io
import json
import logging
import unittest

from structured_logging import JsonLogFormatter, log_event


class StructuredLoggingTest(unittest.TestCase):
    def test_log_event_is_emitted_as_one_json_object(self) -> None:
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        handler.setFormatter(JsonLogFormatter())
        logger = logging.getLogger("oratranslate-test-logger")
        logger.handlers = [handler]
        logger.propagate = False
        logger.setLevel(logging.INFO)

        log_event(
            logger,
            logging.INFO,
            "translation_request_succeeded",
            "OCI Language translation request succeeded",
            session_id="session-123",
            status=200,
            opc_request_id="opc-123",
            latency_ms=42,
        )

        payload = json.loads(output.getvalue())
        self.assertEqual("INFO", payload["level"])
        self.assertEqual(
            "translation_request_succeeded",
            payload["event"],
        )
        self.assertEqual("session-123", payload["session_id"])
        self.assertEqual("opc-123", payload["opc_request_id"])
        self.assertEqual(42, payload["latency_ms"])
        self.assertNotIn("private_key", payload)
        self.assertNotIn("authorization", payload)


if __name__ == "__main__":
    unittest.main()
