""" Blood-pressure CSV import, merge, and 90-day queries. """
from __future__ import annotations

import csv
import io
import re
from datetime import datetime, timedelta
from pathlib import Path

from api import config


class BPMixin:
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
            if not config.BP_DATA_PATH.exists():
                return []
            with config.BP_DATA_PATH.open("r", encoding="utf-8", newline="") as f:
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
            with config.BP_DATA_PATH.open("w", encoding="utf-8", newline="") as f:
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
            self._export_static_files()
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
