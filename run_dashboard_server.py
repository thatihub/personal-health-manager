#!/usr/bin/env python3
"""Serve the lab dashboard on localhost with a non-conflicting default port."""

from __future__ import annotations

import argparse
import base64
import binascii
import hmac
import json
import os
import re
import subprocess
import threading
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
REBUILD_LOCK = threading.Lock()
VAULT_AUTH_USER = os.getenv("VAULT_BASIC_AUTH_USER", "").strip()
VAULT_AUTH_PASS = os.getenv("VAULT_BASIC_AUTH_PASS", "").strip()
ADMIN_AUTH_USER = os.getenv("ADMIN_BASIC_AUTH_USER", "").strip()
ADMIN_AUTH_PASS = os.getenv("ADMIN_BASIC_AUTH_PASS", "").strip()


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
    env = _rebuild_env()
    subprocess.run(
        ["python3", str(PROJECT_DIR / "rebuild_consolidation.py")],
        check=True,
        cwd=PROJECT_DIR.parent,
        env=env,
    )


def _decode_env_text(plain_var: str, b64_var: str) -> str | None:
    plain = os.getenv(plain_var, "")
    if plain:
        return plain
    encoded = os.getenv(b64_var, "")
    if not encoded:
        return None
    try:
        return base64.b64decode(encoded).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None


def _default_upload_dir() -> Path:
    return Path(os.getenv("ADMIN_UPLOADS_DIR", str(PROJECT_DIR / "_uploaded_pdfs")))


def _rebuild_env(upload_dir_override: Path | None = None) -> dict[str, str]:
    env = os.environ.copy()
    if env.get("LAB_RESULTS_DIR", "").strip():
        return env
    if upload_dir_override is not None:
        env["LAB_RESULTS_DIR"] = str(upload_dir_override)
        return env
    default_upload = _default_upload_dir()
    if default_upload.exists():
        env["LAB_RESULTS_DIR"] = str(default_upload)
    return env


class DashboardHandler(SimpleHTTPRequestHandler):
    def _parse_multipart_form(self) -> tuple[dict[str, str], dict[str, tuple[str, bytes]]]:
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            raise ValueError("Use multipart/form-data.")
        m = re.search(r'boundary="?([^";]+)"?', ctype)
        if not m:
            raise ValueError("Missing multipart boundary.")
        boundary = m.group(1).encode("utf-8")
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""

        fields: dict[str, str] = {}
        files: dict[str, tuple[str, bytes]] = {}
        marker = b"--" + boundary
        parts = body.split(marker)
        for part in parts:
            chunk = part.strip()
            if not chunk or chunk == b"--":
                continue
            if chunk.startswith(b"\r\n"):
                chunk = chunk[2:]
            if chunk.endswith(b"--"):
                chunk = chunk[:-2]
            header_blob, sep, content = chunk.partition(b"\r\n\r\n")
            if not sep:
                continue
            headers = header_blob.decode("utf-8", errors="ignore").split("\r\n")
            disposition = ""
            for h in headers:
                if h.lower().startswith("content-disposition:"):
                    disposition = h
                    break
            if not disposition:
                continue
            name_match = re.search(r'name="([^"]+)"', disposition)
            if not name_match:
                continue
            field_name = name_match.group(1)
            filename_match = re.search(r'filename="([^"]*)"', disposition)
            if content.endswith(b"\r\n"):
                content = content[:-2]
            if filename_match and filename_match.group(1):
                files[field_name] = (filename_match.group(1), content)
            else:
                fields[field_name] = content.decode("utf-8", errors="ignore")
        return fields, files

    def _admin_auth_enabled(self) -> bool:
        return bool(ADMIN_AUTH_USER and ADMIN_AUTH_PASS)

    def _vault_auth_enabled(self) -> bool:
        return bool(VAULT_AUTH_USER and VAULT_AUTH_PASS)

    def _is_vault_protected_path(self) -> bool:
        path = self.path.split("?", 1)[0]
        return path in {"/vault.html", "/vault_data.json"}

    def _is_admin_protected_path(self) -> bool:
        path = self.path.split("?", 1)[0]
        return path == "/admin.html" or path.startswith("/api/admin/")

    def _is_authorized(self, expected_user: str, expected_pass: str) -> bool:
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
        return hmac.compare_digest(username, expected_user) and hmac.compare_digest(password, expected_pass)

    def _require_vault_auth(self) -> bool:
        if not self._vault_auth_enabled() or not self._is_vault_protected_path():
            return False
        if self._is_authorized(VAULT_AUTH_USER, VAULT_AUTH_PASS):
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

    def _require_admin_auth(self) -> bool:
        if not self._is_admin_protected_path():
            return False
        if not self._admin_auth_enabled():
            self._json(
                403,
                {"ok": False, "error": "Admin auth disabled. Set ADMIN_BASIC_AUTH_USER and ADMIN_BASIC_AUTH_PASS."},
            )
            return True
        if self._is_authorized(ADMIN_AUTH_USER, ADMIN_AUTH_PASS):
            return False
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Health Admin"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        body = b"Authentication required for Health Admin."
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        return True

    def _last_updated_payload(self) -> dict[str, object]:
        override_sources = []
        if _decode_env_text("VAULT_DATA_JSON", "VAULT_DATA_JSON_B64") is not None:
            override_sources.append("vault_data.json")
        if _decode_env_text("DASHBOARD_DATA_JSON", "DASHBOARD_DATA_JSON_B64") is not None:
            override_sources.append("dashboard_data.js")
        if _decode_env_text("CONSOLIDATED_LABS_JSON", "CONSOLIDATED_LABS_JSON_B64") is not None:
            override_sources.append("consolidated_labs.json")

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
        env_updated = os.getenv("DATA_LAST_UPDATED", "").strip()
        if env_updated and override_sources:
            latest_display = env_updated
            latest_iso = env_updated
        elif latest_epoch > 0:
            latest_dt = datetime.fromtimestamp(latest_epoch).astimezone()
            latest_display = latest_dt.strftime("%b %d, %Y %I:%M %p")
            latest_iso = latest_dt.isoformat()

        return {
            "ok": True,
            "vault_auth_enabled": self._vault_auth_enabled(),
            "admin_auth_enabled": self._admin_auth_enabled(),
            "latest_display": latest_display,
            "latest_iso": latest_iso,
            "data_source": "environment" if override_sources else "files",
            "env_overrides": override_sources,
            "files": known_files,
        }

    def _send_content(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _serve_env_override(self, path: str) -> bool:
        if path == "/vault_data.json":
            payload = _decode_env_text("VAULT_DATA_JSON", "VAULT_DATA_JSON_B64")
            if payload is None:
                return False
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError:
                self._json(500, {"ok": False, "error": "Invalid VAULT_DATA_JSON/VAULT_DATA_JSON_B64"})
                return True
            body = json.dumps(parsed).encode("utf-8")
            self._send_content(200, "application/json; charset=utf-8", body)
            return True

        if path == "/dashboard_data.js":
            payload = _decode_env_text("DASHBOARD_DATA_JSON", "DASHBOARD_DATA_JSON_B64")
            if payload is None:
                return False
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError:
                self._json(500, {"ok": False, "error": "Invalid DASHBOARD_DATA_JSON/DASHBOARD_DATA_JSON_B64"})
                return True
            body = f"window.LAB_DASH_DATA = {json.dumps(parsed)};\n".encode("utf-8")
            self._send_content(200, "application/javascript; charset=utf-8", body)
            return True

        if path == "/consolidated_labs.json":
            payload = _decode_env_text("CONSOLIDATED_LABS_JSON", "CONSOLIDATED_LABS_JSON_B64")
            if payload is None:
                return False
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError:
                self._json(
                    500,
                    {"ok": False, "error": "Invalid CONSOLIDATED_LABS_JSON/CONSOLIDATED_LABS_JSON_B64"},
                )
                return True
            body = json.dumps(parsed).encode("utf-8")
            self._send_content(200, "application/json; charset=utf-8", body)
            return True
        return False

    def _is_override_active_for_target(self, target: str) -> bool:
        if target == "vault":
            return _decode_env_text("VAULT_DATA_JSON", "VAULT_DATA_JSON_B64") is not None
        if target == "dashboard":
            return _decode_env_text("DASHBOARD_DATA_JSON", "DASHBOARD_DATA_JSON_B64") is not None
        if target == "consolidated":
            return _decode_env_text("CONSOLIDATED_LABS_JSON", "CONSOLIDATED_LABS_JSON_B64") is not None
        return False

    def _write_json_file(self, path: Path, payload: object) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(path)

    def _write_dashboard_js(self, payload: object) -> None:
        path = PROJECT_DIR / "dashboard_data.js"
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(f"window.LAB_DASH_DATA = {json.dumps(payload)};\n", encoding="utf-8")
        tmp.replace(path)

    def _handle_admin_upload(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json(400, {"ok": False, "error": "Invalid JSON body"})
            return

        target = str(payload.get("target", "")).strip()
        content = payload.get("content")
        if target not in {"vault", "dashboard", "consolidated"}:
            self._json(400, {"ok": False, "error": "target must be one of: vault, dashboard, consolidated"})
            return
        if content is None:
            self._json(400, {"ok": False, "error": "content is required"})
            return
        if self._is_override_active_for_target(target):
            self._json(
                409,
                {"ok": False, "error": f"Env override active for {target}. Remove related *_JSON vars first."},
            )
            return

        try:
            if target == "vault":
                if not isinstance(content, dict):
                    raise ValueError("Vault content must be a JSON object")
                self._write_json_file(PROJECT_DIR / "vault_data.json", content)
                self._json(200, {"ok": True, "target": "vault_data.json"})
                return

            if target == "dashboard":
                if not isinstance(content, list):
                    raise ValueError("Dashboard content must be a JSON array")
                self._write_dashboard_js(content)
                self._json(200, {"ok": True, "target": "dashboard_data.js"})
                return

            if target == "consolidated":
                if not isinstance(content, list):
                    raise ValueError("Consolidated content must be a JSON array")
                self._write_json_file(PROJECT_DIR / "consolidated_labs.json", content)
                self._json(200, {"ok": True, "target": "consolidated_labs.json"})
                return
        except ValueError as exc:
            self._json(400, {"ok": False, "error": str(exc)})
            return

    def _run_rebuild(self, upload_dir_override: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(PROJECT_DIR / "rebuild_consolidation.py")],
            cwd=PROJECT_DIR.parent,
            check=False,
            capture_output=True,
            text=True,
            env=_rebuild_env(upload_dir_override),
        )

    def _handle_admin_pdf_upload(self) -> None:
        try:
            fields, files = self._parse_multipart_form()
        except ValueError as exc:
            self._json(400, {"ok": False, "error": str(exc)})
            return
        if "file" not in files:
            self._json(400, {"ok": False, "error": "Missing file field."})
            return

        raw_name, file_bytes = files["file"]
        filename = Path(raw_name or "").name
        if not filename:
            self._json(400, {"ok": False, "error": "No file selected."})
            return
        if not filename.lower().endswith(".pdf"):
            self._json(400, {"ok": False, "error": "Only PDF files are supported."})
            return

        upload_dir = _default_upload_dir()
        upload_dir.mkdir(parents=True, exist_ok=True)
        target = upload_dir / filename
        if target.exists():
            stem = target.stem
            suffix = target.suffix
            i = 1
            while target.exists():
                target = upload_dir / f"{stem}_{i}{suffix}"
                i += 1

        with target.open("wb") as f:
            f.write(file_bytes)

        do_rebuild = str(fields.get("rebuild", "1")).strip() != "0"
        if not do_rebuild:
            self._json(200, {"ok": True, "uploaded": target.name, "rebuild": False, "upload_dir": str(upload_dir)})
            return

        result = self._run_rebuild(upload_dir_override=upload_dir)
        if result.returncode != 0:
            self._json(
                500,
                {
                    "ok": False,
                    "error": "PDF uploaded but rebuild failed",
                    "uploaded": target.name,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                },
            )
            return

        self._json(
            200,
            {
                "ok": True,
                "uploaded": target.name,
                "rebuild": True,
                "upload_dir": str(upload_dir),
                "stdout": result.stdout,
            },
        )

    def _json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/api/last-updated":
            self._json(200, self._last_updated_payload())
            return
        if self._require_admin_auth():
            return
        if self._require_vault_auth():
            return
        if self._serve_env_override(path):
            return
        super().do_GET()

    def do_HEAD(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if self._require_admin_auth():
            return
        if self._require_vault_auth():
            return
        if self._serve_env_override(path):
            return
        super().do_HEAD()

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/api/admin/upload-pdf":
            if self._require_admin_auth():
                return
            self._handle_admin_pdf_upload()
            return

        if self.path == "/api/admin/upload-data":
            if self._require_admin_auth():
                return
            self._handle_admin_upload()
            return

        if self.path != "/api/rebuild":
            self._json(404, {"ok": False, "error": "Not found"})
            return

        if not REBUILD_LOCK.acquire(blocking=False):
            self._json(409, {"ok": False, "error": "Rebuild already in progress"})
            return

        try:
            result = self._run_rebuild()
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
