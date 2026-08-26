#!/usr/bin/env bash
set -euo pipefail

project_dir="/home/adi/oratranslate"
python_bin="$project_dir/.venv/bin/python"
server_script="$project_dir/speech_web_server.py"
pid_file="$project_dir/oratranslate.pid"
expected_command="$python_bin $server_script"

if [[ ! -f "$pid_file" ]]; then
    echo "OraTranslate is already stopped."
    exit 0
fi

running_pid="$(<"$pid_file")"
if [[ ! "$running_pid" =~ ^[0-9]+$ ]]; then
    echo "Invalid PID file: $pid_file" >&2
    exit 1
fi

if ! kill -0 "$running_pid" 2>/dev/null; then
    rm -f "$pid_file"
    echo "OraTranslate is already stopped."
    exit 0
fi

actual_command="$(ps -p "$running_pid" -o args=)"
if [[ "$actual_command" != "$expected_command" ]]; then
    echo "Refusing to stop PID $running_pid because it is not OraTranslate." >&2
    exit 1
fi

kill -TERM "$running_pid"

for _ in {1..30}; do
    if ! kill -0 "$running_pid" 2>/dev/null; then
        rm -f "$pid_file"
        echo "OraTranslate stopped."
        exit 0
    fi

    sleep 1
done

echo "OraTranslate did not stop within 30 seconds." >&2
exit 1
