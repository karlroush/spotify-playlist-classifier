from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path


SPOTIFY_AUTH_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SCOPE = "playlist-read-private playlist-modify-public playlist-modify-private"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class AuthError(Exception):
    pass


# ---------------------------------------------------------------------------
# PKCE helpers
# ---------------------------------------------------------------------------

def generate_pkce_pair() -> tuple[str, str]:
    """Return (verifier, challenge). Challenge = base64url(sha256(verifier))."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def build_auth_url(
    client_id: str,
    redirect_uri: str,
    challenge: str,
    state: str,
) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "scope": SCOPE,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
    }
    return f"{SPOTIFY_AUTH_URL}?{urllib.parse.urlencode(params)}"


# ---------------------------------------------------------------------------
# Local callback server
# ---------------------------------------------------------------------------

class CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Handles a single OAuth redirect, stores code+state, sets event."""

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if "error" in params:
            self.server.auth_error = params["error"][0]  # type: ignore[attr-defined]
        else:
            self.server.auth_code = params.get("code", [None])[0]   # type: ignore[attr-defined]
            self.server.auth_state = params.get("state", [None])[0]  # type: ignore[attr-defined]

        self._send_response()
        self.server.auth_event.set()  # type: ignore[attr-defined]

    def _send_response(self) -> None:
        body = b"<html><body><h2>Authentication complete. You may close this tab.</h2></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence access logs
        pass


# ---------------------------------------------------------------------------
# PKCE flow
# ---------------------------------------------------------------------------

def run_pkce_flow(client_id: str, redirect_uri: str) -> dict:
    """
    Open the browser for Spotify PKCE auth and wait for the callback.
    Returns the raw token dict from Spotify.
    """
    verifier, challenge = generate_pkce_pair()
    state = secrets.token_urlsafe(16)
    auth_url = build_auth_url(client_id, redirect_uri, challenge, state)

    parsed = urllib.parse.urlparse(redirect_uri)
    port = parsed.port or 8888

    server = http.server.HTTPServer(("127.0.0.1", port), CallbackHandler)
    server.auth_code = None    # type: ignore[attr-defined]
    server.auth_state = None   # type: ignore[attr-defined]
    server.auth_error = None   # type: ignore[attr-defined]
    server.auth_event = threading.Event()  # type: ignore[attr-defined]

    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    print(f"Opening browser for Spotify login...\n{auth_url}\n")
    import webbrowser
    webbrowser.open(auth_url)

    if not server.auth_event.wait(timeout=120):
        raise AuthError("Timed out waiting for Spotify callback (120s).")

    if server.auth_error:  # type: ignore[attr-defined]
        raise AuthError(f"Spotify returned error: {server.auth_error}")  # type: ignore[attr-defined]

    if server.auth_state != state:  # type: ignore[attr-defined]
        raise AuthError("State mismatch — possible CSRF. Aborting.")

    return exchange_code(client_id, server.auth_code, verifier, redirect_uri)  # type: ignore[attr-defined]


def exchange_code(
    client_id: str,
    code: str,
    verifier: str,
    redirect_uri: str,
) -> dict:
    """Exchange authorization code for tokens."""
    data = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": verifier,
    }).encode()

    req = urllib.request.Request(
        SPOTIFY_TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


# ---------------------------------------------------------------------------
# Token refresh
# ---------------------------------------------------------------------------

def refresh_access_token(client_id: str, refresh_token: str) -> dict:
    """Refresh an expired access token. Returns raw token response."""
    data = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }).encode()

    req = urllib.request.Request(
        SPOTIFY_TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


# ---------------------------------------------------------------------------
# Token storage
# ---------------------------------------------------------------------------

def _save_tokens(token_path: Path, client_id: str, token_data: dict, existing_refresh: str) -> None:
    # Spotify doesn't always return a new refresh token — keep the old one if absent
    refresh = token_data.get("refresh_token") or existing_refresh
    payload = {
        "client_id": client_id,
        "access_token": token_data["access_token"],
        "refresh_token": refresh,
        "expires_at": time.time() + int(token_data.get("expires_in", 3600)) - 60,
    }
    tmp = token_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(token_path)


def load_or_refresh_token(client_id: str, token_path: Path, redirect_port: int = 8888) -> str:
    """
    Main entry point. Returns a valid access token.
    Tries to load from disk and refresh silently; falls back to full PKCE flow.
    """
    redirect_uri = f"http://127.0.0.1:{redirect_port}/callback"

    if token_path.exists():
        try:
            stored = json.loads(token_path.read_text())
            if stored.get("client_id") != client_id:
                raise AuthError("Client ID mismatch — stored tokens are for a different app.")

            # Still valid
            if time.time() < stored.get("expires_at", 0):
                return stored["access_token"]

            # Silently refresh
            token_data = refresh_access_token(client_id, stored["refresh_token"])
            _save_tokens(token_path, client_id, token_data, stored["refresh_token"])
            return token_data["access_token"]

        except (KeyError, json.JSONDecodeError, OSError):
            pass  # fall through to full flow

    # Full PKCE flow
    token_data = run_pkce_flow(client_id, redirect_uri)
    _save_tokens(token_path, client_id, token_data, "")
    return token_data["access_token"]


def _default_port(token_path: Path) -> int:
    """Extract port from token file if present, else default 8888."""
    try:
        stored = json.loads(token_path.read_text())
        return stored.get("port", 8888)
    except Exception:
        return 8888
