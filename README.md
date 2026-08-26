# OCI live English-to-French browser prototype

This local client/server app captures microphone audio in the browser, converts
it to 16 kHz mono signed 16-bit PCM, and streams it to a Python WebSocket
server. The server alone authenticates to OCI, sends audio to Speech Realtime
using the WHISPER model, and translates buffered final English segments to
French with OCI Language.

## Run it in PowerShell

From the project directory:

```powershell
python -m pip install -r requirements.txt
$env:OCI_CONFIG_PROFILE = 'DEFAULT'
$env:OCI_COMPARTMENT_ID = 'ocid1.compartment.oc1..replace_with_yours'
python speech_web_server.py
```

Then open <http://localhost:8765> in Edge or Chrome, optionally enter a session
name, select **Start session**, allow microphone access, speak English, and
select **End session** to flush the final segment and save the outputs.

The defaults match this prototype:

- OCI authentication: persistent API-key signing
- OCI profile: `DEFAULT`
- OCI region: `us-phoenix-1` (or the profile's region)
- Translation grouping window: 1.5 seconds
- Web server: `127.0.0.1:8765`

`OCI_COMPARTMENT_ID` is required. Other overrides for the current PowerShell
window are optional:

```powershell
$env:OCI_CONFIG_PROFILE = 'DEFAULT'
$env:OCI_REGION = 'us-phoenix-1'
$env:OCI_COMPARTMENT_ID = 'ocid1.compartment.oc1..replace_with_yours'
$env:TRANSLATION_BUFFER_SECONDS = '1.5'
$env:ORATRANSLATE_LOG_LEVEL = 'INFO'
$env:SPEECH_WEB_PORT = '8765'
$env:SPEECH_WEB_ALLOWED_ORIGINS = 'http://localhost:8080,https://speech.customer.example'
$env:SESSION_STORAGE_DIR = 'C:\path\to\persistent\recorded_sessions'
```

The selected profile must be an API-key profile containing `tenancy`, `user`,
`fingerprint`, `key_file`, and `region`. Profiles containing
`security_token_file` or `authentication_type` are rejected so the application
cannot accidentally fall back to temporary session authentication.

The server creates two independent `oci.signer.Signer` instances from the same
profile: one is owned by OCI Speech Realtime and one is owned by the dedicated
Language `TranslationService`. Each signer and client is reused for its own
service throughout one live browser session. Do not put private keys or their
contents in environment variables or source files. The server reads the
private-key path from the OCI config profile and never sends credentials to the
browser.

Server logs are emitted as one JSON object per line. Translation request logs
include the saved session ID, request number, latency, status/code, and OPC
request ID, but deliberately omit transcript text, request headers, key paths,
and credentials. Set `ORATRANSLATE_LOG_LEVEL` to `DEBUG`, `INFO`, `WARNING`, or
`ERROR` to change the process log level; the default is `INFO`.

For a dedicated deployment identity, set `OCI_CONFIG_PROFILE=API-USER` after
that profile has been added to the OCI config. The profile name itself is not
special; it must reference the API signing key uploaded for the intended OCI
user. The application defaults to the existing `DEFAULT` API-key profile and
rejects profiles that contain temporary session-token settings.

## API-key IAM requirements

The API-key user must belong to an IAM group authorized for both services in
the exact compartment passed through `OCI_COMPARTMENT_ID`. Ask the tenancy
administrator to confirm policies equivalent to the following, using the real
group and compartment names:

```text
allow group <api-key-user-group> to manage ai-service-speech-family in compartment <compartment-name>
allow group <api-key-user-group> to use ai-service-language-family in compartment <compartment-name>
```

An API key can be valid while a service still returns
`404 NotAuthorizedOrNotFound` for an unauthorized compartment. Realtime Speech
can surface the same authorization problem as WebSocket close code `1008` with
`AUTHENTICATION_FAILURE`. Confirm the profile's user group, both policies, and
the compartment together; changing only the signer cannot grant IAM access.

API signing keys don't have the short lifetime of OCI CLI session tokens.
However, a long-running customer session must still handle ordinary network,
browser, proxy, or service WebSocket disconnections. Automatic reconnection is
not yet implemented in this prototype.

## Request flow

1. The browser requests microphone permission only after **Start session**.
2. An AudioWorklet resamples the browser's native audio rate to 16 kHz PCM.
3. Binary PCM chunks travel over the same-origin `/ws/live` WebSocket.
4. The server opens one OCI Speech session per browser session.
5. Final English segments appear immediately and are grouped briefly for OCI
   Language translation.
6. **End session** flushes remaining audio, requests the final Speech result,
   and completes queued translations.
7. The server closes the MP3 recording and saves English, French, metadata, and
   safe OCI diagnostics in a session-specific folder.
8. The **Session recordings** section refreshes automatically so the completed
   session can be played and its text outputs reviewed or downloaded.

Live English text, French translations, and displayed diagnostics are also kept
temporarily in the browser tab's session storage so refreshing the page can
restore the last live view. Starting a new session clears that live view.

Completed outputs persist on the server under `recorded_sessions` by default:

```text
recorded_sessions\<session-id>\session.mp3
recorded_sessions\<session-id>\english.txt
recorded_sessions\<session-id>\french.txt
recorded_sessions\<session-id>\metadata.json
recorded_sessions\<session-id>\diagnostics.json
```

The MP3 is encoded directly from the same 16 kHz mono PCM stream sent to OCI
Speech. If the browser disconnects without **End session**, the server still
finalizes the available outputs and marks the saved session as `interrupted`.
OCI credentials are never written into a session folder or sent to the browser.

Refreshing the browser does not delete completed sessions. The session library
is rebuilt from the server folders after every refresh. The current tab's last
live English/French view is also restored from browser session storage. If a
page is refreshed during an active session, that connection ends and the server
saves the available recording and text as an `interrupted` session.

Use **Delete saved session** in the selected session viewer to permanently
remove that session's complete server folder. The UI requires confirmation;
after deletion, its MP3, English text, French text, metadata, and diagnostics
cannot be recovered.

## Nginx reverse proxy

The application is reverse-proxy ready. The frontend uses relative URLs, Nginx
forwards ordinary HTTP and both `/ws/` WebSocket endpoints, and the Python
server accepts explicitly configured public browser origins.

Use [`deploy/nginx/nginx.local.conf`](deploy/nginx/nginx.local.conf) to validate
the complete path locally at <http://localhost:8080>. Use
[`deploy/nginx/oci-speech.production.conf.template`](deploy/nginx/oci-speech.production.conf.template)
as the starting point for an HTTPS shared server. Exact Windows and shared-host
steps are in [`deploy/nginx/README.md`](deploy/nginx/README.md).

The Venus-specific deployment uses the existing `127.0.0.1:8010` service and
publishes the app beneath `/OraTranslate/`. Its versioned startup, systemd,
Nginx, and environment templates are in [`deploy/venus`](deploy/venus). The
frontend derives HTTP and WebSocket URLs from the page location, so the same
build works at both the local root and the Venus path prefix.

Keep the Python service bound to `127.0.0.1` when Nginx runs on the same host.
Set `SPEECH_WEB_ALLOWED_ORIGINS` to every exact public browser origin; wildcard
origins are intentionally unsupported. A remote deployment must use HTTPS for
browser microphone permission and must add customer authentication before
exposing stored recordings.

The URL customers use should be a DNS name pointing to Nginx, such as
`https://speech.customer.example`. The frontend automatically uses that same
host for its HTTP APIs and secure `wss://` connections. An IP address or local
network hostname can be used for basic reachability testing, but microphone
capture from a remote browser requires a trusted HTTPS certificate.

## OCI Language reliability test

Open <http://localhost:8765/translation-test.html> in a separate browser tab to
test OCI Language without microphone audio or Speech Realtime. The page starts
with 30 English sentences and sends each sentence as a separate
`batch_language_translation` request using the same API-key profile and
compartment as the live app.

Start with concurrency `1` and a `250 ms` pause to establish a sequential
baseline. Then compare runs with concurrency `2`, `3`, or `5`. Each request is
attempted exactly once; the test does not automatically retry 404, 429, or
other failures. The results table records latency, HTTP status/code, French
text or error message, and the OPC request ID. Results can be downloaded as a
JSON file for comparison or escalation.

An HTTP `429 TooManyRequests` result is evidence of throttling. A
`404 NotAuthorizedOrNotFound` result is recorded separately and should still
be investigated as an authorization, region, compartment, or service-resource
problem. Running the test while the live page is also translating intentionally
adds concurrent OCI Language traffic, so perform the sequential standalone run
first.

## OCI Language 404 diagnostics

Translation errors are not automatically retried. The page shows the OCI
status, error message, and OPC request ID when OCI provides one. For a
`404 NotAuthorizedOrNotFound`, keep the OPC request ID and check:

- whether the OCI Language IAM policy has propagated and authorizes the caller;
- whether OCI Language translation is available in the configured region;
- whether `OCI_COMPARTMENT_ID` points to the intended compartment.
