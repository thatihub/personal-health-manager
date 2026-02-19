#!/usr/bin/env python3
"""Serve the lab dashboard on localhost with a non-conflicting default port."""

from __future__ import annotations

import argparse
import base64
import binascii
import hmac
import json
import os
import subprocess
import threading
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
REBUILD_LOCK = threading.Lock()
VAULT_AUTH_USER = os.getenv("VAULT_BASIC_AUTH_USER", "").strip()
VAULT_AUTH_PASS = os.getenv("VAULT_BASIC_AUTH_PASS", "").strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run lab dashboard locally.")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("PORT", "3002")),
        help="Port to bind (default: 3002).",
    )
    parser.add_argument(
        "--no-rebuild",
        action="store_true",
        help="Skip rebuilding consolidated files before serving.",
    )
    return parser.parse_args()


def rebuild_if_needed(skip: bool) -> None:
    if skip:
        return
    rebuild_now()


def rebuild_now() -> None:
    subprocess.run(
        ["python3", str(PROJECT_DIR / "rebuild_consolidation.py")],
        check=True,
        cwd=PROJECT_DIR.parent,
    )


class DashboardHandler(SimpleHTTPRequestHandler):
    def _vault_auth_enabled(self) -> bool:
        return bool(VAULT_AUTH_USER and VAULT_AUTH_PASS)

    def _is_vault_protected_path(self) -> bool:
        path = self.path.split("?", 1)[0]
        return path in {"/vault.html", "/vault_data.json"}

    def _is_authorized(self) -> bool:
        auth_header = self.headers.get("Authorization", "")
        if not auth_header.startswith("Basic "):
            return False
        encoded = auth_header[6:].strip()
        try:
            decoded = base64.b64decode(encoded).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            return False
        if ":" not in decoded:
            return False
        username, password = decoded.split(":", 1)
        return hmac.compare_digest(username, VAULT_AUTH_USER) and hmac.compare_digest(password, VAULT_AUTH_PASS)

    def _require_vault_auth(self) -> bool:
        if not self._vault_auth_enabled() or not self._is_vault_protected_path():
            return False
        if self._is_authorized():
            return False
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Health Vault"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        body = b"Authentication required for Health Vault."
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        return True

    def _last_updated_payload(self) -> dict[str, object]:
        files = [
            ("Consolidated Labs", PROJECT_DIR / "consolidated_labs.json"),
            ("Dashboard Data", PROJECT_DIR / "dashboard_data.js"),
            ("Vault Data", PROJECT_DIR / "vault_data.json"),
        ]
        known_files: list[dict[str, object]] = []
        latest_epoch = 0.0
        for label, path in files:
            if not path.exists():
                continue
            mtime = path.stat().st_mtime
            latest_epoch = max(latest_epoch, mtime)
            dt = datetime.fromtimestamp(mtime).astimezone()
            known_files.append(
                {
                    "label": label,
                    "file": path.name,
                    "updated_iso": dt.isoformat(),
                    "updated_display": dt.strftime("%b %d, %Y %I:%M %p"),
                }
            )

        latest_display = "-"
        latest_iso = ""
        if latest_epoch > 0:
            latest_dt = datetime.fromtimestamp(latest_epoch).astimezone()
            latest_display = latest_dt.strftime("%b %d, %Y %I:%M %p")
            latest_iso = latest_dt.isoformat()

        return {
            "ok": True,
            "vault_auth_enabled": self._vault_auth_enabled(),
            "latest_display": latest_display,
            "latest_iso": latest_iso,
            "files": known_files,
        }

    def _json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] == "/api/last-updated":
            self._json(200, self._last_updated_payload())
            return
        if self._require_vault_auth():
            return
        super().do_GET()

    def do_HEAD(self) -> None:  # noqa: N802
        if self._require_vault_auth():
            return
        super().do_HEAD()

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/rebuild":
            self._json(404, {"ok": False, "error": "Not found"})
            return

        if not REBUILD_LOCK.acquire(blocking=False):
            self._json(409, {"ok": False, "error": "Rebuild already in progress"})
            return

        try:
            result = subprocess.run(
                ["python3", str(PROJECT_DIR / "rebuild_consolidation.py")],
                cwd=PROJECT_DIR.parent,
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                self._json(
                    500,
                    {
                        "ok": False,
                        "error": "Rebuild failed",
                        "stdout": result.stdout,
                        "stderr": result.stderr,
                    },
                )
                return
            self._json(200, {"ok": True, "stdout": result.stdout})
        finally:
            REBUILD_LOCK.release()


def main() -> None:
    args = parse_args()
    rebuild_if_needed(args.no_rebuild)

    os.chdir(PROJECT_DIR)
    server = ThreadingHTTPServer(("0.0.0.0", args.port), DashboardHandler)
    print(f"Personal Health Manager running at http://localhost:{args.port}/index.html")
    print("Press Ctrl+C to stop.")
    server.serve_forever()


if __name__ == "__main__":
    main()
