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
- Administrator help to install/reload systemd and Nginx configuration.

Do not copy an OCI private key into source control, a deployment archive, the
environment file, terminal output, or chat.

## Stage without interrupting the placeholder

Upload the project into `/home/adi/oratranslate-next`, then run:

```bash
cd /home/adi/oratranslate-next
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

## Activation

Activation should be performed with the administrator because the current
`adi` account cannot modify systemd or Nginx without sudo authorization.

1. Preserve `/home/adi/oratranslate` as a timestamped rollback directory.
2. Move the staged release to `/home/adi/oratranslate`.
3. Copy `deploy/venus/run-oratranslate.sh` to the project root and make it
   executable.
4. Create `/home/adi/.config/oratranslate.env` from the example with mode 600.
5. Install `deploy/venus/oratranslate.service` in
   `/etc/systemd/system/oratranslate.service`.
6. Replace the installed OraTranslate Nginx location include with
   `deploy/venus/oratranslate.nginx.conf`.
7. Run `nginx -t`, reload Nginx, reload systemd, and restart OraTranslate.

Validate both layers after activation:

```bash
curl -i --max-time 5 http://127.0.0.1:8010/health
curl -i --max-time 10 https://venus.aisandbox.ugbu.oraclepdemos.com/OraTranslate/health
systemctl status oratranslate --no-pager -l
```

Finally, use a browser that trusts the HTTPS certificate to validate microphone
permission, live English transcription, French translation, End session, MP3
playback, text downloads, refresh persistence, and saved-session deletion.
