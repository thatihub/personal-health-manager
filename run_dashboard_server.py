#!/usr/bin/env python3
"""Serve the lab dashboard on localhost with a non-conflicting default port."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
REBUILD_LOCK = threading.Lock()


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
    def _json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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
