""" Core responses: status payloads, JSON/file writers, env overrides, DOB helpers. """
from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
import phenoage

from api import config


class CoreMixin:
        def _history_payload(self) -> dict[str, object]:
            payload: dict[str, object] = {"ok": True, "commits": [], "tags": []}
            try:
                commits = subprocess.run(
                    ["git", "log", "--date=short", "--pretty=format:%h|%ad|%s", "-n", "80"],
                    cwd=config.PROJECT_DIR,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if commits.returncode == 0:
                    lines = [ln.strip() for ln in commits.stdout.splitlines() if ln.strip()]
                    parsed = []
                    for ln in lines:
                        parts = ln.split("|", 2)
                        if len(parts) != 3:
                            continue
                        parsed.append({"hash": parts[0], "date": parts[1], "subject": parts[2]})
                    payload["commits"] = parsed
            except Exception:
                payload["commits"] = []
    
            try:
                tags = subprocess.run(
                    ["git", "tag", "--list", "--sort=-creatordate"],
                    cwd=config.PROJECT_DIR,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if tags.returncode == 0:
                    payload["tags"] = [t.strip() for t in tags.stdout.splitlines() if t.strip()]
            except Exception:
                payload["tags"] = []
    
            return payload

        def _last_updated_payload(self) -> dict[str, object]:
            override_sources = []
            if config._decode_env_text("VAULT_DATA_JSON", "VAULT_DATA_JSON_B64") is not None:
                override_sources.append("vault_data.json")
            if config._decode_env_text("DASHBOARD_DATA_JSON", "DASHBOARD_DATA_JSON_B64") is not None:
                override_sources.append("dashboard_data.js")
            if config._decode_env_text("CONSOLIDATED_LABS_JSON", "CONSOLIDATED_LABS_JSON_B64") is not None:
                override_sources.append("consolidated_labs.json")
    
            files = [
                ("Consolidated Labs", config.PROJECT_DIR / "consolidated_labs.json"),
                ("Dashboard Data", config.PROJECT_DIR / "dashboard_data.js"),
                ("Vault Data", config.PROJECT_DIR / "vault_data.json"),
                ("DEXA Records", config.DEXA_DATA_PATH),
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

        def _json(self, status: int, payload: dict[str, object]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _write_json_file(self, path: Path, payload: object) -> None:
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)

        def _write_dashboard_js(self, payload: object) -> None:
            path = config.PROJECT_DIR / "dashboard_data.js"
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(f"window.LAB_DASH_DATA = {json.dumps(payload, ensure_ascii=False)};\n", encoding="utf-8")
            tmp.replace(path)

        def _write_vault_js(self, payload: object) -> None:
            path = config.PROJECT_DIR / "vault_data.js"
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(f"window.VAULT_DATA = {json.dumps(payload, indent=2, ensure_ascii=False)};\n", encoding="utf-8")
            tmp.replace(path)

        def _write_medicine_js(self, payload: object) -> None:
            path = config.PROJECT_DIR / "medicine_data.js"
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(f"window.MEDICINE_DATA = {json.dumps(payload, indent=2, ensure_ascii=False)};\n", encoding="utf-8")
            tmp.replace(path)

        def _serve_env_override(self, path: str) -> bool:
            if path == "/vault_data.json":
                payload = config._decode_env_text("VAULT_DATA_JSON", "VAULT_DATA_JSON_B64")
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
                payload = config._decode_env_text("DASHBOARD_DATA_JSON", "DASHBOARD_DATA_JSON_B64")
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
                payload = config._decode_env_text("CONSOLIDATED_LABS_JSON", "CONSOLIDATED_LABS_JSON_B64")
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
                return config._decode_env_text("VAULT_DATA_JSON", "VAULT_DATA_JSON_B64") is not None
            if target == "dashboard":
                return config._decode_env_text("DASHBOARD_DATA_JSON", "DASHBOARD_DATA_JSON_B64") is not None
            if target == "consolidated":
                return config._decode_env_text("CONSOLIDATED_LABS_JSON", "CONSOLIDATED_LABS_JSON_B64") is not None
            return False

        def _date_of_birth(self):
            try:
                override = config._decode_env_text("VAULT_DATA_JSON", "VAULT_DATA_JSON_B64")
                vault = json.loads(override if override is not None else (config.PROJECT_DIR / "vault_data.json").read_text(encoding="utf-8"))
                scan = vault.get("scan_2026", {})
                summary = str(scan.get("kaiser_permanent_summary") or scan.get("kaiser_permanente_summary") or "")
                match = re.search(r"Date of birth:\s*([0-9]{1,2}/[0-9]{1,2}/[0-9]{4})", summary, flags=re.I)
                if match:
                    dob = datetime.strptime(match.group(1), "%m/%d/%Y").date()
                    return dob if dob <= datetime.now().date() else None
            except (ValueError, OSError, TypeError, AttributeError):
                pass
            return None

        def _chronological_age(self):
            return phenoage.age_on(self._date_of_birth(), datetime.now().date())
