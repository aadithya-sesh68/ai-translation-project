#!/usr/bin/env bash
set -euo pipefail

cd /home/adi/oratranslate

export SPEECH_WEB_HOST="${SPEECH_WEB_HOST:-${ORATRANSLATE_HOST:-127.0.0.1}}"
export SPEECH_WEB_PORT="${SPEECH_WEB_PORT:-${ORATRANSLATE_PORT:-8010}}"

exec /home/adi/oratranslate/.venv/bin/python speech_web_server.py
