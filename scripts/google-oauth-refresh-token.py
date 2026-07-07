#!/usr/bin/env python3
"""Get a Google OAuth refresh token for Kabosu Calendar + Drive.

Required env:
  GOOGLE_CLIENT_ID
  GOOGLE_CLIENT_SECRET

The script opens a browser, receives the OAuth callback on localhost, and
prints the KABOSU_GOOGLE_REFRESH_TOKEN value for deploy/env-example.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer


SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/drive.file",
]
TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"


class OAuthCallback(BaseHTTPRequestHandler):
    server_version = "KabosuOAuth/1.0"

    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        self.server.oauth_code = (params.get("code") or [""])[0]
        self.server.oauth_state = (params.get("state") or [""])[0]
        self.server.oauth_error = (params.get("error") or [""])[0]
        body = (
            "OAuth callback received. You can close this tab and return to the terminal."
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body.encode("utf-8"))))
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, _format, *args):
        return


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--account-email",
        default=os.getenv("KABOSU_CALENDAR_ACCOUNT_EMAIL", "").strip(),
        help="Google account email to show as the OAuth login hint.",
    )
    args = parser.parse_args()

    client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        print("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are required", file=sys.stderr)
        return 2

    redirect_uri = f"http://{args.host}:{args.port}/oauth2callback"
    state = secrets.token_urlsafe(24)
    auth_params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "select_account consent",
        "state": state,
    }
    if args.account_email:
        auth_params["login_hint"] = args.account_email
    auth_url = f"{AUTH_URL}?{urllib.parse.urlencode(auth_params)}"

    server = HTTPServer((args.host, args.port), OAuthCallback)
    server.oauth_code = ""
    server.oauth_state = ""
    server.oauth_error = ""
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    if args.account_email:
        print(f"Open this URL as {args.account_email}:")
    else:
        print("Open this URL with the Kabosu Google account:")
    print(auth_url)
    if not args.no_browser:
        webbrowser.open(auth_url)

    thread.join(timeout=300)
    server.server_close()

    if server.oauth_error:
        print(f"OAuth failed: {server.oauth_error}", file=sys.stderr)
        return 1
    if not server.oauth_code:
        print("Timed out waiting for OAuth callback", file=sys.stderr)
        return 1
    if server.oauth_state != state:
        print("OAuth state mismatch", file=sys.stderr)
        return 1

    data = urllib.parse.urlencode({
        "code": server.oauth_code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode("utf-8")
    req = urllib.request.Request(
        TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        token_data = json.loads(resp.read().decode("utf-8"))

    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        print(
            "No refresh_token returned. Re-run with prompt=consent and ensure this OAuth client "
            "is allowed for the requested scopes.",
            file=sys.stderr,
        )
        print(json.dumps(token_data, indent=2, ensure_ascii=False))
        return 1

    print()
    print("Add this to .env:")
    print(f"KABOSU_GOOGLE_REFRESH_TOKEN={refresh_token}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
