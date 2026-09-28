"""Shared configuration: paths, auth, locks, env helpers.

Importable on its own; run_dashboard_server re-exports these names so
existing references (including tests using server.PROJECT_DIR) keep working.
"""
from __future__ import annotations

import base64
import binascii
import os
import threading
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent.parent
HEALTH_DATA_DIR = Path(os.getenv("HEALTH_DATA_DIR", str(PROJECT_DIR))).expanduser().resolve()
HOSTED_RENDER = os.getenv("RENDER", "").lower() == "true"
SCAN_LOCK = threading.Lock()
REBUILD_LOCK = threading.Lock()
WEIGHT_LOCK = threading.Lock()
WEIGHT_DATA_PATH = HEALTH_DATA_DIR / "weight_entries.json"
VAULT_AUTH_USER = os.getenv("VAULT_BASIC_AUTH_USER", "").strip()
VAULT_AUTH_PASS = os.getenv("VAULT_BASIC_AUTH_PASS", "").strip()
ADMIN_AUTH_USER = os.getenv("ADMIN_BASIC_AUTH_USER", "").strip()
ADMIN_AUTH_PASS = os.getenv("ADMIN_BASIC_AUTH_PASS", "").strip()
BP_DATA_PATH = PROJECT_DIR / "bp_readings.csv"
AG_IMPORTS_PATH = PROJECT_DIR / "ag_lab_imports.json"
CONSOLIDATED_LABS_PATH = PROJECT_DIR / "consolidated_labs.json"
DEXA_DATA_PATH = HEALTH_DATA_DIR / "dexa_records.json"
MEDICINE_DATA_PATH = PROJECT_DIR / "medicine_data.json"


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
    # First, check if custom ADMIN_UPLOADS_DIR is set
    env_uploads = os.getenv("ADMIN_UPLOADS_DIR")
    if env_uploads:
        p = Path(env_uploads).expanduser()
        p.mkdir(parents=True, exist_ok=True)
        return p

    # Check if standard env var LAB_RESULTS_DIR is set
    env_root = os.getenv("LAB_RESULTS_DIR")
    if env_root:
        p = Path(env_root).expanduser()
        if p.exists():
            return p

    # Otherwise discover standard candidates exactly like rebuild_consolidation.py does
    candidates = [
        PROJECT_DIR.parent / "Health prakash/Lab tests/Lab Results",
        Path.home() / "Documents/Health/Health prakash/Lab tests/Lab Results",
        PROJECT_DIR.parent,
    ]

    for c in candidates:
        if c.exists() and any(c.rglob("*.pdf")):
            return c

    # Fallback to _uploaded_pdfs if nothing else exists
    fallback = PROJECT_DIR / "_uploaded_pdfs"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def _rebuild_env(upload_dir_override: Path | None = None) -> dict[str, str]:
    env = os.environ.copy()
    if env.get("LAB_RESULTS_DIR", "").strip():
        return env
    if upload_dir_override is not None:
        env["LAB_RESULTS_DIR"] = str(upload_dir_override)
        return env
    return env
