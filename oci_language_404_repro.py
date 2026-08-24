#!/usr/bin/env python3
"""Repeat OCI Language translations and capture intermittent 404 diagnostics.

This file is standalone apart from the OCI Python SDK. It deliberately disables
SDK retries so every report entry represents one BatchLanguageTranslation API
request. It never prints or writes private keys, security tokens, fingerprints,
or request headers.

Examples (PowerShell):

    $env:OCI_COMPARTMENT_ID = 'ocid1.compartment.oc1..replace_with_yours'
    python oci_language_404_repro.py --loops 10

Run until Ctrl+C:

    python oci_language_404_repro.py --loops 0

Each successful request may incur OCI Language usage. Start with sequential
requests (the default) so rate-limit responses do not obscure the 404 test.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import oci
from oci.ai_language import AIServiceLanguageClient
from oci.ai_language.models import (
    BatchLanguageTranslationDetails,
    TextDocument,
)
from oci.auth.signers.security_token_signer import SecurityTokenSigner


SENTENCES = [
    "Welcome to this session about Oracle Cloud Infrastructure.",
    "Today we will explore several practical cloud services.",
    "OCI provides computing, networking, storage, and database capabilities.",
    "The speaker will demonstrate a live translation proof of concept.",
    "A browser can capture microphone audio after the user grants permission.",
    "The local server sends raw audio to OCI Speech Realtime.",
    "Whisper converts the English speech into final transcript segments.",
    "OCI Language translates the buffered English text into French.",
    "Each request in this test contains exactly one English sentence.",
    "The application records the response time for every translation call.",
    "Successful responses include an OPC request identifier.",
    "Failed responses can also include an OPC request identifier for support.",
    "An authorization failure should not be retried without investigation.",
    "A rate limit normally produces an HTTP 429 response.",
    "The sequential test establishes a low-concurrency baseline.",
    "A second run can use two concurrent translation requests.",
    "A controlled burst can reveal whether throttling occurs.",
    "The test results can be downloaded as a JSON diagnostic report.",
    "MongoDB can be used alongside services in Oracle Cloud Infrastructure.",
    "Artificial intelligence is changing how applications process language.",
    "Clear punctuation helps the translation service understand each sentence.",
    "Technical product names should be spoken slowly and clearly.",
    "Headphones can reduce echo during a live transcription session.",
    "Background noise affects transcription but does not cause an OCI 404.",
    "The configured compartment must authorize the calling OCI identity.",
    "Service availability and IAM policies can vary between OCI regions.",
    "Request identifiers help Oracle Support trace failures in service logs.",
    "This sentence checks whether translation remains stable near the end.",
    "The twenty-ninth request provides another repeatability checkpoint.",
    "Thank you for completing the OCI Language reliability test.",
]


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp suitable for a diagnostic report."""

    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return parsed


@dataclass(frozen=True)
class TestSettings:
    config_file: str
    profile_name: str
    auth_mode: str
    region: str
    compartment_id: str
    tenancy_name: str | None
    tenancy_id: str | None
    loops: int
    delay_ms: int
    loop_delay_ms: int
    output_file: Path


def parse_arguments() -> TestSettings:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    default_output = f"oci-language-reliability-{timestamp}.json"

    parser = argparse.ArgumentParser(
        description=(
            "Repeatedly translate 30 English sentences with OCI Language and "
            "write request-level status, latency, and OPC request IDs to JSON."
        )
    )
    parser.add_argument(
        "--config-file",
        default=os.environ.get(
            "OCI_CONFIG_FILE", str(Path.home() / ".oci" / "config")
        ),
        help="OCI config file path (default: OCI_CONFIG_FILE or ~/.oci/config)",
    )
    parser.add_argument(
        "--profile",
        default=os.environ.get("OCI_CONFIG_PROFILE", "DEFAULT"),
        help="OCI config profile (default: OCI_CONFIG_PROFILE or DEFAULT)",
    )
    parser.add_argument(
        "--auth",
        choices=("auto", "security_token", "api_key"),
        default="auto",
        help="Authentication type; auto detects the profile type (default: auto)",
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("OCI_REGION", "us-phoenix-1"),
        help="OCI service region (default: OCI_REGION or us-phoenix-1)",
    )
    parser.add_argument(
        "--compartment-id",
        default=os.environ.get("OCI_COMPARTMENT_ID"),
        help="Compartment OCID (required; may use OCI_COMPARTMENT_ID)",
    )
    parser.add_argument(
        "--tenancy-name",
        default=os.environ.get("OCI_TENANCY_NAME"),
        help="Tenancy name to include in the report metadata",
    )
    parser.add_argument(
        "--tenancy-id",
        default=os.environ.get("OCI_TENANCY_ID"),
        help="Expected tenancy OCID to include and compare with the profile",
    )
    parser.add_argument(
        "--loops",
        type=non_negative_int,
        default=10,
        help="Number of 30-sentence loops; 0 runs until Ctrl+C (default: 10)",
    )
    parser.add_argument(
        "--delay-ms",
        type=non_negative_int,
        default=250,
        help="Pause after every API request in milliseconds (default: 250)",
    )
    parser.add_argument(
        "--loop-delay-ms",
        type=non_negative_int,
        default=1000,
        help="Pause between completed loops in milliseconds (default: 1000)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(default_output),
        help=f"JSON report path (default: {default_output})",
    )

    args = parser.parse_args()
    compartment_id = (args.compartment_id or "").strip()
    if not compartment_id:
        parser.error(
            "--compartment-id or the OCI_COMPARTMENT_ID environment variable "
            "is required"
        )

    return TestSettings(
        config_file=str(Path(args.config_file).expanduser()),
        profile_name=args.profile,
        auth_mode=args.auth,
        region=args.region,
        compartment_id=compartment_id,
        tenancy_name=(args.tenancy_name or "").strip() or None,
        tenancy_id=(args.tenancy_id or "").strip() or None,
        loops=args.loops,
        delay_ms=args.delay_ms,
        loop_delay_ms=args.loop_delay_ms,
        output_file=args.output.expanduser().resolve(),
    )


def create_language_client(
    settings: TestSettings,
) -> tuple[AIServiceLanguageClient, str, bool | None]:
    """Create a Language client without exposing authentication material."""

    config = oci.config.from_file(
        file_location=settings.config_file,
        profile_name=settings.profile_name,
    )
    config["region"] = settings.region

    profile_has_session_token = bool(config.get("security_token_file"))
    auth_mode = settings.auth_mode
    if auth_mode == "auto":
        auth_mode = "security_token" if profile_has_session_token else "api_key"

    if auth_mode == "security_token":
        if not profile_has_session_token:
            raise ValueError(
                "The selected profile does not contain security_token_file, "
                "so it cannot use security-token authentication."
            )
        token_path = config.get("security_token_file")
        key_path = config.get("key_file")
        if not token_path or not key_path:
            raise ValueError(
                "The session-token profile must contain security_token_file "
                "and key_file paths."
            )
        with open(token_path, "r", encoding="utf-8") as token_file:
            token = token_file.readline().strip()
        private_key = oci.signer.load_private_key_from_file(key_path)
        signer = SecurityTokenSigner(token=token, private_key=private_key)
        client = AIServiceLanguageClient(config=config, signer=signer)
    else:
        if profile_has_session_token:
            raise ValueError(
                "The selected profile is a session-token profile. Choose an "
                "API-key profile or use --auth security_token."
            )
        client = AIServiceLanguageClient(config=config)

    profile_tenancy = config.get("tenancy")
    tenancy_matches: bool | None = None
    if settings.tenancy_id and profile_tenancy:
        tenancy_matches = settings.tenancy_id == profile_tenancy

    return client, auth_mode, tenancy_matches


def safe_service_error(error: Exception) -> dict[str, Any]:
    """Extract support-safe error fields without dumping request headers."""

    if isinstance(error, oci.exceptions.ServiceError):
        headers = getattr(error, "headers", None) or {}
        return {
            "status": error.status,
            "code": error.code,
            "message": error.message,
            "opc_request_id": error.request_id
            or headers.get("opc-request-id"),
            "retry_after": headers.get("retry-after"),
        }

    return {
        "status": 0,
        "code": type(error).__name__,
        "message": str(error),
        "opc_request_id": None,
        "retry_after": None,
    }


def translate_once(
    client: AIServiceLanguageClient,
    settings: TestSettings,
    loop_number: int,
    sentence_index: int,
    request_number: int,
    english: str,
) -> dict[str, Any]:
    request_started_at = utc_now()
    timer_started = time.perf_counter()
    document_key = f"loop-{loop_number}-sentence-{sentence_index}"

    try:
        details = BatchLanguageTranslationDetails(
            compartment_id=settings.compartment_id,
            target_language_code="fr",
            documents=[
                TextDocument(
                    key=document_key,
                    text=english,
                    language_code="en",
                )
            ],
        )
        response = client.batch_language_translation(
            details,
            retry_strategy=oci.retry.NoneRetryStrategy(),
        )
        latency_ms = round((time.perf_counter() - timer_started) * 1000)
        headers = getattr(response, "headers", None) or {}
        documents = getattr(response.data, "documents", None) or []
        translated_text = (
            getattr(documents[0], "translated_text", None) if documents else None
        )
        return {
            "request_number": request_number,
            "loop": loop_number,
            "sentence_index": sentence_index,
            "started_at": request_started_at,
            "finished_at": utc_now(),
            "english": english,
            "french": translated_text,
            "status": getattr(response, "status", 200),
            "code": "OK",
            "message": None,
            "latency_ms": latency_ms,
            "opc_request_id": headers.get("opc-request-id"),
            "retry_after": headers.get("retry-after"),
        }
    except Exception as error:  # Capture every request outcome in the report.
        latency_ms = round((time.perf_counter() - timer_started) * 1000)
        details = safe_service_error(error)
        return {
            "request_number": request_number,
            "loop": loop_number,
            "sentence_index": sentence_index,
            "started_at": request_started_at,
            "finished_at": utc_now(),
            "english": english,
            "french": None,
            "status": details["status"],
            "code": details["code"],
            "message": details["message"],
            "latency_ms": latency_ms,
            "opc_request_id": details["opc_request_id"],
            "retry_after": details["retry_after"],
        }


def summarize(
    results: list[dict[str, Any]], completed_loops: int, interrupted: bool
) -> dict[str, Any]:
    status_counts = Counter(str(result["status"]) for result in results)
    code_counts = Counter(str(result["code"]) for result in results)
    error_404_results = [result for result in results if result["status"] == 404]
    error_429_results = [result for result in results if result["status"] == 429]
    successful_results = [
        result for result in results if 200 <= result["status"] < 300
    ]
    latencies = [result["latency_ms"] for result in results]

    return {
        "total_requests": len(results),
        "completed_loops": completed_loops,
        "successful_requests": len(successful_results),
        "error_requests": len(results) - len(successful_results),
        "status_404_count": len(error_404_results),
        "status_429_count": len(error_429_results),
        "status_counts": dict(sorted(status_counts.items())),
        "code_counts": dict(sorted(code_counts.items())),
        "average_latency_ms": (
            round(sum(latencies) / len(latencies)) if latencies else None
        ),
        "maximum_latency_ms": max(latencies) if latencies else None,
        "first_404_request_number": (
            error_404_results[0]["request_number"] if error_404_results else None
        ),
        "opc_request_ids_404": [
            result["opc_request_id"]
            for result in error_404_results
            if result["opc_request_id"]
        ],
        "interrupted": interrupted,
    }


def write_report(path: Path, report: dict[str, Any]) -> None:
    """Atomically preserve progress so Ctrl+C does not lose earlier results."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(path)


def print_result(result: dict[str, Any]) -> None:
    label = (
        f"request={result['request_number']} "
        f"loop={result['loop']} sentence={result['sentence_index']} "
        f"status={result['status']} code={result['code']} "
        f"latency={result['latency_ms']}ms"
    )
    print(label, flush=True)
    if result["status"] == 404:
        print(
            "  404 OPC request ID: "
            f"{result['opc_request_id'] or 'not returned'}",
            flush=True,
        )
    elif result["status"] == 429:
        print(
            f"  Rate limited; Retry-After: {result['retry_after'] or 'not returned'}",
            flush=True,
        )


def run(settings: TestSettings) -> int:
    client, resolved_auth_mode, tenancy_matches = create_language_client(settings)
    expected_requests = (
        None if settings.loops == 0 else settings.loops * len(SENTENCES)
    )
    report: dict[str, Any] = {
        "schema_version": 1,
        "test_name": "OCI Language repeated translation 404 diagnostic",
        "started_at": utc_now(),
        "finished_at": None,
        "service": "OCI Language",
        "operation": "BatchLanguageTranslation",
        "region": settings.region,
        "tenancy": {
            "name": settings.tenancy_name,
            "ocid": settings.tenancy_id,
            "matches_profile_tenancy": tenancy_matches,
        },
        "compartment_id": settings.compartment_id,
        "authentication": {
            "profile_name": settings.profile_name,
            "type": resolved_auth_mode,
        },
        "settings": {
            "sentence_count": len(SENTENCES),
            "requested_loops": settings.loops,
            "expected_requests": expected_requests,
            "delay_ms": settings.delay_ms,
            "loop_delay_ms": settings.loop_delay_ms,
            "concurrency": 1,
            "source_language": "en",
            "target_language": "fr",
            "sdk_retry_strategy": "none",
        },
        "summary": summarize([], 0, False),
        "results": [],
    }

    print("OCI Language repeated translation diagnostic")
    print(f"Region: {settings.region}")
    print(f"Profile: {settings.profile_name} ({resolved_auth_mode})")
    print(f"Sentences per loop: {len(SENTENCES)}")
    print(
        "Loops: until Ctrl+C" if settings.loops == 0 else f"Loops: {settings.loops}"
    )
    print(f"Report: {settings.output_file}")
    print("SDK retries: disabled")
    if tenancy_matches is False:
        print(
            "WARNING: --tenancy-id does not match the tenancy in the selected profile.",
            file=sys.stderr,
        )

    completed_loops = 0
    request_number = 0
    interrupted = False
    write_report(settings.output_file, report)

    try:
        loop_number = 0
        while settings.loops == 0 or loop_number < settings.loops:
            loop_number += 1
            print(f"\nStarting loop {loop_number}", flush=True)
            for sentence_index, sentence in enumerate(SENTENCES, start=1):
                request_number += 1
                result = translate_once(
                    client=client,
                    settings=settings,
                    loop_number=loop_number,
                    sentence_index=sentence_index,
                    request_number=request_number,
                    english=sentence,
                )
                report["results"].append(result)
                report["summary"] = summarize(
                    report["results"], completed_loops, False
                )
                write_report(settings.output_file, report)
                print_result(result)

                if settings.delay_ms:
                    time.sleep(settings.delay_ms / 1000)

            completed_loops = loop_number
            report["summary"] = summarize(
                report["results"], completed_loops, False
            )
            write_report(settings.output_file, report)
            print(
                f"Completed loop {loop_number}: "
                f"404={report['summary']['status_404_count']} "
                f"429={report['summary']['status_429_count']} "
                f"success={report['summary']['successful_requests']}",
                flush=True,
            )

            more_loops = settings.loops == 0 or loop_number < settings.loops
            if more_loops and settings.loop_delay_ms:
                time.sleep(settings.loop_delay_ms / 1000)
    except KeyboardInterrupt:
        interrupted = True
        print("\nStopped by user; preserving collected results.", flush=True)
    finally:
        report["finished_at"] = utc_now()
        report["summary"] = summarize(
            report["results"], completed_loops, interrupted
        )
        write_report(settings.output_file, report)

    summary = report["summary"]
    print("\nFinal summary")
    print(f"Total requests: {summary['total_requests']}")
    print(f"Successful: {summary['successful_requests']}")
    print(f"404 responses: {summary['status_404_count']}")
    print(f"429 responses: {summary['status_429_count']}")
    print(f"Report saved to: {settings.output_file}")
    return 0


def main() -> int:
    try:
        return run(parse_arguments())
    except (KeyError, OSError, ValueError, oci.exceptions.ConfigFileNotFound) as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
