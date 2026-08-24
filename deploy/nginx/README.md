# Nginx reverse proxy

The browser uses relative HTTP URLs and same-origin WebSocket URLs, so the same
frontend works directly on port 8765, through local Nginx on port 8080, or from
a future HTTPS hostname. Nginx forwards both `/ws/live` and
`/ws/translation-test` through the shared `/ws/` location.

## Local Windows proof

Download and extract the official Nginx Windows ZIP. If it is extracted at
`C:\tools\nginx-1.29.8`, use two PowerShell windows.

Python server:

```powershell
cd 'C:\path\to\ai-translation-project'
$env:SPEECH_WEB_HOST = '127.0.0.1'
$env:SPEECH_WEB_PORT = '8765'
$env:SPEECH_WEB_ALLOWED_ORIGINS = 'http://localhost:8080,http://127.0.0.1:8080'
python speech_web_server.py
```

Nginx:

```powershell
$nginxRoot = 'C:\tools\nginx-1.29.8'
$projectRoot = 'C:\path\to\ai-translation-project'

& "$nginxRoot\nginx.exe" -p "$nginxRoot\" -t -c "$projectRoot\deploy\nginx\nginx.local.conf"
& "$nginxRoot\nginx.exe" -p "$nginxRoot\" -c "$projectRoot\deploy\nginx\nginx.local.conf"
```

Open <http://localhost:8080>. Stop only this configured Nginx instance with:

```powershell
& "$nginxRoot\nginx.exe" -p "$nginxRoot\" -s quit -c "$projectRoot\deploy\nginx\nginx.local.conf"
```

## Shared server

1. Copy `oci-speech.production.conf.template` to a deployment-owned Nginx
   configuration file.
2. Replace the public hostname and TLS certificate paths.
3. Keep Python bound to `127.0.0.1:8765`; expose only Nginx.
4. Set `SPEECH_WEB_ALLOWED_ORIGINS` to the exact public HTTPS origin, for
   example `https://speech.customer.example`.
5. Set `SESSION_STORAGE_DIR` to a persistent, backed-up server directory owned
   by the Python service account.
6. Run `nginx -t`, reload Nginx, then validate `/api/health`, `/ws/live`, saved
   audio playback, and downloads through the public hostname.

Remote microphone capture requires HTTPS because browsers allow
`getUserMedia()` only in secure contexts (localhost is the development
exception). The current application doesn't implement user accounts or
per-customer authorization. Add customer SSO or another Nginx authentication
layer before exposing recordings on a shared network.
