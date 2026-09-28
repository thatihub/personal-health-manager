""" Doctor notes and medication records. """
from __future__ import annotations

import json
from datetime import datetime, timedelta
from uuid import uuid4

from api import config


class RecordsMixin:
        def _doctor_notes_payload(self) -> dict[str, object]:
            path = config.PROJECT_DIR / "doctor_notes.json"
            if not path.exists():
                return {"current": None, "archived": []}
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {"current": None, "archived": []}
            if not isinstance(payload, dict):
                return {"current": None, "archived": []}
            current = payload.get("current")
            archived = payload.get("archived", [])
            return {
                "current": current if isinstance(current, dict) else None,
                "archived": archived if isinstance(archived, list) else [],
            }

        def _medicine_payload(self) -> dict[str, object]:
            if not config.MEDICINE_DATA_PATH.exists():
                return {"medications": [], "updated_at": ""}
            try:
                payload = json.loads(config.MEDICINE_DATA_PATH.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {"medications": [], "updated_at": ""}
            if not isinstance(payload, dict) or not isinstance(payload.get("medications", []), list):
                return {"medications": [], "updated_at": ""}
            return payload

        def _handle_medicine_save(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 1_000_000:
                self._json(413, {"ok": False, "error": "Medication data is too large"})
                return
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._json(400, {"ok": False, "error": "Invalid JSON body"})
                return
            medicines = payload.get("medications") if isinstance(payload, dict) else None
            if not isinstance(medicines, list) or len(medicines) > 500:
                self._json(400, {"ok": False, "error": "Medications must be a list of no more than 500 records"})
                return
            allowed = {"id", "name", "rx_number", "dose", "schedule", "type", "notes"}
            cleaned: list[dict[str, str]] = []
            for item in medicines:
                if not isinstance(item, dict) or not str(item.get("name", "")).strip():
                    self._json(400, {"ok": False, "error": "Every medication needs a name"})
                    return
                record = {key: str(item.get(key, ""))[:1000].strip() for key in allowed}
                record["id"] = record["id"] or str(uuid4())
                cleaned.append(record)
            saved = {"medications": cleaned, "updated_at": datetime.now().astimezone().isoformat()}
            self._write_json_file(config.MEDICINE_DATA_PATH, saved)
            self._write_medicine_js(saved)
            self._json(200, {"ok": True, **saved})

        def _handle_doctor_notes_save(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 2_000_000:
                self._json(413, {"ok": False, "error": "Notes file is too large"})
                return
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._json(400, {"ok": False, "error": "Invalid JSON body"})
                return
    
            if not isinstance(payload, dict):
                self._json(400, {"ok": False, "error": "Notes data must be an object"})
                return
            current = payload.get("current")
            archived = payload.get("archived", [])
            if current is not None and not isinstance(current, dict):
                self._json(400, {"ok": False, "error": "Current note must be an object or null"})
                return
            if not isinstance(archived, list) or any(not isinstance(note, dict) for note in archived):
                self._json(400, {"ok": False, "error": "Archived notes must be a list"})
                return
            if len(archived) > 500:
                self._json(400, {"ok": False, "error": "Archive limit is 500 notes"})
                return
    
            allowed = {"id", "doctor", "appointment_date", "title", "content", "updated_at", "archived_at"}
            def clean_note(note: dict[str, object]) -> dict[str, str]:
                cleaned: dict[str, str] = {}
                for key in allowed:
                    value = note.get(key, "")
                    if value is not None:
                        cleaned[key] = str(value)[:100_000 if key == "content" else 500]
                return cleaned
    
            saved = {
                "current": clean_note(current) if current is not None else None,
                "archived": [clean_note(note) for note in archived],
            }
            self._write_json_file(config.PROJECT_DIR / "doctor_notes.json", saved)
            self._json(200, {"ok": True})
