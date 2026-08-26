#!/usr/bin/env bash
set -euo pipefail

umask 077

project_dir="/home/adi/oratranslate"
environment_file="/home/adi/.config/oratranslate.env"
python_bin="$project_dir/.venv/bin/python"
server_script="$project_dir/speech_web_server.py"
pid_file="$project_dir/oratranslate.pid"
stdout_log="$project_dir/oratranslate.stdout.log"
stderr_log="$project_dir/oratranslate.stderr.log"
expected_command="$python_bin $server_script"

cd "$project_dir"

if [[ -f "$pid_file" ]]; then
    running_pid="$(<"$pid_file")"
    if [[ ! "$running_pid" =~ ^[0-9]+$ ]]; then
        echo "Invalid PID file: $pid_file" >&2
        exit 1
    fi

    if kill -0 "$running_pid" 2>/dev/null; then
        actual_command="$(ps -p "$running_pid" -o args=)"
        if [[ "$actual_command" == "$expected_command" ]]; then
            echo "OraTranslate is already running with PID $running_pid."
            exit 0
        fi

        echo "Refusing to replace PID $running_pid because it is not OraTranslate." >&2
        exit 1
    fi

    rm -f "$pid_file"
fi

if [[ ! -r "$environment_file" ]]; then
    echo "Missing environment file: $environment_file" >&2
    exit 1
fi

if [[ ! -x "$python_bin" ]]; then
    echo "Missing Python environment: $python_bin" >&2
    exit 1
fi

set -a
source "$environment_file"
set +a

export PYTHONUNBUFFERED="${PYTHONUNBUFFERED:-1}"
export SPEECH_WEB_HOST="${SPEECH_WEB_HOST:-${ORATRANSLATE_HOST:-127.0.0.1}}"
export SPEECH_WEB_PORT="${SPEECH_WEB_PORT:-${ORATRANSLATE_PORT:-8010}}"
export ORATRANSLATE_BASE_PATH="${ORATRANSLATE_BASE_PATH:-/OraTranslate}"

health_url="http://127.0.0.1:${SPEECH_WEB_PORT}/health"

/usr/bin/nohup "$python_bin" "$server_script" \
    >>"$stdout_log" 2>>"$stderr_log" < /dev/null &
running_pid="$!"
printf '%s\n' "$running_pid" >"$pid_file"

for _ in {1..15}; do
    if ! kill -0 "$running_pid" 2>/dev/null; then
        echo "OraTranslate exited during startup. See $stderr_log." >&2
        rm -f "$pid_file"
        exit 1
    fi

    if /usr/bin/curl -fsS --max-time 2 "$health_url" >/dev/null 2>&1; then
        echo "OraTranslate started with PID $running_pid."
        exit 0
    fi

    sleep 1
done

echo "OraTranslate did not become healthy. See $stderr_log." >&2
kill -TERM "$running_pid" 2>/dev/null || true
rm -f "$pid_file"
exit 1
