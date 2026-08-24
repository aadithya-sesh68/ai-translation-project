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
$env:OCI_COMPARTMENT_ID = 'ocid1.compartment.oc1..replace_with_yours'
python speech_web_server.py
```

Then open <http://localhost:8765> in Edge or Chrome, select **Start listening**,
allow microphone access, speak English, and select **Stop** to flush the final
segment.

The defaults match this prototype:

- OCI authentication: API-key profile loaded from the OCI config file
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
$env:SPEECH_WEB_PORT = '8765'
```

The selected profile must be an API-key profile containing `tenancy`, `user`,
`fingerprint`, `key_file`, and `region`. Session-token profiles are rejected.
Do not put private keys or their contents in environment variables or source
files. The server reads the API-key configuration from the OCI config file and
never sends credentials to the browser.

## Request flow

1. The browser requests microphone permission only after **Start listening**.
2. An AudioWorklet resamples the browser's native audio rate to 16 kHz PCM.
3. Binary PCM chunks travel over the same-origin `/ws/live` WebSocket.
4. The server opens one OCI Speech session per browser session.
5. Final English segments appear immediately and are grouped briefly for OCI
   Language translation.
6. **Stop** flushes remaining audio, requests the final Speech result, completes
   queued translations, and closes the session.

Final English text, French translations, and displayed diagnostics are kept in
the browser tab's session storage. Refreshing the page restores them. Starting
a new listening session clears the previous results, and closing the tab clears
the stored session data. Audio and OCI credentials are never stored there.

## OCI Language 404 diagnostics

Translation errors are not automatically retried. The page shows the OCI
status, error message, and OPC request ID when OCI provides one. For a
`404 NotAuthorizedOrNotFound`, keep the OPC request ID and check:

- whether the OCI Language IAM policy has propagated and authorizes the caller;
- whether OCI Language translation is available in the configured region;
- whether `OCI_COMPARTMENT_ID` points to the intended compartment.
