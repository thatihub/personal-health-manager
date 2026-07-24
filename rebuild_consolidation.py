#!/usr/bin/env python3
"""Rebuild consolidated lab dataset from PDFs in the parent workspace."""

from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from PIL import Image
from PIL import ImageEnhance
from PIL import ImageFilter


PROJECT_DIR = Path(__file__).resolve().parent


def discover_source_root() -> Path:
    env_root = os.getenv("LAB_RESULTS_DIR")
    if env_root:
        p = Path(env_root).expanduser()
        if p.exists():
            return p

    candidates = [
        PROJECT_DIR.parent / "Health prakash/Lab tests/Lab Results",
        Path.home() / "Documents/Health/Health prakash/Lab tests/Lab Results",
        PROJECT_DIR.parent,
    ]

    for c in candidates:
        if c.exists() and any(c.rglob("*.pdf")):
            return c

    return PROJECT_DIR.parent


ROOT_DIR = discover_source_root()

FIELDS = [
    "date",
    "source",
    "report_file",
    "glucose_mg_dl",
    "hba1c_pct",
    "total_chol_mg_dl",
    "hdl_mg_dl",
    "ldl_mg_dl",
    "triglycerides_mg_dl",
    "lipoprotein_a_mg_dl",
    "bun_mg_dl",
    "bun_creatinine_ratio",
    "sodium_mmol_l",
    "potassium_mmol_l",
    "chloride_mmol_l",
    "co2_mmol_l",
    "calcium_mg_dl",
    "total_protein_g_dl",
    "albumin_g_dl",
    "bilirubin_mg_dl",
    "alk_phos_u_l",
    "ast_u_l",
    "alt_u_l",
    "creatinine_mg_dl",
    "egfr_ml_min_1_73",
    "urine_acr_mg_g",
    "c_peptide_ng_ml",
    "tsh_uiu_ml",
    "free_t4_ng_dl",
    "thyroxine_t4_ug_dl",
    "t3_uptake_pct",
    "free_thyroxine_index",
    "thyroglobulin_ab_iu_ml",
    "tpo_ab_iu_ml",
    "vitamin_d_ng_ml",
    "vitamin_b12_pg_ml",
    "wbc_x10e3_ul",
    "rbc_x10e6_ul",
    "hemoglobin_g_dl",
    "hematocrit_pct",
    "platelets_x10e3_ul",
    "psa_ng_ml",
    "hcv_antibody",
    "estimated_avg_glucose_mg_dl",
    "notes",
]


def as_iso(mmddyyyy: str | None) -> str | None:
    if not mmddyyyy:
        return None
    return datetime.strptime(mmddyyyy, "%m/%d/%Y").strftime("%Y-%m-%d")


def normalize_pdf_text(pdf_path: Path) -> str:
    reader = PdfReader(str(pdf_path))
    raw = "\n".join((page.extract_text() or "") for page in reader.pages)
    text = " ".join(raw.split())
    if text:
        return text

    # OCR fallback for scanned/image-based PDFs.
    ocr_chunks: list[str] = []
    for page in reader.pages:
        for image_file in getattr(page, "images", []):
            ocr_text = ocr_image_bytes(image_file.data)
            if ocr_text:
                ocr_chunks.append(ocr_text)
    return " ".join(" ".join(ocr_chunks).split())


def ocr_image_bytes(image_bytes: bytes) -> str:
    from io import BytesIO
    from PIL import ImageOps

    input_path: Path | None = None
    output_base: Path | None = None
    output_txt: Path | None = None
    try:
        img = Image.open(BytesIO(image_bytes))
        img = ImageOps.exif_transpose(img)
        if img.mode != "L":
            img = img.convert("L")

        # Fast orientation/rotation detection using resized image
        keywords = ["body", "composition", "fat", "lean", "mass", "visceral", "vat", "metabolic", "bmr", "weight", "skeletal", "muscle", "smm", "young", "matched", "percentile"]
        w, h = img.size
        new_w = 800
        new_h = int(h * (new_w / w))
        small_img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        
        best_angle = 0
        max_kw_count = -1
        
        for angle in [0, 90, 180, 270]:
            rotated = small_img.rotate(angle, expand=True) if angle != 0 else small_img
            proc = ImageEnhance.Contrast(rotated).enhance(1.8)
            proc = proc.filter(ImageFilter.SHARPEN)
            proc = proc.point(lambda p: 255 if p > 165 else 0)
            
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f_out:
                temp_path = f_out.name
            proc.save(temp_path, format="PNG")
            
            out_base = temp_path + ".ocr"
            try:
                subprocess.run(
                    ["tesseract", temp_path, out_base, "--dpi", "150", "--psm", "6"],
                    capture_output=True, text=True, check=False
                )
                out_txt = Path(f"{out_base}.txt")
                if out_txt.exists():
                    text = out_txt.read_text(encoding="utf-8", errors="ignore").lower()
                    kw_count = sum(text.count(kw) for kw in keywords)
                    if kw_count > max_kw_count:
                        max_kw_count = kw_count
                        best_angle = angle
                    out_txt.unlink(missing_ok=True)
            except Exception:
                pass
            finally:
                Path(temp_path).unlink(missing_ok=True)

        if best_angle != 0:
            img = img.rotate(best_angle, expand=True)

        # Final high quality OCR
        img = ImageEnhance.Contrast(img).enhance(1.8)
        img = img.filter(ImageFilter.SHARPEN)
        img = img.point(lambda p: 255 if p > 165 else 0)

        in_fd, in_name = tempfile.mkstemp(suffix=".png")
        out_fd, out_name = tempfile.mkstemp(suffix=".ocr")
        os.close(in_fd)
        os.close(out_fd)
        Path(in_name).unlink(missing_ok=True)
        Path(out_name).unlink(missing_ok=True)
        input_path = Path(in_name)
        output_base = Path(out_name)
        output_txt = Path(f"{output_base}.txt")

        img.save(input_path, format="PNG")

        result = subprocess.run(
            [
                "tesseract",
                str(input_path),
                str(output_base),
                "--dpi",
                "300",
                "--psm",
                "6",
                "-c",
                "preserve_interword_spaces=1",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return ""
        if not output_txt.exists():
            return ""
        return output_txt.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    finally:
        for p in (input_path, output_txt, output_base):
            try:
                if p is not None:
                    p.unlink(missing_ok=True)
            except Exception:
                pass


def parse_numeric_token(token: str) -> float:
    token = token.strip().lstrip("<>").strip()
    if "," in token and "." not in token:
        left, right = token.split(",", 1)
        # OCR often reads decimal points as commas for lab values (e.g., 11,400 -> 11.400).
        if left.isdigit() and right.isdigit() and len(left) <= 2 and len(right) == 3:
            token = f"{left}.{right}"
        else:
            token = token.replace(",", "")
    return float(token)


def match_float(text: str, pattern: str, flags: int = re.I) -> float | None:
    m = re.search(pattern, text, flags)
    if not m:
        return None
    return parse_numeric_token(m.group(1))


def match_string(text: str, pattern: str, flags: int = re.I) -> str | None:
    m = re.search(pattern, text, flags)
    if not m:
        return None
    return m.group(1).strip()


def match_number_or_bound(text: str, pattern: str, flags: int = re.I) -> tuple[float | None, str]:
    m = re.search(pattern, text, flags)
    if not m:
        return None, ""
    token = m.group(1)
    note = ""
    if token.startswith(">") or token.startswith("<"):
        note = f"Value reported as {token}"
        token = token.lstrip("<>")
    return parse_numeric_token(token), note


def extract_date(text: str) -> str | None:
    date_patterns = [
        r"Date Collected:\s*(\d{2}/\d{2}/\d{4})",
        r"Collected:\s*(\d{2}/\d{2}/\d{4})",
        r"Collection Date:\s*Collection Time:\s*Type:\s*(\d{2}/\d{2}/\d{4})",
        r"Collection Date:\s*(\d{2}/\d{2}/\d{4})",
    ]
    for pattern in date_patterns:
        m = re.search(pattern, text, re.I)
        if m:
            return as_iso(m.group(1))
    return None


def extract_labcorp(text: str, report_name: str) -> dict[str, Any]:
    b12, b12_note = match_number_or_bound(text, r"Vitamin B12\s*\d+\s*([<>]?\d+(?:\.\d+)?)")
    tg_ab, tg_ab_note = match_number_or_bound(text, r"Thyroglobulin Antibody\s*\d*\s*([<>]{0,2}\d+(?:\.\d+)?)")
    tpo_ab, tpo_ab_note = match_number_or_bound(text, r"(?:Thyroid Peroxidase \(TPO\) Ab|Thyroid Peroxidase Ab|TPO Antibody|Thyroid Peroxidase Autoantibodies)[^0-9<>]{0,20}([<>]{0,2}\d+(?:\.\d+)?)")
    notes = []
    if b12_note:
        notes.append(b12_note)
    if tg_ab_note:
        notes.append(tg_ab_note)
    if tpo_ab_note:
        notes.append(tpo_ab_note)

    row = {
        "source": "Labcorp",
        "report_file": report_name,
        "glucose_mg_dl": match_float(text, r"Glucose\s*\d+\s*([<>]?\d+(?:\.\d+)?)"),
        "hba1c_pct": match_float(text, r"Hemoglobin A1c\s*\d+\s*([<>]?\d+(?:\.\d+)?)"),
        "total_chol_mg_dl": match_float(text, r"Cholesterol, Total\s*\d+\s*([<>]?\d+(?:\.\d+)?)"),
        "hdl_mg_dl": match_float(text, r"HDL Cholesterol\s*\d+\s*([<>]?\d+(?:\.\d+)?)"),
        "ldl_mg_dl": match_float(text, r"LDL Chol Calc \(NIH\)\s*([<>]?\d+(?:\.\d+)?)"),
        "triglycerides_mg_dl": match_float(text, r"Triglycerides\s*\d+\s*([<>]?\d+(?:\.\d+)?)"),
        "lipoprotein_a_mg_dl": None,
        "bun_mg_dl": match_float(text, r"BUN\s*\d+\s*([<>]?\d+(?:[.,]\d+)?)"),
        "bun_creatinine_ratio": match_float(text, r"BUN/Creatinine Ratio\s*([<>]?\d+(?:[.,]\d+)?)"),
        "sodium_mmol_l": match_float(text, r"Sodium\s*\d+\s*([<>]?\d+(?:[.,]\d+)?)"),
        "potassium_mmol_l": match_float(text, r"Potassium\s*\d+\s*([<>]?\d+(?:[.,]\d+)?)"),
        "chloride_mmol_l": match_float(text, r"Chloride\s*\d+\s*([<>]?\d+(?:[.,]\d+)?)"),
        "co2_mmol_l": match_float(text, r"Carbon Dioxide, Total\s*\d+\s*([<>]?\d+(?:[.,]\d+)?)"),
        "calcium_mg_dl": match_float(text, r"Calcium\s*\d+\s*([<>]?\d+(?:[.,]\d+)?)"),
        "total_protein_g_dl": match_float(text, r"Protein, Total\s*\d+\s*([<>]?\d+(?:[.,]\d+)?)"),
        "albumin_g_dl": match_float(text, r"Albumin\s*\d+\s*([<>]?\d+(?:[.,]\d+)?)"),
        "bilirubin_mg_dl": match_float(text, r"Bilirubin, Total\s*\d+\s*([<>]?\d+(?:[.,]\d+)?)"),
        "alk_phos_u_l": match_float(text, r"Alkaline Phosphatase\s*\d+\s*([<>]?\d+(?:[.,]\d+)?)"),
        "ast_u_l": match_float(text, r"AST \(SGOT\)\s*\d+\s*([<>]?\d+(?:[.,]\d+)?)"),
        "alt_u_l": match_float(text, r"ALT \(SGPT\)\s*\d+\s*([<>]?\d+(?:[.,]\d+)?)"),
        "creatinine_mg_dl": match_float(text, r"Creatinine\s*\d+\s*([<>]?\d+(?:\.\d+)?)"),
        "egfr_ml_min_1_73": match_float(text, r"eGFR\s*([<>]?\d+(?:\.\d+)?)\s"),
        "urine_acr_mg_g": match_float(text, r"Alb/Creat Ratio\s*([<>]?\d+(?:\.\d+)?)"),
        "c_peptide_ng_ml": match_float(text, r"C-Peptide, Serum\s*\d+\s*([<>]?\d+(?:\.\d+)?)"),
        "tsh_uiu_ml": match_float(text, r"TSH\s*\d+\s*([<>]?\d+(?:[.,]\d+)?)"),
        "free_t4_ng_dl": match_float(text, r"T4,Free\(Direct\)\s*\d+\s*([<>]?\d+(?:[.,]\d+)?)"),
        "thyroxine_t4_ug_dl": match_float(text, r"Thyroxine\s*\(T4\)[^0-9<>]{0,20}([<>]?\d+(?:[.,]\d+)?)"),
        "t3_uptake_pct": match_float(text, r"T3 Uptake[^0-9<>]{0,20}([<>]?\d+(?:[.,]\d+)?)"),
        "free_thyroxine_index": match_float(text, r"Free Thyroxine Index[^0-9<>]{0,20}([<>]?\d+(?:[.,]\d+)?)"),
        "thyroglobulin_ab_iu_ml": tg_ab,
        "tpo_ab_iu_ml": tpo_ab,
        "vitamin_d_ng_ml": match_float(text, r"Vitamin D, 25-Hydroxy\s*\d+\s*([<>]?\d+(?:\.\d+)?)"),
        "vitamin_b12_pg_ml": b12,
        "estimated_avg_glucose_mg_dl": None,
        "wbc_x10e3_ul": match_float(text, r"\bWBC\s*\d*\s*([<>]?\d+(?:\.\d+)?)"),
        "rbc_x10e6_ul": match_float(text, r"\bRBC\s*\d*\s*([<>]?\d+(?:\.\d+)?)"),
        "hemoglobin_g_dl": match_float(text, r"\bHemoglobin\s*\d*\s*([<>]?\d+(?:\.\d+)?)"),
        "hematocrit_pct": match_float(text, r"\bHematocrit\s*\d*\s*([<>]?\d+(?:\.\d+)?)"),
        "platelets_x10e3_ul": match_float(text, r"\bPlatelets\s*\d*\s*([<>]?\d+(?:\.\d+)?)"),
        "psa_ng_ml": match_float(text, r"\bPSA,\s*Ultrasensitive\s*\d*\s*([<>]?\d+(?:\.\d+)?)"),
        "hcv_antibody": match_string(text, r"\bHep\s*C\s*Virus\s*Ab\s*\d*\s*(Non Reactive|Reactive|Equivocal)"),
        "notes": "; ".join(notes),
    }
    return row


def extract_quest(text: str, report_name: str) -> dict[str, Any]:
    c_peptide = match_float(text, r"C-PEPTIDE Analyte Value C-PEPTIDE\s*([<>]?\d+(?:\.\d+)?)")
    if c_peptide is None:
        c_peptide = match_float(text, r"C-PEPTIDE C-PEPTIDE Reference Range:\s*0\.80-3\.85 ng/mL\s*([<>]?\d+(?:\.\d+)?)")

    row = {
        "source": "Quest",
        "report_file": report_name,
        "glucose_mg_dl": match_float(text, r"GLUCOSE Reference Range:.*?confirmed with a follow-up test\.\s*([<>]?\d+(?:\.\d+)?)"),
        "hba1c_pct": match_float(text, r"HEMOGLOBIN A1c Reference Range:.*?children\.\s*([<>]?\d+(?:\.\d+)?)\s*<5\.7"),
        "total_chol_mg_dl": match_float(text, r"CHOLESTEROL, TOTAL Reference Range: <200 mg/dL\s*([<>]?\d+(?:\.\d+)?)"),
        "hdl_mg_dl": match_float(text, r"HDL CHOLESTEROL Reference Range:.*?mg/dL\s*([<>]?\d+(?:\.\d+)?)\s*> OR = 40"),
        "ldl_mg_dl": match_float(text, r"LDL-CHOLESTEROL mg/dL \(calc\)\s*([<>]?\d+(?:\.\d+)?)"),
        "triglycerides_mg_dl": match_float(text, r"TRIGLYCERIDES Reference Range: <150 mg/dL\s*([<>]?\d+(?:\.\d+)?)"),
        "bun_mg_dl": match_float(text, r"BUN Reference Range:\s*8-27 mg/dL\s*([<>]?\d+(?:[.,]\d+)?)") or match_float(text, r"BUN Reference Range:.*?([<>]?\d+(?:[.,]\d+)?)\s*8\s*27"),
        "bun_creatinine_ratio": None,
        "sodium_mmol_l": match_float(text, r"SODIUM Reference Range:.*?([<>]?\d+(?:[.,]\d+)?)\s*135\s*146"),
        "potassium_mmol_l": match_float(text, r"POTASSIUM Reference Range:.*?([<>]?\d+(?:[.,]\d+)?)\s*3\.5\s*5\.3"),
        "chloride_mmol_l": match_float(text, r"CHLORIDE Reference Range:.*?([<>]?\d+(?:[.,]\d+)?)\s*98\s*110"),
        "co2_mmol_l": match_float(text, r"CARBON DIOXIDE Reference Range:.*?([<>]?\d+(?:[.,]\d+)?)\s*20\s*32"),
        "calcium_mg_dl": match_float(text, r"CALCIUM Reference Range:.*?([<>]?\d+(?:[.,]\d+)?)\s*8\.6\s*10\.3"),
        "total_protein_g_dl": match_float(text, r"PROTEIN, TOTAL Reference Range:.*?([<>]?\d+(?:[.,]\d+)?)\s*6\.1\s*8\.1"),
        "albumin_g_dl": match_float(text, r"ALBUMIN Reference Range:.*?([<>]?\d+(?:[.,]\d+)?)\s*3\.6\s*5\.1"),
        "bilirubin_mg_dl": match_float(text, r"BILIRUBIN, TOTAL Reference Range:.*?([<>]?\d+(?:[.,]\d+)?)\s*0\.2\s*1\.2"),
        "alk_phos_u_l": match_float(text, r"ALKALINE PHOSPHATASE Reference Range:.*?([<>]?\d+(?:[.,]\d+)?)\s*35\s*144"),
        "ast_u_l": match_float(text, r"AST Reference Range:.*?([<>]?\d+(?:[.,]\d+)?)\s*10\s*35"),
        "alt_u_l": match_float(text, r"ALT Reference Range:.*?([<>]?\d+(?:[.,]\d+)?)\s*9\s*46"),
        "creatinine_mg_dl": match_float(text, r"CREATININE Reference Range: 0\.70-1\.35 mg/dL\s*([<>]?\d+(?:\.\d+)?)"),
        "egfr_ml_min_1_73": match_float(text, r"EGFR Reference Range: > OR = 60 mL/min/1\.73m2.*?([<>]?\d+(?:\.\d+)?)\s*> OR = 60"),
        "urine_acr_mg_g": None,
        "c_peptide_ng_ml": c_peptide,
        "tsh_uiu_ml": None,
        "free_t4_ng_dl": None,
        "thyroxine_t4_ug_dl": None,
        "t3_uptake_pct": None,
        "free_thyroxine_index": None,
        "thyroglobulin_ab_iu_ml": None,
        "tpo_ab_iu_ml": None,
        "vitamin_d_ng_ml": None,
        "vitamin_b12_pg_ml": None,
        "estimated_avg_glucose_mg_dl": None,
        "notes": "",
    }

    if row["glucose_mg_dl"] is None and row["hba1c_pct"] is None and row["c_peptide_ng_ml"] is not None:
        row["notes"] = "C-peptide only report"
    return row


def extract_letsgetchecked(text: str, report_name: str) -> dict[str, Any]:
    lpa, lpa_note = match_number_or_bound(text, r"Lipoprotein \(a\)\s+Normal\s+mg/dL\s+NORMAL\s+([<>]?\d+(?:\.\d+)?)")
    notes = []
    if lpa_note:
        notes.append(lpa_note)
    row = {
        "source": "LetsGetChecked",
        "report_file": report_name,
        "glucose_mg_dl": None,
        "hba1c_pct": match_float(text, r"Hemoglobin A1c High % HIGH\s*([<>]?\d+(?:\.\d+)?)"),
        "total_chol_mg_dl": match_float(text, r"Cholesterol Normal mg/dL NORMAL\s*([<>]?\d+(?:\.\d+)?)"),
        "hdl_mg_dl": match_float(text, r"HDL Cholesterol Normal mg/dL NORMAL\s*([<>]?\d+(?:\.\d+)?)"),
        "ldl_mg_dl": match_float(text, r"LDL Cholesterol \(Calc\)\s*Normal mg/dL NORMAL\s*([<>]?\d+(?:\.\d+)?)"),
        "triglycerides_mg_dl": match_float(text, r"Triglycerides Normal mg/dL NORMAL\s*([<>]?\d+(?:\.\d+)?)"),
        "lipoprotein_a_mg_dl": lpa,
        "bun_mg_dl": None,
        "bun_creatinine_ratio": None,
        "sodium_mmol_l": None,
        "potassium_mmol_l": None,
        "chloride_mmol_l": None,
        "co2_mmol_l": None,
        "calcium_mg_dl": None,
        "total_protein_g_dl": None,
        "albumin_g_dl": None,
        "bilirubin_mg_dl": None,
        "alk_phos_u_l": None,
        "ast_u_l": None,
        "alt_u_l": None,
        "creatinine_mg_dl": None,
        "egfr_ml_min_1_73": None,
        "urine_acr_mg_g": None,
        "c_peptide_ng_ml": None,
        "tsh_uiu_ml": None,
        "free_t4_ng_dl": None,
        "thyroxine_t4_ug_dl": None,
        "t3_uptake_pct": None,
        "free_thyroxine_index": None,
        "thyroglobulin_ab_iu_ml": None,
        "tpo_ab_iu_ml": None,
        "vitamin_d_ng_ml": None,
        "vitamin_b12_pg_ml": None,
        "estimated_avg_glucose_mg_dl": match_float(text, r"Estimated Avg Glucose \(Calc\)\s*N/A mg/dL\s*([<>]?\d+(?:\.\d+)?)"),
        "notes": "; ".join(notes),
    }
    return row


def extract_generic(text: str, report_name: str, source_name: str = "Unknown") -> dict[str, Any]:
    b12, b12_note = match_number_or_bound(text, r"Vitamin B12[^0-9<>]*([<>]?\d+(?:\.\d+)?)")
    tg_ab, tg_ab_note = match_number_or_bound(text, r"Thyroglobulin Antibody[^0-9<>]{0,20}([<>]{0,2}\d+(?:\.\d+)?)")
    tpo_ab, tpo_ab_note = match_number_or_bound(text, r"(?:Thyroid Peroxidase \(TPO\) Ab|Thyroid Peroxidase Ab|TPO Antibody|Thyroid Peroxidase Autoantibodies)[^0-9<>]{0,20}([<>]{0,2}\d+(?:\.\d+)?)")
    notes = []
    if b12_note:
        notes.append(b12_note)
    if tg_ab_note:
        notes.append(tg_ab_note)
    if tpo_ab_note:
        notes.append(tpo_ab_note)
    return {
        "source": source_name,
        "report_file": report_name,
        "glucose_mg_dl": match_float(text, r"Glucose[^0-9<>]{0,20}([<>]?\d+(?:\.\d+)?)"),
        "hba1c_pct": match_float(text, r"Hemoglobin A1c[^0-9<>]{0,20}([<>]?\d+(?:\.\d+)?)"),
        "total_chol_mg_dl": match_float(text, r"Cholesterol,? Total[^0-9<>]{0,20}([<>]?\d+(?:\.\d+)?)"),
        "hdl_mg_dl": match_float(text, r"\bHDL(?: Cholesterol)?\b[^0-9<>]{0,20}([<>]?\d+(?:\.\d+)?)"),
        "ldl_mg_dl": match_float(text, r"\bLDL\b[^0-9<>]{0,20}([<>]?\d+(?:\.\d+)?)"),
        "triglycerides_mg_dl": match_float(text, r"Triglycerides[^0-9<>]{0,20}([<>]?\d+(?:\.\d+)?)"),
        "bun_mg_dl": match_float(text, r"BUN[^0-9<>]{0,20}([<>]?\d+(?:[.,]\d+)?)"),
        "bun_creatinine_ratio": match_float(text, r"BUN/Creatinine Ratio[^0-9<>]{0,20}([<>]?\d+(?:[.,]\d+)?)"),
        "sodium_mmol_l": match_float(text, r"Sodium[^0-9<>]{0,20}([<>]?\d+(?:[.,]\d+)?)"),
        "potassium_mmol_l": match_float(text, r"Potassium[^0-9<>]{0,20}([<>]?\d+(?:[.,]\d+)?)"),
        "chloride_mmol_l": match_float(text, r"Chloride[^0-9<>]{0,20}([<>]?\d+(?:[.,]\d+)?)"),
        "co2_mmol_l": match_float(text, r"\b(?:Carbon Dioxide, Total|CO2)\b[^0-9<>]{0,20}([<>]?\d+(?:[.,]\d+)?)"),
        "calcium_mg_dl": match_float(text, r"Calcium[^0-9<>]{0,20}([<>]?\d+(?:[.,]\d+)?)"),
        "total_protein_g_dl": match_float(text, r"Protein, Total[^0-9<>]{0,20}([<>]?\d+(?:[.,]\d+)?)"),
        "albumin_g_dl": match_float(text, r"Albumin[^0-9<>]{0,20}([<>]?\d+(?:[.,]\d+)?)"),
        "bilirubin_mg_dl": match_float(text, r"Bilirubin, Total[^0-9<>]{0,20}([<>]?\d+(?:[.,]\d+)?)"),
        "alk_phos_u_l": match_float(text, r"(?:Alkaline Phosphatase|ALK PHOS)[^0-9<>]{0,20}([<>]?\d+(?:[.,]\d+)?)"),
        "ast_u_l": match_float(text, r"\b(?:AST|SGOT)\b[^0-9<>]{0,20}([<>]?\d+(?:[.,]\d+)?)"),
        "alt_u_l": match_float(text, r"\b(?:ALT|SGPT)\b[^0-9<>]{0,20}([<>]?\d+(?:[.,]\d+)?)"),
        "creatinine_mg_dl": match_float(text, r"Creatinine[^0-9<>]{0,20}([<>]?\d+(?:\.\d+)?)"),
        "egfr_ml_min_1_73": match_float(text, r"eGFR[^0-9<>]{0,20}([<>]?\d+(?:\.\d+)?)"),
        "urine_acr_mg_g": match_float(text, r"Alb/Creat Ratio[^0-9<>]{0,20}([<>]?\d+(?:\.\d+)?)"),
        "c_peptide_ng_ml": match_float(text, r"C-?Peptide(?:, Serum)?[^0-9<>]{0,20}([<>]?\d+(?:\.\d+)?)"),
        "tsh_uiu_ml": match_float(text, r"TSH[^0-9<>]{0,20}([<>]?\d+(?:[.,]\d+)?)"),
        "free_t4_ng_dl": match_float(text, r"T4,? ?Free(?:\(Direct\))?[^0-9<>]{0,20}([<>]?\d+(?:[.,]\d+)?)"),
        "thyroxine_t4_ug_dl": match_float(text, r"Thyroxine\s*\(T4\)[^0-9<>]{0,20}([<>]?\d+(?:[.,]\d+)?)"),
        "t3_uptake_pct": match_float(text, r"T3 Uptake[^0-9<>]{0,20}([<>]?\d+(?:[.,]\d+)?)"),
        "free_thyroxine_index": match_float(text, r"Free Thyroxine Index[^0-9<>]{0,20}([<>]?\d+(?:[.,]\d+)?)"),
        "thyroglobulin_ab_iu_ml": tg_ab,
        "tpo_ab_iu_ml": tpo_ab,
        "vitamin_d_ng_ml": match_float(text, r"Vitamin D[^0-9<>]{0,30}([<>]?\d+(?:\.\d+)?)"),
        "vitamin_b12_pg_ml": b12,
        "estimated_avg_glucose_mg_dl": match_float(text, r"Estimated Avg Glucose[^0-9<>]{0,20}([<>]?\d+(?:\.\d+)?)"),
        "notes": "; ".join(notes),
    }


def build_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pdf in sorted(ROOT_DIR.rglob("*.pdf")):
        if "lab_trends_project" in pdf.parts or "_extracted_text" in pdf.parts:
            continue
        low_name = pdf.name.lower()
        if "chatgpt-prakash_lab_summary" in low_name:
            continue
        if "hba1c graph" in low_name:
            continue

        try:
            text = normalize_pdf_text(pdf)
        except Exception as exc:
            print(f"Skipping unreadable PDF: {pdf} ({exc})")
            continue
        row: dict[str, Any]
        content_l = text.lower()
        is_labcorp = (
            "labcorp" in low_name
            or "lab corp" in low_name
            or "labcorp" in content_l
            or "laboratory corporation of america" in content_l
        )
        is_quest = (
            "quest" in low_name
            or "quest diagnostics" in content_l
            or "questdiagnostics" in content_l
        )
        is_lgc = (
            "let get checked" in low_name
            or "letsgetchecked" in low_name
            or "letsgetchecked" in content_l
        )

        if is_labcorp:
            row = extract_labcorp(text, pdf.name)
        elif is_quest:
            row = extract_quest(text, pdf.name)
        elif is_lgc:
            row = extract_letsgetchecked(text, pdf.name)
        else:
            row = extract_generic(text, pdf.name, source_name="Unknown")

        row["date"] = extract_date(text)
        if row["date"] is None:
            continue

        for field in FIELDS:
            row.setdefault(field, None if field != "notes" else "")
        rows.append(row)

    rows.sort(key=lambda r: r["date"])
    return rows


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_json(rows: list[dict[str, Any]], path: Path) -> None:
    path.write_text(json.dumps(rows, indent=2), encoding="utf-8")


def build_dashboard_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    dash = []
    for row in rows:
        dash.append(
            {
                "date": row["date"],
                "source": row["source"],
                "glucose": row["glucose_mg_dl"],
                "hba1c": row["hba1c_pct"],
                "totalChol": row["total_chol_mg_dl"],
                "hdl": row["hdl_mg_dl"],
                "ldl": row["ldl_mg_dl"],
                "triglycerides": row["triglycerides_mg_dl"],
                "lipoproteinA": row["lipoprotein_a_mg_dl"],
                "bun": row["bun_mg_dl"],
                "sodium": row["sodium_mmol_l"],
                "chloride": row["chloride_mmol_l"],
                "potassium": row["potassium_mmol_l"],
                "co2": row["co2_mmol_l"],
                "calcium": row["calcium_mg_dl"],
                "totalProtein": row["total_protein_g_dl"],
                "albumin": row["albumin_g_dl"],
                "globulin": (
                    round(row["total_protein_g_dl"] - row["albumin_g_dl"], 2)
                    if row["total_protein_g_dl"] is not None and row["albumin_g_dl"] is not None
                    else None
                ),
                "bilirubin": row["bilirubin_mg_dl"],
                "alkPhos": row["alk_phos_u_l"],
                "ast": row["ast_u_l"],
                "alt": row["alt_u_l"],
                "creatinine": row["creatinine_mg_dl"],
                "egfr": row["egfr_ml_min_1_73"],
                "acr": row["urine_acr_mg_g"],
                "cPeptide": row["c_peptide_ng_ml"],
                "tsh": row["tsh_uiu_ml"],
                "freeT4": row["free_t4_ng_dl"],
                "thyroxineT4": row["thyroxine_t4_ug_dl"],
                "t3Uptake": row["t3_uptake_pct"],
                "freeThyroxineIndex": row["free_thyroxine_index"],
                "tgAb": row["thyroglobulin_ab_iu_ml"],
                "tpoAb": row["tpo_ab_iu_ml"],
                "vitaminD": row["vitamin_d_ng_ml"],
                "vitaminB12": row["vitamin_b12_pg_ml"],
                "wbc": row["wbc_x10e3_ul"],
                "rbc": row["rbc_x10e6_ul"],
                "hemoglobin": row["hemoglobin_g_dl"],
                "hematocrit": row["hematocrit_pct"],
                "platelets": row["platelets_x10e3_ul"],
                "psa": row["psa_ng_ml"],
                "hcv": row["hcv_antibody"],
                "notes": row["notes"],
            }
        )
    return dash


def write_dashboard_data(rows: list[dict[str, Any]], path: Path) -> None:
    dash_rows = build_dashboard_rows(rows)
    js = "window.LAB_DASH_DATA = " + json.dumps(dash_rows, indent=2) + ";\n"
    path.write_text(js, encoding="utf-8")


def merge_rows(existing_rows: list[dict[str, Any]], new_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged_map = {}
    for row in existing_rows:
        date = row.get("date")
        source = row.get("source")
        if date and source:
            key = (date, source)
            merged_map[key] = row
        else:
            rf = row.get("report_file")
            if rf:
                merged_map[rf] = row

    for row in new_rows:
        date = row.get("date")
        source = row.get("source")
        if date and source:
            key = (date, source)
            merged_map[key] = row
        else:
            rf = row.get("report_file")
            if rf:
                merged_map[rf] = row

    merged_list = list(merged_map.values())
    merged_list.sort(key=lambda r: r.get("date") or "")
    return merged_list


def main() -> None:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Rebuild consolidated lab dataset.")
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Force a clean rebuild from PDFs only (ignore existing JSON)."
    )
    args = parser.parse_args()

    # Find all PDF files in the source root to check for parsing failures later
    pdf_files = list(ROOT_DIR.rglob("*.pdf"))
    filtered_pdfs = []
    for pdf in pdf_files:
        if "lab_trends_project" in pdf.parts or "_extracted_text" in pdf.parts:
            continue
        low_name = pdf.name.lower()
        if "chatgpt-prakash_lab_summary" in low_name:
            continue
        if "hba1c graph" in low_name:
            continue
        filtered_pdfs.append(pdf)

    new_rows = build_rows()

    # If PDFs were found in ROOT_DIR but none parsed successfully, exit with code 3.
    # This signals a parsing failure (e.g. invalid PDF format) to the calling server.
    if len(filtered_pdfs) > 0 and len(new_rows) == 0:
        print(f"Error: Found {len(filtered_pdfs)} PDF files in source directory, but failed to parse any valid lab records.", file=sys.stderr)
        sys.exit(3)

    csv_path = PROJECT_DIR / "consolidated_labs.csv"
    json_path = PROJECT_DIR / "consolidated_labs.json"
    dash_js_path = PROJECT_DIR / "dashboard_data.js"

    if args.clean:
        rows = new_rows
        print("Running clean rebuild from PDFs only...")
    else:
        existing_rows = []
        if json_path.exists():
            try:
                with json_path.open("r", encoding="utf-8") as f:
                    existing_rows = json.load(f)
                print(f"Loaded {len(existing_rows)} existing records from {json_path}")
            except Exception as e:
                print(f"Warning: could not load existing consolidated_labs.json: {e}", file=sys.stderr)

        if existing_rows:
            rows = merge_rows(existing_rows, new_rows)
            print(f"Merged {len(new_rows)} new/updated records with existing database (total: {len(rows)} records)")
        else:
            rows = new_rows
            print(f"No existing records found. Created database with {len(rows)} records.")

    write_csv(rows, csv_path)
    write_json(rows, json_path)
    write_dashboard_data(rows, dash_js_path)

    print(f"Processed {len(rows)} reports")
    print(f"Wrote {csv_path}")
    print(f"Wrote {json_path}")
    print(f"Wrote {dash_js_path}")


if __name__ == "__main__":
    main()
