# API-key authentication checkpoint

Created: 2026-08-23

## Scope

This checkpoint preserves only the files changed for the OCI API-key
authentication experiment:

- `oci_speech_service.py`
- `README.md`

It contains no OCI config file, private key, security token, JWT, browser data,
or other credential material.

## Preserved behavior

- Loads the `DEFAULT` API-key profile from the OCI config file.
- Rejects session-token profiles.
- Creates OCI Language and Realtime Speech clients from the API-key config.
- Does not construct or pass `SecurityTokenSigner`.
- Keeps the existing Whisper, browser audio, transcript, and translation UI.

## Validation and known blocker

- Python compilation passed.
- The `DEFAULT` profile constructed standard API-key signers for both clients.
- OCI Language translation succeeded with the API key.
- Direct API-key authentication to Realtime Speech was unreliable and returned
  WebSocket `1008 AUTHENTICATION_FAILURE` responses.
- The API-key request to `CreateRealtimeSessionToken` returned
  `404 NotAuthorizedOrNotFound`; IAM group and Speech policy access require
  clarification before restoring this checkpoint.

## Restore

From the project root, compare the working files first. When ready to restore
the API-key approach, copy only these two files:

```powershell
Copy-Item -LiteralPath '.\checkpoints\api-key-auth-2026-08-23\oci_speech_service.py' -Destination '.\oci_speech_service.py'
Copy-Item -LiteralPath '.\checkpoints\api-key-auth-2026-08-23\README.md' -Destination '.\README.md'
```

Then validate with Python compilation and a live Speech/Language authentication
test. Do not restore the checkpoint until the API-key user's compartment and
IAM policy have been confirmed.
