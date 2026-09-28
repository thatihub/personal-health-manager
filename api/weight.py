""" Weight entries, DEXA/body-scan records, and the home-page health context. """
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
import weight_tracking
import health_storage

from api import config


class WeightMixin:
        def _parse_iso_date(self, raw: str) -> str | None:
            text = (raw or "").strip()
            if not text:
                return None
            for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
                try:
                    return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
                except ValueError:
                    continue
            return None

        def _to_float(self, raw: object) -> float | None:
            if raw is None:
                return None
            txt = str(raw).strip().replace(",", "")
            if not txt:
                return None
            m = re.search(r"-?\d+(?:\.\d+)?", txt)
            if not m:
                return None
            try:
                return float(m.group(0))
            except ValueError:
                return None

        def _load_json_array(self, path: Path) -> list[dict[str, object]]:
            if not path.exists():
                return []
            try:
                parsed = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(parsed, list):
                    return [x for x in parsed if isinstance(x, dict)]
            except Exception:
                return []
            return []

        def _weight_context(self, rows=None):
            reference = None
            reference_path = config.PROJECT_DIR / "home_weight_reference.js"
            if reference_path.exists():
                text = reference_path.read_text(encoding="utf-8")
                weight = re.search(r"weight_lb:\s*([0-9.]+)", text)
                date = re.search(r'date:\s*"([0-9-]+)"', text)
                if weight and date:
                    reference = {"weight_lb": float(weight.group(1)), "date": date.group(1), "source": "User-reported home weight (legacy reference)"}
            if rows is None:
                try:
                    rows = weight_tracking.load_entries(config.WEIGHT_DATA_PATH)
                except (ValueError, OSError, TypeError, KeyError):
                    # Keep the existing lab dashboard available if the weight file is damaged.
                    return {"error": "Weight data could not be read; existing file was preserved."}
            try:
                scans = self._load_dexa_data()
            except (ValueError, OSError):
                return {"error": "Scan data could not be read. Existing records and cached scans were preserved."}
            return weight_tracking.health_context(rows, scans, reference=reference)

        def _handle_weight_save(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 16384:
                    raise ValueError("Invalid weight entry size.")
                payload = json.loads(self.rfile.read(length))
                health_storage.require_durable(config.HEALTH_DATA_DIR, config.HOSTED_RENDER)
                with config.WEIGHT_LOCK:
                    rows = weight_tracking.load_entries(config.WEIGHT_DATA_PATH)
                    rows = weight_tracking.save_entry(rows, payload)
                    self._write_json_file(config.WEIGHT_DATA_PATH, rows)
                self._json(200, {"ok": True})
            except FileExistsError as error:
                self._json(409, {"ok": False, "error": str(error)})
            except LookupError as error:
                self._json(404, {"ok": False, "error": str(error)})
            except (ValueError, TypeError, UnicodeDecodeError) as error:
                self._json(400, {"ok": False, "error": str(error)})
            except OSError as error:
                self._json(503, {"ok": False, "error": str(error) or "Could not save weight file. Existing entries were preserved."})

        def _load_dexa_data(self) -> list[dict[str, object]]:
            return health_storage.load_scans(config.DEXA_DATA_PATH)

        def _handle_admin_save_dexa_record(self) -> None:
            try:
                health_storage.require_durable(config.HEALTH_DATA_DIR, config.HOSTED_RENDER)
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 65536:
                    raise ValueError("Invalid scan record size")
                payload = json.loads(self.rfile.read(length))
                with config.SCAN_LOCK:
                    records = self._load_dexa_data()
                    updated = health_storage.merge_scan(records, payload)
                    health_storage.save_scans(config.DEXA_DATA_PATH, updated)
                self._json(200, {"ok": True, "date": payload["date"]})
            except (ValueError, TypeError, UnicodeDecodeError) as error:
                self._json(400, {"ok": False, "error": str(error)})
            except OSError as error:
                self._json(503, {"ok": False, "error": str(error)})
