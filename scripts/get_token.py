# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.27"]
# ///
"""Logs in through Keycloak's browser flow with the public `web` client
(authorization code + PKCE S256), the same way the web app does, and prints
the access token. smoke.sh and the eval runner use it, so no direct-grant
client has to exist in the realm.

    uv run scripts/get_token.py analyst analyst
"""

import base64
import hashlib
import html
import os
import re
import secrets
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

REPO = Path(__file__).resolve().parents[1]


def env() -> dict[str, str]:
    values = {}
    dotenv = REPO / ".env"
    if dotenv.is_file():
        for line in dotenv.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"')
    return {**values, **os.environ}


def login(username: str, password: str) -> dict:
    cfg = env()
    keycloak, realm = cfg["KEYCLOAK_PUBLIC_URL"], cfg["KC_REALM"]
    client_id = cfg["KC_WEB_CLIENT_ID"]
    redirect_uri = f"{cfg['WEB_PUBLIC_URL']}/auth/callback"
    base = f"{keycloak}/realms/{realm}/protocol/openid-connect"

    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)

    with httpx.Client(follow_redirects=False, timeout=30) as http:
        page = http.get(
            f"{base}/auth",
            params={
                "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code",
                "scope": "openid profile email", "state": state,
                "code_challenge": challenge, "code_challenge_method": "S256",
            },
        )
        page.raise_for_status()
        form = re.search(r'<form\b[^>]*\bid="kc-form-login"[^>]*>', page.text)
        action = form and re.search(r'\baction="([^"]+)"', form.group(0))
        if not action:
            raise SystemExit("Keycloak login form not found")
        # Keycloak marks its login cookies Secure. Browsers still send them to
        # http://localhost (a secure context); Python's cookie jar does not, so
        # forward them by hand.
        cookies = "; ".join(c.split(";", 1)[0] for c in page.headers.get_list("set-cookie"))
        http.cookies.clear()
        submitted = http.post(
            html.unescape(action.group(1)),
            data={"username": username, "password": password, "credentialId": ""},
            headers={"Cookie": cookies},
        )
        location = submitted.headers.get("location", "")
        query = parse_qs(urlparse(location).query)
        if submitted.status_code != 302 or not location.startswith(redirect_uri) or "code" not in query:
            raise SystemExit(f"login failed for {username} (HTTP {submitted.status_code})")
        if query.get("state") != [state]:
            raise SystemExit("state mismatch in the authorization response")
        tokens = http.post(
            f"{base}/token",
            data={
                "grant_type": "authorization_code", "client_id": client_id, "code": query["code"][0],
                "redirect_uri": redirect_uri, "code_verifier": verifier,
            },
        )
        tokens.raise_for_status()
        return tokens.json()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: get_token.py USERNAME PASSWORD")
    print(login(sys.argv[1], sys.argv[2])["access_token"])
