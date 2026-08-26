# Venus deployment

OraTranslate remains bound to `127.0.0.1:8010`. Nginx publishes it at
`https://venus.aisandbox.ugbu.oraclepdemos.com/OraTranslate/` and removes the
`/OraTranslate` prefix before proxying requests to Python.

## Prerequisites

- A server-side OCI API-key profile readable only by `adi`. Generate its
  private key on Venus and provide only the public key to the OCI administrator.
- The OCI user must be authorized for Speech Realtime and Language in the
  configured compartment.
- A complete, browser-trusted TLS certificate chain on the public Nginx server.
- The existing Nginx route must preserve WebSocket upgrades and long-lived
  connections. Administrator help is needed only if that route must change.

Do not copy an OCI private key into source control, a deployment archive, the
environment file, terminal output, or chat.

## Stage without interrupting the placeholder

Upload the project into `/home/adi/oratranslate-next`, then run:

```bash
cd /home/adi/oratranslate-next
export UV_PYTHON_INSTALL_DIR="$HOME/.local/share/uv/python"
/usr/local/bin/uv python install 3.13
/usr/local/bin/uv venv --python 3.13 .venv
/usr/local/bin/uv pip install --python .venv/bin/python -r requirements.txt

mkdir -p "$HOME/oratranslate-data/recorded_sessions"
chmod 700 "$HOME/oratranslate-data" "$HOME/oratranslate-data/recorded_sessions"

OCI_CONFIG_FILE="$HOME/.oci/config" \
OCI_CONFIG_PROFILE="DEFAULT" \
OCI_REGION="us-phoenix-1" \
OCI_COMPARTMENT_ID="replace-with-authorized-compartment-ocid" \
SPEECH_WEB_ALLOWED_ORIGINS="https://venus.aisandbox.ugbu.oraclepdemos.com" \
SESSION_STORAGE_DIR="$HOME/oratranslate-data/recorded_sessions" \
SPEECH_WEB_HOST="127.0.0.1" \
SPEECH_WEB_PORT="8011" \
.venv/bin/python speech_web_server.py
```

From a second SSH window, validate the staged process:

```bash
curl -i --max-time 5 http://127.0.0.1:8011/health
curl -i --max-time 5 http://127.0.0.1:8011/
```

Stop the staged process with Ctrl+C after validation. Port 8011 is used only
for this check and doesn't disturb the placeholder on port 8010.

## Current activation with user-managed scripts

The current Venus deployment uses a PID file and `nohup`, so `adi` can perform
the cutover without sudo. Preserve the placeholder directory, move the staged
release to `/home/adi/oratranslate`, rebuild its virtual environment at the
final path, and install the versioned scripts:

```bash
cp deploy/venus/start-oratranslate.sh start-oratranslate.sh
cp deploy/venus/stop-oratranslate.sh stop-oratranslate.sh
chmod 700 start-oratranslate.sh stop-oratranslate.sh
./start-oratranslate.sh
```

The start script sources `/home/adi/.config/oratranslate.env`, launches
`speech_web_server.py` with the project virtual environment, records its PID,
and waits for `/health`. The stop script checks both the PID and exact command
before sending `SIGTERM`.

Validate both layers after activation:

```bash
curl -i --max-time 5 http://127.0.0.1:8010/health
curl -i --max-time 10 https://venus.aisandbox.ugbu.oraclepdemos.com/OraTranslate/health
```

Finally, use a browser that trusts the HTTPS certificate to validate microphone
permission, live English transcription, French translation, End session, MP3
playback, text downloads, refresh persistence, and saved-session deletion.

## Future Supervisor activation

`deploy/venus/run-oratranslate.sh` keeps the server in the foreground for the
planned Supervisor deployment. `deploy/venus/oratranslate.service` remains a
reference for a possible systemd deployment; neither managed-process template
is needed while the user-managed start and stop scripts are active.
