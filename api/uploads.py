""" Admin uploads: multipart parsing, PDF intake, and the guarded rebuild pipeline. """
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
import health_storage

from api import config


class UploadsMixin:
        def _find_duplicate_pdf(self, upload_dir: Path, file_bytes: bytes) -> Path | None:
            incoming_hash = hashlib.sha256(file_bytes).hexdigest()
            for candidate in upload_dir.rglob("*.pdf"):
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
            if target not in {"vault", "dashboard", "consolidated", "dexa"}:
                self._json(400, {"ok": False, "error": "target must be one of: vault, dashboard, consolidated, dexa"})
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
                    self._write_json_file(config.PROJECT_DIR / "vault_data.json", content)
                    self._write_vault_js(content)
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
                    self._write_json_file(config.PROJECT_DIR / "consolidated_labs.json", content)
                    self._json(200, {"ok": True, "target": "consolidated_labs.json"})
                    return
    
                if target == "dexa":
                    if not isinstance(content, list):
                        raise ValueError("DEXA content must be a JSON array")
                    health_storage.require_durable(config.HEALTH_DATA_DIR, config.HOSTED_RENDER)
                    if any(not isinstance(row, dict) or not row.get("date") for row in content):
                        raise ValueError("Each scan must have a date")
                    with config.SCAN_LOCK:
                        health_storage.save_scans(config.DEXA_DATA_PATH, content)
                    self._json(200, {"ok": True, "target": "dexa_records.json"})
                    return
            except ValueError as exc:
                self._json(400, {"ok": False, "error": str(exc)})
                return

        def _run_rebuild(self, upload_dir_override: Path | None = None) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                ["python3", str(config.PROJECT_DIR / "rebuild_consolidation.py")],
                cwd=config.PROJECT_DIR.parent,
                check=False,
                capture_output=True,
                text=True,
                env=config._rebuild_env(upload_dir_override),
            )

        def _count_rows_in_dashboard_js(self) -> int:
            path = config.PROJECT_DIR / "dashboard_data.js"
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
                p = config.PROJECT_DIR / name
                if p.exists():
                    snapshot[name] = p.read_bytes()
            return snapshot

        def _restore_outputs(self, snapshot: dict[str, bytes]) -> None:
            for name, content in snapshot.items():
                (config.PROJECT_DIR / name).write_bytes(content)

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
            
            # Successful rebuild - export latest static JS files
            self._export_static_files()
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
    
            upload_dir = config._default_upload_dir()
            upload_dir.mkdir(parents=True, exist_ok=True)
            duplicate = self._find_duplicate_pdf(upload_dir, file_bytes)
            if duplicate is not None:
                do_rebuild = str(fields.get("rebuild", "1")).strip() != "0"
                if do_rebuild:
                    result, rebuild_error = self._guarded_rebuild(upload_dir_override=upload_dir)
                    if rebuild_error is not None:
                        self._json(422, {"ok": False, "error": rebuild_error, "uploaded": duplicate.name})
                        return
                    self._json(200, {"ok": True, "duplicate": True, "uploaded": duplicate.name, "rebuild": True, "reprocessed": True})
                    return
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

        def _handle_admin_dexa_pdf_upload(self) -> None:
            import datetime
            import sys
            try:
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
                is_pdf = filename.lower().endswith(".pdf")
                is_image = filename.lower().endswith((".jpg", ".jpeg", ".png"))
                if not (is_pdf or is_image):
                    self._json(400, {"ok": False, "error": "Only PDF and JPEG/PNG image files are supported."})
                    return
    
                health_storage.require_durable(config.HEALTH_DATA_DIR, config.HOSTED_RENDER)
                # Archive inside local records folder
                env_dexa = os.getenv("DEXA_SCANS_DIR")
                if config.HEALTH_DATA_DIR != config.PROJECT_DIR:
                    dexa_scans_dir = config.HEALTH_DATA_DIR / "dexa_scans"
                elif env_dexa:
                    dexa_scans_dir = Path(env_dexa).expanduser()
                else:
                    candidates = [
                        config.PROJECT_DIR.parent / "Health prakash/Lab tests/Dexa scans",
                        Path.home() / "Documents/Health/Health prakash/Lab tests/Dexa scans",
                        config.PROJECT_DIR / "dexa_scans",
                    ]
                    dexa_scans_dir = config.PROJECT_DIR / "dexa_scans"
                    for c in candidates:
                        try:
                            if c.exists() or (c.parent.exists() and "Health prakash" in str(c)):
                                dexa_scans_dir = c
                                break
                        except Exception:
                            pass
    
                dexa_scans_dir.mkdir(parents=True, exist_ok=True)
                target = dexa_scans_dir / filename
                if target.exists():
                    stem = target.stem
                    suffix = target.suffix
                    i = 1
                    while target.exists():
                        target = dexa_scans_dir / f"{stem}_{i}{suffix}"
                        i += 1
    
                with target.open("wb") as f:
                    f.write(file_bytes)
    
                # Parse date from filename if matches format like Dexa-YYYY-MM-DD.pdf or similar
                parsed_date = ""
                # Look for YYYY-MM-DD
                m = re.search(r"(\d{4})-(\d{2})-(\d{2})", filename)
                if m:
                    parsed_date = m.group(0)
                else:
                    # Look for MM-DD-YYYY
                    m2 = re.search(r"(\d{2})-(\d{2})-(\d{4})", filename)
                    if m2:
                        parsed_date = f"{m2.group(3)}-{m2.group(1)}-{m2.group(2)}"
    
                if not parsed_date:
                    parsed_date = datetime.date.today().isoformat()
    
                # Fallback quiet OCR text parsing to extract possible numbers
                parsed_weight = None
                parsed_fat = None
                parsed_muscle = None
                parsed_bmr = None
                parsed_water = None
                
                # DEXA specific fields
                parsed_vat_area = None
                parsed_vat_mass = None
                parsed_vat_volume = None
                parsed_alm_index = None
                parsed_lean_height2_index = None
                parsed_lean_index_percentile = None
    
                try:
                    sys.path.append(str(config.PROJECT_DIR))
                    text = ""
                    if is_pdf:
                        from rebuild_consolidation import normalize_pdf_text
                        text = normalize_pdf_text(target)
                    elif is_image:
                        from rebuild_consolidation import ocr_image_bytes
                        text = ocr_image_bytes(file_bytes)
    
                    if text:
                        # Clean up the text: normalize spaces inside decimal numbers, e.g., "167. 3" -> "167.3"
                        text = re.sub(r'(\d+)\.\s+(\d+)', r'\1.\2', text)
                        # Replace things like "78. Tits" or "78. tits" -> "78.7"
                        text = re.sub(r'(\d+)\.\s*(?:tits|tlt|tit|ts|t)\b', r'\1.7', text, flags=re.I)
                        # Replace multiple spaces with a single space
                        text = " ".join(text.split())
    
                        # 1. Date extraction from text
                        date_m = re.search(r"Scan Date:\s*([A-Za-z]+)\s*(\d{1,2})[,\s]+(\d{4})", text, re.IGNORECASE)
                        if date_m:
                            months = {"jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05", "jun": "06", "jul": "07", "aug": "08", "sep": "09", "oct": "10", "nov": "11", "dec": "12"}
                            m_str = date_m.group(1)[:3].lower()
                            m_num = months.get(m_str, "01")
                            d_num = f"{int(date_m.group(2)):02d}"
                            y_num = date_m.group(3)
                            parsed_date = f"{y_num}-{m_num}-{d_num}"
    
                        # 2. Total Composition row (Fat, Lean, Total, Fat %)
                        comp_m = re.search(r"\bTotal\b\s+([\d\.]+)\s+(\d+\.\d{2})\s*(\d+\.\d{2})\s+([\d\.]+)", text, re.IGNORECASE)
                        if comp_m:
                            parsed_fat_mass = float(comp_m.group(1))
                            # Total Lean Mass is Lean + BMC
                            parsed_lean_mass = float(comp_m.group(2))
                            parsed_total_mass = float(comp_m.group(3))
                            parsed_fat = float(comp_m.group(4))
                        else:
                            # Fallback for standard fat % or InBody "Percent Bodyfat ... (53.0)"
                            fat_m = re.search(r"Percent\s*Bodyfat.{0,100}?\((\d+\.?\d*)\)", text, re.IGNORECASE)
                            if fat_m:
                                parsed_fat = float(fat_m.group(1))
                            else:
                                fat_m2 = re.search(r"(?:total body % fat|body fat %|fat %|percent body fat|percent bodyfat|fat)\s*(?:pct|percent)?\s*(?:is|:|value)?\s*(\d+\.?\d*)\s*%", text, re.IGNORECASE)
                                if fat_m2:
                                    parsed_fat = float(fat_m2.group(1))
                                else:
                                    fat_m3 = re.search(r"(?:total body % fat|body fat %|fat %|percent body fat|percent bodyfat)\s*(?:is|:|value)?\s*(\d+\.?\d*)", text, re.IGNORECASE)
                                    if fat_m3:
                                        parsed_fat = float(fat_m3.group(1))
    
                        # 3. Look for weight
                        weight_inbody_m = re.search(r"wen\s*ib\s*\d+\s*een\s*(\d+\.?\d*)", text, re.IGNORECASE)
                        if weight_inbody_m:
                            parsed_weight = float(weight_inbody_m.group(1))
                        else:
                            weight_m = re.search(r"weight\s*(?:is|:|value)?\s*(\d+\.?\d*)\s*(?:lbs|lb|kg)", text, re.IGNORECASE)
                            if weight_m:
                                parsed_weight = float(weight_m.group(1))
                            elif comp_m:
                                parsed_weight = parsed_total_mass
    
                        # 4. Look for BMR
                        bmr_m = re.search(r"(?:basal|bmr|metabolic)\s*(\d{3,4})\s*(?:kcal|calories)?", text, re.IGNORECASE)
                        if bmr_m:
                            parsed_bmr = float(bmr_m.group(1))
                        else:
                            bmr_inbody_m = re.search(r"Basal\s*Metabolic\s*Rate.{0,50}?(\d+)", text, re.IGNORECASE)
                            if bmr_inbody_m:
                                parsed_bmr = float(bmr_inbody_m.group(1))
    
                        # 5. Look for Muscle (Skeletal Muscle Mass)
                        muscle_m = re.search(r"Shela\s*Muse\s*Mas.{0,50}?(\d+\.\d+)", text, re.IGNORECASE)
                        if muscle_m:
                            parsed_muscle = float(muscle_m.group(1))
                        else:
                            muscle_m2 = re.search(r"(?:skeletal|muscle|smm)\s*(?:mass)?\s*(?:is|:|value)?\s*(\d+\.?\d*)\s*(?:lbs|lb|kg)", text, re.IGNORECASE)
                            if muscle_m2:
                                parsed_muscle = float(muscle_m2.group(1))
    
                        # 6. Look for Water
                        water_m = re.search(r"(?:Total|‘otal|otal)\s*Body\s*Water.{0,50}?(\d+\.\d+)", text, re.IGNORECASE)
                        if water_m:
                            parsed_water = float(water_m.group(1))
                        else:
                            water_m2 = re.search(r"(?:total body water|tbw|water)\s*(?:is|:|value)?\s*(\d+\.?\d*)\s*(?:lbs|lb|kg|l)?", text, re.IGNORECASE)
                            if water_m2:
                                parsed_water = float(water_m2.group(1))
    
                        # 7. Visceral Fat (VAT) metrics
                        vat_area_m = re.search(r"(?:est\.\s*)?vat\s*area\s*(?:\(cm²\))?\s*(\d+\.?\d*)", text, re.IGNORECASE)
                        if vat_area_m:
                            parsed_vat_area = float(vat_area_m.group(1))
    
                        vat_mass_m = re.search(r"(?:est\.\s*)?vat\s*mass\s*(?:\(g\))?\s*(\d+\.?\d*)", text, re.IGNORECASE)
                        if vat_mass_m:
                            parsed_vat_mass = float(vat_mass_m.group(1))
    
                        vat_vol_m = re.search(r"(?:est\.\s*)?vat\s*volume\s*(?:\(cm³\))?\s*(\d+\.?\d*)", text, re.IGNORECASE)
                        if vat_vol_m:
                            parsed_vat_volume = float(vat_vol_m.group(1))
    
                        # 8. Lean Indices
                        lean_idx_m = re.search(r"Lean/Height²\s*\(kg/m²\)\s*(\d+\.?\d*)", text, re.IGNORECASE)
                        if lean_idx_m:
                            parsed_lean_height2_index = float(lean_idx_m.group(1))
    
                        lean_idx_all_m = re.search(r"Lean/Height²\s*\(kg/m²\)\s*(\d+\.?\d*)\s*(\d+)\s*(\d+)", text, re.IGNORECASE)
                        if lean_idx_all_m:
                            parsed_lean_index_percentile = int(lean_idx_all_m.group(3))
    
                        alm_idx_m = re.search(r"Appen\.\s*Lean/Height²\s*\(kg/m²\)\s*(\d+\.?\d*)", text, re.IGNORECASE)
                        if alm_idx_m:
                            parsed_alm_index = float(alm_idx_m.group(1))
    
                        # 9. Bone Mineral Density (BMD) & Scores
                        parsed_bmd = None
                        parsed_bmd_t = None
                        parsed_bmd_z = None
                        bmd_m = re.search(r"Total\s+[\d\.]+\s+[\d\.]+\s+(\d+\.\d+)\s+([-\d\.]+)\s+([-\d\.]+)", text, re.IGNORECASE)
                        if bmd_m:
                            parsed_bmd = float(bmd_m.group(1))
                            parsed_bmd_t = float(bmd_m.group(2))
                            parsed_bmd_z = float(bmd_m.group(3))
                except Exception:
                    pass
    
                self._json(
                    200,
                    {
                        "ok": True,
                        "uploaded": target.name,
                        "date": parsed_date,
                        "scan_weight": parsed_weight,
                        "body_fat": parsed_fat,
                        "muscle": parsed_muscle,
                        "basal_metabolic_rate_kcal": parsed_bmr,
                        "total_body_water_lb": parsed_water,
                        "visceral_fat_area_cm2": parsed_vat_area,
                        "visceral_fat_mass_g": parsed_vat_mass,
                        "visceral_fat_volume_cm3": parsed_vat_volume,
                        "appendicular_lean_mass_index": parsed_alm_index,
                        "lean_height2_index": parsed_lean_height2_index,
                        "lean_index_percentile": parsed_lean_index_percentile,
                        "bmd_g_cm2": parsed_bmd,
                        "bmd_t_score": parsed_bmd_t,
                        "bmd_z_score": parsed_bmd_z,
                        "upload_dir": str(dexa_scans_dir)
                    }
                )
            except Exception as e:
                self._json(500, {"ok": False, "error": f"Unhandled error during file write/OCR: {str(e)}"})

        def _export_static_files(self) -> None:
            try:
                # 1. Export Biological Age snapshot to bio_age_data.js
                snapshot = self._compute_ag_snapshot()
                bio_age_js = config.PROJECT_DIR / "bio_age_data.js"
                js_content = "window.BIO_AGE_DATA = " + json.dumps(snapshot, indent=2) + ";\n"
                bio_age_js.write_text(js_content, encoding="utf-8")
            except Exception as e:
                print(f"Error exporting bio_age_data.js: {e}")
    
            try:
                # 2. Export BP readings to bp_data.js
                rows = self._read_existing_bp()
                bp_js = config.PROJECT_DIR / "bp_data.js"
                js_content = "window.BP_DASH_DATA = " + json.dumps(rows, indent=2) + ";\n"
                bp_js.write_text(js_content, encoding="utf-8")
            except Exception as e:
                print(f"Error exporting bp_data.js: {e}")
    
            try:
                # 3. Export DEXA data to dexa_data.js
                rows = self._load_dexa_data()
                dexa_js = config.PROJECT_DIR / "dexa_data.js"
                js_content = "window.DEXA_DATA = " + json.dumps({"ok": True, "rows": rows}, indent=2) + ";\n"
                dexa_js.write_text(js_content, encoding="utf-8")
            except Exception as e:
                print(f"Error exporting dexa_data.js: {e}")
