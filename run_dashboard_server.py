#!/usr/bin/env python3
"""Serve the lab dashboard on localhost with a non-conflicting default port."""

from __future__ import annotations

import argparse
import base64
import binascii
import csv
import hashlib
import hmac
import io
import json
import os
import re
import subprocess
import threading
from datetime import datetime, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
REBUILD_LOCK = threading.Lock()
VAULT_AUTH_USER = os.getenv("VAULT_BASIC_AUTH_USER", "").strip()
VAULT_AUTH_PASS = os.getenv("VAULT_BASIC_AUTH_PASS", "").strip()
ADMIN_AUTH_USER = os.getenv("ADMIN_BASIC_AUTH_USER", "").strip()
ADMIN_AUTH_PASS = os.getenv("ADMIN_BASIC_AUTH_PASS", "").strip()
BP_DATA_PATH = PROJECT_DIR / "bp_readings.csv"


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
    def _decode_csv_bytes(self, raw: bytes) -> str:
        for enc in ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "latin-1"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="ignore")

    def _to_int(self, v: str) -> int | None:
        try:
            return int(float(str(v).strip()))
        except Exception:
            return None

    def _parse_bp_datetime(self, date_s: str, time_s: str) -> datetime | None:
        date_s = date_s.strip()
        time_s = time_s.strip()
        candidates = [
            f"{date_s} {time_s}",
            date_s,
        ]
        fmts = [
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%m/%d/%Y %H:%M",
            "%m/%d/%Y %I:%M %p",
            "%Y-%m-%d",
            "%m/%d/%Y",
        ]
        for c in candidates:
            for fmt in fmts:
                try:
                    return datetime.strptime(c, fmt)
                except ValueError:
                    continue
        return None

    def _parse_bp_csv(self, raw: bytes, source_file: str) -> list[dict[str, object]]:
        text = self._decode_csv_bytes(raw).replace("\x00", "")
        sample = text[:2048]
        if sample.count("\t") >= max(sample.count(","), sample.count(";")):
            delimiter = "\t"
        elif sample.count(";") > sample.count(","):
            delimiter = ";"
        else:
            delimiter = ","
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        rows: list[dict[str, object]] = []

        # Map incoming headers to canonical names.
        def pick(d: dict[str, str], keys: list[str]) -> str:
            norm = lambda s: re.sub(r"\s+", " ", (s or "").strip().lower())
            normalized_keys = [norm(k) for k in keys]
            for k in keys:
                for dk, dv in d.items():
                    if dk is None:
                        continue
                    ndk = norm(dk)
                    nk = norm(k)
                    if ndk == nk or nk in ndk:
                        return (dv or "").strip()
            for dk, dv in d.items():
                ndk = norm(dk or "")
                for nk in normalized_keys:
                    if nk in ndk:
                        return (dv or "").strip()
            return ""

        for rec in reader:
            if not rec:
                continue
            date_s = pick(rec, ["date"])
            time_s = pick(rec, ["time"]) or "00:00"
            sys_s = pick(rec, ["sys mmhg", "sys", "systolic"])
            dia_s = pick(rec, ["dia mmhg", "dia", "diastolic"])
            pulse_s = pick(rec, ["pulse beats/min", "pulse", "heart rate"])

            dt = self._parse_bp_datetime(date_s, time_s)
            sys_v = self._to_int(sys_s)
            dia_v = self._to_int(dia_s)
            pulse_v = self._to_int(pulse_s)
            if dt is None or sys_v is None or dia_v is None or pulse_v is None:
                continue
            rows.append(
                {
                    "date": dt.strftime("%Y-%m-%d"),
                    "time": dt.strftime("%H:%M"),
                    "systolic": sys_v,
                    "diastolic": dia_v,
                    "pulse": pulse_v,
                    "source_file": source_file,
                }
            )
        return rows

    def _read_existing_bp(self) -> list[dict[str, object]]:
        if not BP_DATA_PATH.exists():
            return []
        with BP_DATA_PATH.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            rows: list[dict[str, object]] = []
            for r in reader:
                try:
                    rows.append(
                        {
                            "date": (r.get("date") or "").strip(),
                            "time": (r.get("time") or "").strip(),
                            "systolic": int(r.get("systolic", "0")),
                            "diastolic": int(r.get("diastolic", "0")),
                            "pulse": int(r.get("pulse", "0")),
                            "source_file": (r.get("source_file") or "").strip(),
                        }
                    )
                except Exception:
                    continue
            return rows

    def _write_bp_rows(self, rows: list[dict[str, object]]) -> None:
        rows = sorted(rows, key=lambda r: (str(r["date"]), str(r["time"])))
        with BP_DATA_PATH.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["date", "time", "systolic", "diastolic", "pulse", "source_file"])
            writer.writeheader()
            writer.writerows(rows)

    def _merge_bp_rows(self, existing: list[dict[str, object]], incoming: list[dict[str, object]]) -> tuple[list[dict[str, object]], int]:
        seen = {
            (str(r["date"]), str(r["time"]), int(r["systolic"]), int(r["diastolic"]), int(r["pulse"]))
            for r in existing
        }
        added = 0
        for r in incoming:
            key = (str(r["date"]), str(r["time"]), int(r["systolic"]), int(r["diastolic"]), int(r["pulse"]))
            if key in seen:
                continue
            seen.add(key)
            existing.append(r)
            added += 1
        return existing, added

    def _bp_last_days(self, days: int = 90) -> list[dict[str, object]]:
        rows = self._read_existing_bp()
        if not rows:
            return []
        dated: list[tuple[datetime, dict[str, object]]] = []
        for r in rows:
            dt = self._parse_bp_datetime(str(r.get("date", "")), str(r.get("time", "")))
            if dt is None:
                continue
            dated.append((dt, r))
        if not dated:
            return []
        latest_dt = max(dt for dt, _ in dated)
        threshold = latest_dt - timedelta(days=max(1, days))
        out: list[dict[str, object]] = []
        for dt, r in dated:
            if dt < threshold:
                continue
            out.append(
                {
                    "date": r["date"],
                    "time": r["time"],
                    "systolic": r["systolic"],
                    "diastolic": r["diastolic"],
                    "pulse": r["pulse"],
                }
            )
        out.sort(key=lambda x: (x["date"], x["time"]))
        return out

    def _find_duplicate_pdf(self, upload_dir: Path, file_bytes: bytes) -> Path | None:
        incoming_hash = hashlib.sha256(file_bytes).hexdigest()
        for candidate in upload_dir.glob("*.pdf"):
            try:
                existing_hash = hashlib.sha256(candidate.read_bytes()).hexdigest()
            except OSError:
                continue
            if existing_hash == incoming_hash:
                return candidate
        return None

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

    def _count_rows_in_dashboard_js(self) -> int:
        path = PROJECT_DIR / "dashboard_data.js"
        if not path.exists():
            return 0
        text = path.read_text(encoding="utf-8", errors="ignore").strip()
        if not text.startswith("window.LAB_DASH_DATA"):
            return 0
        try:
            json_part = text.split("=", 1)[1].strip()
            if json_part.endswith(";"):
                json_part = json_part[:-1].strip()
            parsed = json.loads(json_part)
            if isinstance(parsed, list):
                return len(parsed)
        except Exception:
            return 0
        return 0

    def _snapshot_outputs(self) -> dict[str, bytes]:
        snapshot: dict[str, bytes] = {}
        for name in ("dashboard_data.js", "consolidated_labs.json", "consolidated_labs.csv"):
            p = PROJECT_DIR / name
            if p.exists():
                snapshot[name] = p.read_bytes()
        return snapshot

    def _restore_outputs(self, snapshot: dict[str, bytes]) -> None:
        for name, content in snapshot.items():
            (PROJECT_DIR / name).write_bytes(content)

    def _guarded_rebuild(self, upload_dir_override: Path | None = None) -> tuple[subprocess.CompletedProcess[str], str | None]:
        before = self._snapshot_outputs()
        result = self._run_rebuild(upload_dir_override=upload_dir_override)
        if result.returncode != 0:
            self._restore_outputs(before)
            return result, "Rebuild failed"

        row_count = self._count_rows_in_dashboard_js()
        if row_count < 1:
            self._restore_outputs(before)
            return result, "Rebuild produced no valid report rows; previous dashboard restored"
        return result, None

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
        duplicate = self._find_duplicate_pdf(upload_dir, file_bytes)
        if duplicate is not None:
            self._json(
                200,
                {
                    "ok": True,
                    "duplicate": True,
                    "message": "File already uploaded",
                    "uploaded": duplicate.name,
                    "rebuild": False,
                    "upload_dir": str(upload_dir),
                },
            )
            return

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

        result, rebuild_error = self._guarded_rebuild(upload_dir_override=upload_dir)
        if rebuild_error is not None:
            self._json(
                422,
                {
                    "ok": False,
                    "error": f"PDF uploaded but {rebuild_error}",
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

    def _handle_admin_bp_upload(self) -> None:
        try:
            _fields, files = self._parse_multipart_form()
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
        if not filename.lower().endswith(".csv"):
            self._json(400, {"ok": False, "error": "Only CSV files are supported."})
            return

        incoming = self._parse_bp_csv(file_bytes, filename)
        if not incoming:
            preview = self._decode_csv_bytes(file_bytes).replace("\x00", "")[:240]
            self._json(
                422,
                {
                    "ok": False,
                    "error": "CSV parsed but no valid BP rows found.",
                    "preview": preview,
                },
            )
            return

        existing = self._read_existing_bp()
        merged, added = self._merge_bp_rows(existing, incoming)
        self._write_bp_rows(merged)
        self._json(
            200,
            {
                "ok": True,
                "uploaded_file": filename,
                "parsed_rows": len(incoming),
                "appended_rows": added,
                "total_rows": len(merged),
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
        if path == "/api/bp-data":
            self._json(200, {"ok": True, "days": 90, "rows": self._bp_last_days(90)})
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
        if self.path == "/api/admin/upload-bp-csv":
            if self._require_admin_auth():
                return
            self._handle_admin_bp_upload()
            return

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
            result, rebuild_error = self._guarded_rebuild()
            if rebuild_error is not None:
                self._json(
                    500,
                    {
                        "ok": False,
                        "error": rebuild_error,
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
