""" Lab import pipeline: name normalization, unit conversion, scoring, AG snapshots. """
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import datetime, timedelta
from uuid import uuid4
import phenoage

from api import config


class LabsMixin:
        def _load_ag_imports(self) -> dict[str, object]:
            if not config.AG_IMPORTS_PATH.exists():
                return {"reports": [], "results": []}
            try:
                parsed = json.loads(config.AG_IMPORTS_PATH.read_text(encoding="utf-8"))
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
            tmp = config.AG_IMPORTS_PATH.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(config.AG_IMPORTS_PATH)

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
                "mean corpuscular volume": "mcv",
                "mean cell volume": "mcv",
                "mcv": "mcv",
                "red cell distribution width": "rdw",
                "red blood cell distribution width": "rdw",
                "rdw": "rdw",
                "lymphocyte %": "lymphocyte_pct",
                "lymphocyte": "lymphocyte_pct",
                "high sensitivity c-reactive protein": "hs_crp",
                "c-reactive protein": "crp",
                "c reactive protein": "crp",
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
                "rbc": "10^6/uL",
                "vitamin_d": "ng/mL",
                "vitamin_b12": "pg/mL",
                "psa": "ng/mL",
                "thyroglobulin_ab": "IU/mL",
                "tpo_ab": "IU/mL",
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
            marker = "crp" if standard_name == "hs_crp" else standard_name
            if marker in phenoage.SPECS:
                normalize_unit = lambda text: text.lower().replace("µ", "u").replace("μ", "u").replace(" ", "")
                factors = phenoage.SPECS[marker][3]
                original = normalize_unit(u)
                target = normalize_unit(std_unit)
                if original not in factors:
                    return value, unit, "Missing or unsupported units; excluded from PhenoAge until reviewed"
                converted = value * factors[original] / factors[target]
                return converted, std_unit, (f"Converted from {unit} to {std_unit}" if original != target else None)
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
            return self._load_json_array(config.CONSOLIDATED_LABS_PATH)

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
                "wbc_x10e3_ul": ("wbc", "10^3/uL"),
                "mcv_fl": ("mcv", "fL"),
                "rdw_pct": ("rdw", "%"),
                "lymphocyte_pct": ("lymphocyte_pct", "%"),
                "crp_mg_l": ("crp", "mg/L"),
                "hs_crp_mg_l": ("hs_crp", "mg/L"),
                "rbc_x10e6_ul": ("rbc", "10^6/uL"),
                "hemoglobin_g_dl": ("hemoglobin", "g/dL"),
                "hematocrit_pct": ("hematocrit", "%"),
                "platelets_x10e3_ul": ("platelets", "10^3/uL"),
                "vitamin_d_ng_ml": ("vitamin_d", "ng/mL"),
                "vitamin_b12_pg_ml": ("vitamin_b12", "pg/mL"),
                "psa_ng_ml": ("psa", "ng/mL"),
                "thyroglobulin_ab_iu_ml": ("thyroglobulin_ab", "IU/mL"),
                "tpo_ab_iu_ml": ("tpo_ab", "IU/mL"),
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
                            "value_text": str(report.get(k)),
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
            if marker == "free_t4":
                return self._score_by_bands(value, [(0.0, 0.81, 55), (0.82, 1.77, 95), (1.78, 2.5, 65), (2.51, 10.0, 35)])
            if marker == "wbc":
                return self._score_by_bands(value, [(0.0, 3.39, 60), (3.4, 10.8, 95), (10.81, 15.0, 70), (15.01, 100.0, 40)])
            if marker == "hemoglobin":
                return self._score_by_bands(value, [(0.0, 11.9, 45), (12.0, 12.9, 75), (13.0, 17.7, 95), (17.71, 20.0, 75), (20.01, 50.0, 40)])
            if marker == "hematocrit":
                return self._score_by_bands(value, [(0.0, 34.9, 45), (35.0, 37.4, 75), (37.5, 51.0, 95), (51.01, 55.0, 75), (55.01, 100.0, 40)])
            if marker == "platelets":
                return self._score_by_bands(value, [(0.0, 149.0, 60), (150.0, 450.0, 95), (450.1, 600.0, 70), (600.1, 2000.0, 40)])
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
            chronological_age = self._chronological_age()
            phenotype = phenoage.assess(all_results, self._date_of_birth())
    
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
                "estimated_bio_age": phenotype["estimated_age"],
                "age_delta_years": phenotype["age_difference"],
                "confidence": "not_assessed",
                "phenoage": phenotype,
                "score_note": "Custom lab wellness scores are educational summaries, not validated biological-age estimates.",
                "section_scores": section_scores,
                "timeline": timeline,
                "normalized_results": all_results,
                "reports": imports.get("reports", []),
                "weight_context": self._weight_context(),
                "model_version": phenoage.MODEL_VERSION,
                "generated_at": datetime.now().astimezone().isoformat(),
            }

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
