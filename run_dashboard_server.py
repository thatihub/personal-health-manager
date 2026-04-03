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
from uuid import uuid4


PROJECT_DIR = Path(__file__).resolve().parent
REBUILD_LOCK = threading.Lock()
VAULT_AUTH_USER = os.getenv("VAULT_BASIC_AUTH_USER", "").strip()
VAULT_AUTH_PASS = os.getenv("VAULT_BASIC_AUTH_PASS", "").strip()
ADMIN_AUTH_USER = os.getenv("ADMIN_BASIC_AUTH_USER", "").strip()
ADMIN_AUTH_PASS = os.getenv("ADMIN_BASIC_AUTH_PASS", "").strip()
BP_DATA_PATH = PROJECT_DIR / "bp_readings.csv"
AG_IMPORTS_PATH = PROJECT_DIR / "ag_lab_imports.json"
CONSOLIDATED_LABS_PATH = PROJECT_DIR / "consolidated_labs.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run lab dashboard locally.")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("PORT", "3002")),
        help="Port to bind (default: 3002).",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("HOST", "127.0.0.1"),
        help="Host/interface to bind (default: 127.0.0.1; use 0.0.0.0 to listen on all).",
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
    def _history_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {"ok": True, "commits": [], "tags": []}
        try:
            commits = subprocess.run(
                ["git", "log", "--date=short", "--pretty=format:%h|%ad|%s", "-n", "80"],
                cwd=PROJECT_DIR,
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
                cwd=PROJECT_DIR,
                check=False,
                capture_output=True,
                text=True,
            )
            if tags.returncode == 0:
                payload["tags"] = [t.strip() for t in tags.stdout.splitlines() if t.strip()]
        except Exception:
            payload["tags"] = []

        return payload

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
            "%b %d %Y %I:%M %p",
            "%b %d %Y %H:%M",
            "%B %d %Y %I:%M %p",
            "%B %d %Y %H:%M",
            "%Y-%m-%d",
            "%m/%d/%Y",
            "%b %d %Y",
            "%B %d %Y",
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

    def _load_ag_imports(self) -> dict[str, object]:
        if not AG_IMPORTS_PATH.exists():
            return {"reports": [], "results": []}
        try:
            parsed = json.loads(AG_IMPORTS_PATH.read_text(encoding="utf-8"))
            if not isinstance(parsed, dict):
                return {"reports": [], "results": []}
            reports = parsed.get("reports")
            results = parsed.get("results")
            return {
                "reports": reports if isinstance(reports, list) else [],
                "results": results if isinstance(results, list) else [],
            }
        except Exception:
            return {"reports": [], "results": []}

    def _save_ag_imports(self, payload: dict[str, object]) -> None:
        tmp = AG_IMPORTS_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(AG_IMPORTS_PATH)

    def _test_aliases(self) -> dict[str, str]:
        return {
            "hba1c": "hba1c",
            "a1c": "hba1c",
            "hemoglobin a1c": "hba1c",
            "glucose": "glucose_fasting",
            "fasting glucose": "glucose_fasting",
            "glucose fasting": "glucose_fasting",
            "insulin": "insulin",
            "total cholesterol": "total_cholesterol",
            "cholesterol total": "total_cholesterol",
            "ldl": "ldl",
            "ldl cholesterol": "ldl",
            "hdl": "hdl",
            "hdl cholesterol": "hdl",
            "triglycerides": "triglycerides",
            "non hdl": "non_hdl",
            "non-hdl": "non_hdl",
            "creatinine": "creatinine",
            "egfr": "egfr",
            "estimated gfr": "egfr",
            "bun": "bun",
            "urine albumin": "urine_albumin",
            "urine acr": "urine_acr",
            "acr": "urine_acr",
            "alt": "alt",
            "ast": "ast",
            "alkaline phosphatase": "alkaline_phosphatase",
            "alk phos": "alkaline_phosphatase",
            "albumin": "albumin",
            "bilirubin": "bilirubin",
            "hemoglobin": "hemoglobin",
            "hematocrit": "hematocrit",
            "wbc": "wbc",
            "white blood cell": "wbc",
            "platelets": "platelets",
            "mcv": "mcv",
            "rdw": "rdw",
            "lymphocyte %": "lymphocyte_pct",
            "lymphocyte": "lymphocyte_pct",
            "hs-crp": "hs_crp",
            "crp": "crp",
            "esr": "esr",
            "tsh": "tsh",
            "free t4": "free_t4",
            "free t3": "free_t3",
            "sodium": "sodium",
            "potassium": "potassium",
            "calcium": "calcium",
            "magnesium": "magnesium",
        }

    def _section_specs(self) -> dict[str, dict[str, object]]:
        return {
            "Glucose / Diabetes": {"weight": 20, "tests": ["glucose_fasting", "hba1c", "insulin"]},
            "Lipids": {"weight": 10, "tests": ["total_cholesterol", "ldl", "hdl", "triglycerides", "non_hdl"]},
            "Kidney": {"weight": 15, "tests": ["creatinine", "egfr", "bun", "urine_albumin", "urine_acr"]},
            "Liver / Protein": {
                "weight": 10,
                "tests": ["alt", "ast", "alkaline_phosphatase", "albumin", "bilirubin"],
            },
            "CBC / Blood Aging": {
                "weight": 15,
                "tests": ["hemoglobin", "hematocrit", "wbc", "platelets", "mcv", "rdw", "lymphocyte_pct"],
            },
            "Inflammation": {"weight": 15, "tests": ["hs_crp", "crp", "esr"]},
            "Thyroid": {"weight": 10, "tests": ["tsh", "free_t4", "free_t3"]},
            "Electrolytes / Minerals": {"weight": 5, "tests": ["sodium", "potassium", "calcium", "magnesium"]},
        }

    def _section_for_test(self, standard_name: str) -> str:
        for section, spec in self._section_specs().items():
            tests = spec.get("tests", [])
            if standard_name in tests:
                return section
        return "Other"

    def _standard_unit_for(self, standard_name: str) -> str:
        unit_map = {
            "hba1c": "%",
            "glucose_fasting": "mg/dL",
            "insulin": "uIU/mL",
            "total_cholesterol": "mg/dL",
            "ldl": "mg/dL",
            "hdl": "mg/dL",
            "triglycerides": "mg/dL",
            "non_hdl": "mg/dL",
            "creatinine": "mg/dL",
            "egfr": "mL/min/1.73m2",
            "bun": "mg/dL",
            "urine_albumin": "mg/L",
            "urine_acr": "mg/g",
            "alt": "U/L",
            "ast": "U/L",
            "alkaline_phosphatase": "U/L",
            "albumin": "g/dL",
            "bilirubin": "mg/dL",
            "hemoglobin": "g/dL",
            "hematocrit": "%",
            "wbc": "10^3/uL",
            "platelets": "10^3/uL",
            "mcv": "fL",
            "rdw": "%",
            "lymphocyte_pct": "%",
            "hs_crp": "mg/L",
            "crp": "mg/L",
            "esr": "mm/h",
            "tsh": "uIU/mL",
            "free_t4": "ng/dL",
            "free_t3": "pg/mL",
            "sodium": "mmol/L",
            "potassium": "mmol/L",
            "calcium": "mg/dL",
            "magnesium": "mg/dL",
        }
        return unit_map.get(standard_name, "")

    def _normalize_test_name(self, raw_name: str) -> str:
        key = re.sub(r"\s+", " ", (raw_name or "").strip().lower())
        aliases = self._test_aliases()
        if key in aliases:
            return aliases[key]
        for alias, standard in aliases.items():
            if alias in key:
                return standard
        return re.sub(r"[^a-z0-9]+", "_", key).strip("_") or "unknown_test"

    def _convert_to_standard_unit(self, standard_name: str, value: float, unit: str) -> tuple[float, str, str | None]:
        u = (unit or "").strip().lower()
        std_unit = self._standard_unit_for(standard_name) or unit
        note = None
        if standard_name == "glucose_fasting" and u in {"mmol/l", "mmol/liter"}:
            return round(value * 18.0, 2), "mg/dL", "Converted from mmol/L to mg/dL"
        if standard_name == "creatinine" and u in {"umol/l", "µmol/l"}:
            return round(value / 88.4, 3), "mg/dL", "Converted from umol/L to mg/dL"
        return value, std_unit, note

    def _normalize_import_row(
        self,
        raw: dict[str, object],
        default_date: str,
        source: str,
        source_type: str,
        report_id: str,
    ) -> dict[str, object] | None:
        name = str(raw.get("test") or raw.get("test_name") or raw.get("name") or "").strip()
        if not name:
            return None
        value = self._to_float(raw.get("value"))
        if value is None:
            return None
        original_unit = str(raw.get("unit") or "").strip()
        standard_name = self._normalize_test_name(name)
        converted, standard_unit, conversion_note = self._convert_to_standard_unit(standard_name, value, original_unit)
        date = (
            self._parse_iso_date(str(raw.get("collection_date") or ""))
            or self._parse_iso_date(str(raw.get("date") or ""))
            or default_date
        )
        ref_low = self._to_float(raw.get("reference_range_low") or raw.get("ref_low"))
        ref_high = self._to_float(raw.get("reference_range_high") or raw.get("ref_high"))
        flag = str(raw.get("flag") or "").strip().lower()
        if not flag and ref_low is not None and converted < ref_low:
            flag = "low"
        if not flag and ref_high is not None and converted > ref_high:
            flag = "high"
        if not flag:
            flag = "normal"
        notes = str(raw.get("notes") or "").strip()
        if conversion_note:
            notes = f"{notes} | {conversion_note}".strip(" |")
        return {
            "id": f"res_{uuid4().hex[:10]}",
            "report_id": report_id,
            "collection_date": date,
            "original_test_name": name,
            "standard_test_name": standard_name,
            "value_numeric": converted,
            "value_text": str(raw.get("value") or ""),
            "original_unit": original_unit,
            "standard_unit": standard_unit,
            "reference_range_low": ref_low,
            "reference_range_high": ref_high,
            "flag": flag,
            "section_name": self._section_for_test(standard_name),
            "lab_source": source,
            "source_type": source_type,
            "specimen_type": str(raw.get("specimen_type") or "").strip(),
            "parser_confidence": 0.9,
            "notes": notes,
        }

    def _parse_import_text(self, text: str) -> list[dict[str, object]]:
        cleaned = (text or "").strip()
        if not cleaned:
            return []
        first_line = cleaned.splitlines()[0].lower()
        if "," in first_line and ("test" in first_line or "name" in first_line):
            reader = csv.DictReader(io.StringIO(cleaned))
            return [dict(r) for r in reader if r]
        if "\t" in first_line and ("test" in first_line or "name" in first_line):
            reader = csv.DictReader(io.StringIO(cleaned), delimiter="\t")
            return [dict(r) for r in reader if r]

        rows: list[dict[str, object]] = []
        for line in cleaned.splitlines():
            t = line.strip()
            if not t or t.startswith("#"):
                continue
            parts = [p.strip() for p in re.split(r"\||\t", t) if p.strip()]
            if len(parts) >= 2:
                row = {"test": parts[0], "value": parts[1]}
                if len(parts) >= 3:
                    row["unit"] = parts[2]
                if len(parts) >= 4:
                    row["collection_date"] = parts[3]
                rows.append(row)
                continue
            m = re.match(
                r"^(?P<test>[A-Za-z0-9 %/().+\-]+?)\s*[:=-]\s*(?P<value>-?\d+(?:\.\d+)?)\s*(?P<unit>[A-Za-z/%^0-9.]+)?\s*$",
                t,
            )
            if m:
                rows.append(
                    {
                        "test": m.group("test").strip(),
                        "value": m.group("value"),
                        "unit": (m.group("unit") or "").strip(),
                    }
                )
        return rows

    def _read_consolidated_reports(self) -> list[dict[str, object]]:
        return self._load_json_array(CONSOLIDATED_LABS_PATH)

    def _normalized_results_from_consolidated(self) -> list[dict[str, object]]:
        key_map = {
            "glucose_mg_dl": ("glucose_fasting", "mg/dL"),
            "hba1c_pct": ("hba1c", "%"),
            "total_chol_mg_dl": ("total_cholesterol", "mg/dL"),
            "ldl_mg_dl": ("ldl", "mg/dL"),
            "hdl_mg_dl": ("hdl", "mg/dL"),
            "triglycerides_mg_dl": ("triglycerides", "mg/dL"),
            "creatinine_mg_dl": ("creatinine", "mg/dL"),
            "egfr_ml_min_1_73": ("egfr", "mL/min/1.73m2"),
            "bun_mg_dl": ("bun", "mg/dL"),
            "urine_acr_mg_g": ("urine_acr", "mg/g"),
            "alt_u_l": ("alt", "U/L"),
            "ast_u_l": ("ast", "U/L"),
            "alk_phos_u_l": ("alkaline_phosphatase", "U/L"),
            "albumin_g_dl": ("albumin", "g/dL"),
            "bilirubin_mg_dl": ("bilirubin", "mg/dL"),
            "tsh_uiu_ml": ("tsh", "uIU/mL"),
            "free_t4_ng_dl": ("free_t4", "ng/dL"),
            "sodium_mmol_l": ("sodium", "mmol/L"),
            "potassium_mmol_l": ("potassium", "mmol/L"),
            "calcium_mg_dl": ("calcium", "mg/dL"),
        }
        rows: list[dict[str, object]] = []
        for report in self._read_consolidated_reports():
            collection_date = self._parse_iso_date(str(report.get("date") or "")) or ""
            source = str(report.get("source") or "Unknown")
            report_id = f"con_{hashlib.sha1((str(report.get('report_file')) + collection_date).encode()).hexdigest()[:10]}"
            for k, (standard_name, unit) in key_map.items():
                value = self._to_float(report.get(k))
                if value is None:
                    continue
                rows.append(
                    {
                        "id": f"res_{uuid4().hex[:10]}",
                        "report_id": report_id,
                        "collection_date": collection_date,
                        "original_test_name": k,
                        "standard_test_name": standard_name,
                        "value_numeric": value,
                        "value_text": str(value),
                        "original_unit": unit,
                        "standard_unit": unit,
                        "reference_range_low": None,
                        "reference_range_high": None,
                        "flag": "normal",
                        "section_name": self._section_for_test(standard_name),
                        "lab_source": source,
                        "source_type": "consolidated",
                        "specimen_type": "",
                        "parser_confidence": 1.0,
                        "notes": str(report.get("notes") or ""),
                    }
                )
        return rows

    def _score_by_bands(self, value: float, bands: list[tuple[float, float, int]]) -> int:
        for low, high, score in bands:
            if low <= value <= high:
                return score
        if value < bands[0][0]:
            return bands[0][2]
        return bands[-1][2]

    def _score_marker(self, marker: str, value: float) -> int:
        if marker == "hba1c":
            return self._score_by_bands(value, [(0.0, 5.4, 100), (5.5, 5.6, 92), (5.7, 5.9, 80), (6.0, 6.4, 65), (6.5, 6.9, 45), (7.0, 20.0, 25)])
        if marker == "hdl":
            return self._score_by_bands(value, [(0.0, 39.9, 40), (40.0, 49.9, 65), (50.0, 59.9, 85), (60.0, 200.0, 100)])
        if marker == "ldl":
            return self._score_by_bands(value, [(0.0, 79.0, 100), (80.0, 99.0, 85), (100.0, 129.0, 65), (130.0, 159.0, 40), (160.0, 300.0, 20)])
        if marker == "glucose_fasting":
            return self._score_by_bands(value, [(0.0, 99.0, 95), (100.0, 109.0, 82), (110.0, 125.0, 65), (126.0, 400.0, 40)])
        if marker == "triglycerides":
            return self._score_by_bands(value, [(0.0, 99.0, 95), (100.0, 149.0, 82), (150.0, 199.0, 65), (200.0, 800.0, 40)])
        if marker == "egfr":
            return self._score_by_bands(value, [(0.0, 59.0, 35), (60.0, 74.0, 62), (75.0, 89.0, 82), (90.0, 180.0, 95)])
        if marker == "creatinine":
            return self._score_by_bands(value, [(0.0, 0.79, 82), (0.8, 1.2, 95), (1.21, 1.5, 70), (1.51, 9.0, 35)])
        if marker == "urine_acr":
            return self._score_by_bands(value, [(0.0, 29.0, 95), (30.0, 99.0, 62), (100.0, 5000.0, 30)])
        if marker == "albumin":
            return self._score_by_bands(value, [(0.0, 3.49, 50), (3.5, 5.1, 95), (5.11, 8.0, 65)])
        if marker == "alt" or marker == "ast":
            return self._score_by_bands(value, [(0.0, 40.0, 92), (40.1, 80.0, 70), (80.1, 600.0, 35)])
        if marker == "tsh":
            return self._score_by_bands(value, [(0.0, 0.39, 55), (0.4, 2.5, 95), (2.51, 4.5, 75), (4.51, 9.0, 45), (9.01, 100.0, 20)])
        if marker in {"sodium", "potassium", "calcium"}:
            return 90
        if marker in {"crp", "hs_crp"}:
            return self._score_by_bands(value, [(0.0, 1.0, 95), (1.01, 3.0, 75), (3.01, 100.0, 40)])
        return 80

    def _trend_direction(self, marker: str, previous: float, current: float) -> int:
        if marker in {"hdl", "egfr"}:
            return 1 if current > previous else (-1 if current < previous else 0)
        if marker in {"glucose_fasting", "hba1c", "ldl", "triglycerides", "crp", "hs_crp", "esr", "tsh", "urine_acr"}:
            return 1 if current < previous else (-1 if current > previous else 0)
        # Middle-is-best markers: give neutral trend for now.
        return 0

    def _section_color(self, score: float | None) -> str:
        if score is None:
            return "gray"
        if score >= 85:
            return "green"
        if score >= 70:
            return "yellow"
        if score >= 55:
            return "orange"
        return "red"

    def _age_delta_from_score(self, score: float) -> float:
        # score -> age delta years
        points = [
            (50.0, 12.0),
            (55.0, 9.0),
            (60.0, 6.0),
            (65.0, 3.0),
            (70.0, 0.0),
            (75.0, -3.0),
            (80.0, -5.0),
            (85.0, -7.0),
            (90.0, -9.0),
            (95.0, -12.0),
        ]
        if score <= points[0][0]:
            return points[0][1]
        if score >= points[-1][0]:
            return points[-1][1]
        for (s1, d1), (s2, d2) in zip(points, points[1:]):
            if s1 <= score <= s2:
                ratio = (score - s1) / (s2 - s1)
                return d1 + ratio * (d2 - d1)
        return 0.0

    def _chronological_age(self) -> int:
        dob = None
        try:
            vault = json.loads((PROJECT_DIR / "vault_data.json").read_text(encoding="utf-8"))
            scan = vault.get("scan_2026", {})
            kaiser = str(scan.get("kaiser_permanent_summary") or scan.get("kaiser_permanente_summary") or "")
            m = re.search(r"Date of birth:\s*([0-9]{1,2}/[0-9]{1,2}/[0-9]{4})", kaiser, flags=re.I)
            if m:
                dob = datetime.strptime(m.group(1), "%m/%d/%Y").date()
        except Exception:
            dob = None
        if dob is None:
            return 65
        today = datetime.now().date()
        return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

    def _compute_ag_snapshot(self) -> dict[str, object]:
        imports = self._load_ag_imports()
        imported_results = [r for r in imports.get("results", []) if isinstance(r, dict)]
        all_results = self._normalized_results_from_consolidated() + imported_results
        all_results = [r for r in all_results if r.get("collection_date")]
        all_results.sort(key=lambda r: (str(r.get("collection_date")), str(r.get("standard_test_name"))))

        sections = self._section_specs()
        section_scores: list[dict[str, object]] = []
        section_score_map: dict[str, float] = {}
        for section_name, spec in sections.items():
            tests = set(spec.get("tests", []))
            by_marker: dict[str, list[dict[str, object]]] = {}
            for row in all_results:
                marker = str(row.get("standard_test_name") or "")
                if marker not in tests:
                    continue
                by_marker.setdefault(marker, []).append(row)

            marker_scores: list[float] = []
            trend_marks: list[int] = []
            for marker, rows in by_marker.items():
                rows.sort(key=lambda r: str(r.get("collection_date")))
                latest_val = self._to_float(rows[-1].get("value_numeric"))
                if latest_val is None:
                    continue
                marker_scores.append(float(self._score_marker(marker, latest_val)))
                if len(rows) >= 2:
                    prev = self._to_float(rows[-2].get("value_numeric"))
                    cur = self._to_float(rows[-1].get("value_numeric"))
                    if prev is not None and cur is not None:
                        trend_marks.append(self._trend_direction(marker, prev, cur))

            section_score = None
            trend_arrow = "flat"
            if marker_scores:
                base = sum(marker_scores) / len(marker_scores)
                trend_adj = (sum(trend_marks) / len(trend_marks)) * 3 if trend_marks else 0.0
                section_score = max(0.0, min(100.0, base + trend_adj))
                section_score_map[section_name] = section_score
                if trend_marks:
                    total_trend = sum(trend_marks)
                    trend_arrow = "up" if total_trend > 0 else ("down" if total_trend < 0 else "flat")
            section_scores.append(
                {
                    "section_name": section_name,
                    "score": round(section_score, 1) if section_score is not None else None,
                    "trend": trend_arrow if section_score is not None else "na",
                    "border_color": self._section_color(section_score),
                    "markers_present": len(marker_scores),
                    "markers_total": len(tests),
                    "weight": spec.get("weight", 0),
                }
            )

        weighted = 0.0
        weight_total = 0.0
        missing_sections = 0
        for entry in section_scores:
            score = entry["score"]
            weight = float(entry["weight"])
            if score is None:
                missing_sections += 1
                continue
            weighted += float(score) * weight
            weight_total += weight
        overall_score = (weighted / weight_total) if weight_total else 0.0
        overall_score = max(0.0, min(100.0, overall_score - (missing_sections * 1.5)))

        latest_date = ""
        if all_results:
            latest_date = str(all_results[-1].get("collection_date") or "")
        latest_rows = [r for r in all_results if str(r.get("collection_date") or "") == latest_date]
        latest_markers = {str(r.get("standard_test_name") or "") for r in latest_rows}
        core_groups = [
            {"hba1c", "glucose_fasting"},
            {"creatinine", "egfr"},
            {"albumin"},
            {"hemoglobin", "hematocrit", "wbc", "platelets"},
            {"ldl", "hdl", "triglycerides", "total_cholesterol"},
            {"hs_crp", "crp", "esr"},
        ]
        core_hits = sum(1 for group in core_groups if latest_markers.intersection(group))
        confidence = "high" if core_hits >= 6 else ("medium" if core_hits >= 4 else "low")

        chronological_age = self._chronological_age()
        age_delta = self._age_delta_from_score(overall_score)
        bio_age = chronological_age + age_delta

        # Year-by-year timeline
        by_date: dict[str, list[dict[str, object]]] = {}
        for row in all_results:
            d = str(row.get("collection_date") or "")
            by_date.setdefault(d, []).append(row)
        timeline: list[dict[str, object]] = []
        for d in sorted(by_date.keys()):
            src = sorted({str(r.get("lab_source") or "Unknown") for r in by_date[d]})
            timeline.append(
                {
                    "collection_date": d,
                    "lab_sources": src,
                    "tests_count": len(by_date[d]),
                }
            )

        return {
            "ok": True,
            "latest_date": latest_date,
            "chronological_age": chronological_age,
            "overall_score": round(overall_score, 1),
            "estimated_bio_age": round(bio_age, 1),
            "age_delta_years": round(age_delta, 1),
            "confidence": confidence,
            "section_scores": section_scores,
            "timeline": timeline,
            "normalized_results": all_results,
            "reports": imports.get("reports", []),
            "model_version": "ag-v1",
            "generated_at": datetime.now().astimezone().isoformat(),
        }

    def _is_duplicate_normalized_result(self, candidate: dict[str, object], existing: list[dict[str, object]]) -> bool:
        c_date = str(candidate.get("collection_date") or "")
        c_test = str(candidate.get("standard_test_name") or "")
        c_unit = str(candidate.get("standard_unit") or "")
        c_val = self._to_float(candidate.get("value_numeric"))
        if c_val is None:
            return False
        for row in existing:
            if str(row.get("collection_date") or "") != c_date:
                continue
            if str(row.get("standard_test_name") or "") != c_test:
                continue
            if str(row.get("standard_unit") or "") != c_unit:
                continue
            r_val = self._to_float(row.get("value_numeric"))
            if r_val is None:
                continue
            if abs(r_val - c_val) <= 0.0001:
                return True
        return False

    def _handle_ag_import_preview(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json(400, {"ok": False, "error": "Invalid JSON body"})
            return

        source = str(payload.get("source") or "Unknown").strip() or "Unknown"
        source_type = str(payload.get("source_type") or "text").strip() or "text"
        collection_date = self._parse_iso_date(str(payload.get("collection_date") or "")) or datetime.now().strftime("%Y-%m-%d")
        report_id = f"preview_{uuid4().hex[:8]}"
        rows_raw = payload.get("rows")
        if isinstance(rows_raw, list):
            parsed_rows = [r for r in rows_raw if isinstance(r, dict)]
        else:
            parsed_rows = self._parse_import_text(str(payload.get("text") or ""))

        normalized: list[dict[str, object]] = []
        uncertain: list[dict[str, object]] = []
        for row in parsed_rows:
            item = self._normalize_import_row(row, collection_date, source, source_type, report_id)
            if item is None:
                uncertain.append({"row": row, "reason": "Could not parse test/value"})
                continue
            if item["standard_test_name"] == "unknown_test":
                uncertain.append({"row": row, "reason": "Unknown test mapping"})
            normalized.append(item)

        existing = self._normalized_results_from_consolidated() + [
            r for r in self._load_ag_imports().get("results", []) if isinstance(r, dict)
        ]
        duplicates = [r for r in normalized if self._is_duplicate_normalized_result(r, existing)]
        self._json(
            200,
            {
                "ok": True,
                "preview_report_id": report_id,
                "rows_found": len(parsed_rows),
                "normalized_rows": normalized,
                "uncertain_rows": uncertain,
                "likely_duplicates": duplicates,
            },
        )

    def _handle_ag_import_commit(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json(400, {"ok": False, "error": "Invalid JSON body"})
            return
        rows = payload.get("normalized_rows")
        if not isinstance(rows, list) or not rows:
            self._json(400, {"ok": False, "error": "normalized_rows is required"})
            return

        source = str(payload.get("source") or "Unknown").strip() or "Unknown"
        source_type = str(payload.get("source_type") or "text").strip() or "text"
        file_name = str(payload.get("file_name") or "").strip()
        collection_date = self._parse_iso_date(str(payload.get("collection_date") or "")) or datetime.now().strftime("%Y-%m-%d")
        report_id = f"imp_{uuid4().hex[:10]}"
        now_iso = datetime.now().astimezone().isoformat()

        store = self._load_ag_imports()
        existing_results = [r for r in store.get("results", []) if isinstance(r, dict)]
        accepted: list[dict[str, object]] = []
        skipped = 0
        for row in rows:
            if not isinstance(row, dict):
                skipped += 1
                continue
            row = dict(row)
            row["report_id"] = report_id
            row["lab_source"] = source
            row["source_type"] = source_type
            row["collection_date"] = self._parse_iso_date(str(row.get("collection_date") or "")) or collection_date
            if self._is_duplicate_normalized_result(row, self._normalized_results_from_consolidated() + existing_results + accepted):
                skipped += 1
                continue
            accepted.append(row)

        report = {
            "id": report_id,
            "source": source,
            "source_type": source_type,
            "file_name": file_name,
            "upload_date": now_iso,
            "collection_date": collection_date,
            "parser_status": "reviewed",
            "confidence_score": 0.9,
            "raw_text": str(payload.get("raw_text") or ""),
            "metadata_json": {
                "rows_submitted": len(rows),
                "rows_saved": len(accepted),
                "rows_skipped": skipped,
            },
        }
        store_reports = [r for r in store.get("reports", []) if isinstance(r, dict)]
        store_results = existing_results + accepted
        store_reports.append(report)
        self._save_ag_imports({"reports": store_reports, "results": store_results})
        self._json(
            200,
            {
                "ok": True,
                "report_id": report_id,
                "saved_rows": len(accepted),
                "skipped_rows": skipped,
            },
        )

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
            # Dev/local mode: if admin creds are not configured, allow access.
            # When creds are configured, Basic Auth is enforced below.
            return False
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
        if path == "/api/ag-dashboard-data":
            self._json(200, self._compute_ag_snapshot())
            return
        if path == "/api/history":
            self._json(200, self._history_payload())
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
        if self.path == "/api/ag-import/preview":
            if self._require_admin_auth():
                return
            self._handle_ag_import_preview()
            return

        if self.path == "/api/ag-import/commit":
            if self._require_admin_auth():
                return
            self._handle_ag_import_commit()
            return

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
    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    print(
        f"Personal Health Manager running at http://{args.host}:{args.port}/index.html"
    )
    print("Press Ctrl+C to stop.")
    server.serve_forever()


if __name__ == "__main__":
    main()
