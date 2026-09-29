"""Personal Health Manager dashboard server (v2).

Thin composition root: request handling lives in the api/ mixin modules;
this file keeps route dispatch (do_GET/do_HEAD/do_POST), startup, and the
public compatibility interface (DashboardHandler, PROJECT_DIR, ...).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import health_storage
import weight_tracking
import youtube_analyzer

from api import config
from api.auth import AuthMixin
from api.bp import BPMixin
from api.core import CoreMixin
from api.labs import LabsMixin
from api.records import RecordsMixin
from api.uploads import UploadsMixin
from api.weight import WeightMixin

# Compatibility re-exports: existing references (tests, tooling) keep working.
PROJECT_DIR = config.PROJECT_DIR
HEALTH_DATA_DIR = config.HEALTH_DATA_DIR
HOSTED_RENDER = config.HOSTED_RENDER
SCAN_LOCK = config.SCAN_LOCK
REBUILD_LOCK = config.REBUILD_LOCK
WEIGHT_LOCK = config.WEIGHT_LOCK
WEIGHT_DATA_PATH = config.WEIGHT_DATA_PATH
VAULT_AUTH_USER = config.VAULT_AUTH_USER
VAULT_AUTH_PASS = config.VAULT_AUTH_PASS
ADMIN_AUTH_USER = config.ADMIN_AUTH_USER
ADMIN_AUTH_PASS = config.ADMIN_AUTH_PASS
BP_DATA_PATH = config.BP_DATA_PATH
AG_IMPORTS_PATH = config.AG_IMPORTS_PATH
CONSOLIDATED_LABS_PATH = config.CONSOLIDATED_LABS_PATH
DEXA_DATA_PATH = config.DEXA_DATA_PATH
MEDICINE_DATA_PATH = config.MEDICINE_DATA_PATH


class DashboardHandler(
    CoreMixin,
    AuthMixin,
    LabsMixin,
    BPMixin,
    WeightMixin,
    UploadsMixin,
    RecordsMixin,
    SimpleHTTPRequestHandler,
):

    def do_GET(self) -> None:
        path = self.path.split('?', 1)[0]
        if path == '/api/storage-status':
            self._json(200, {'ok': True, **health_storage.status(config.HEALTH_DATA_DIR, config.HOSTED_RENDER)})
            return
        if path == '/api/weight-data':
            try:
                rows = weight_tracking.load_entries(config.WEIGHT_DATA_PATH)
                self._json(200, {'ok': True, 'rows': rows, 'health_context': self._weight_context(rows)})
            except (ValueError, OSError, TypeError, KeyError):
                self._json(500, {'ok': False, 'error': 'Cannot read weight file. Existing data was preserved.'})
            return
        if path == '/api/last-updated':
            self._json(200, self._last_updated_payload())
            return
        if path == '/api/ag-dashboard-data':
            self._json(200, self._compute_ag_snapshot())
            self._export_static_files()
            return
        if path == '/api/history':
            self._json(200, self._history_payload())
            return
        if path == '/api/bp-data':
            self._json(200, {'ok': True, 'days': 90, 'rows': self._bp_last_days(90), 'history_rows': self._read_existing_bp()})
            self._export_static_files()
            return
        if path == '/api/dexa-data':
            try:
                rows = self._load_dexa_data()
                self._json(200, {'ok': True, 'rows': rows})
                self._export_static_files()
            except (ValueError, OSError):
                self._json(500, {'ok': False, 'error': 'Cannot read scan records. Existing files and cached scans were preserved.'})
            return
        if self._require_admin_auth():
            return
        if self._require_vault_auth():
            return
        if path == '/health/youtube-comments':
            self.path = '/youtube-comments.html'
            super().do_GET()
            return
        if self._serve_env_override(path):
            return
        if path == '/api/doctor-notes':
            self._json(200, {'ok': True, **self._doctor_notes_payload()})
            return
        if path == '/api/medications':
            self._json(200, {'ok': True, **self._medicine_payload()})
            return
        super().do_GET()

    def do_HEAD(self) -> None:
        path = self.path.split('?', 1)[0]
        if self._require_admin_auth():
            return
        if self._require_vault_auth():
            return
        if self._serve_env_override(path):
            return
        super().do_HEAD()

    def do_POST(self) -> None:
        if self.path == '/api/admin/weight-entry':
            if self._require_admin_auth():
                return
            self._handle_weight_save()
            return
        if self.path == '/api/medications':
            if self._require_vault_auth():
                return
            self._handle_medicine_save()
            return
        if self.path == '/api/doctor-notes':
            if self._require_vault_auth():
                return
            self._handle_doctor_notes_save()
            return
        if self.path == '/api/ag-import/preview':
            if self._require_admin_auth():
                return
            self._handle_ag_import_preview()
            return
        if self.path == '/api/ag-import/commit':
            if self._require_admin_auth():
                return
            self._handle_ag_import_commit()
            return
        if self.path == '/api/admin/upload-bp-csv':
            if self._require_admin_auth():
                return
            self._handle_admin_bp_upload()
            return
        if self.path == '/api/admin/upload-pdf':
            if self._require_admin_auth():
                return
            self._handle_admin_pdf_upload()
            return
        if self.path == '/api/admin/upload-data':
            if self._require_admin_auth():
                return
            self._handle_admin_upload()
            return
        if self.path == '/api/admin/upload-dexa-pdf':
            if self._require_admin_auth():
                return
            self._handle_admin_dexa_pdf_upload()
            return
        if self.path == '/api/admin/upload-inbody-pdf':
            if self._require_admin_auth():
                return
            self._handle_admin_inbody_pdf_upload()
            return
        if self.path == '/api/admin/save-dexa-record':
            if self._require_admin_auth():
                return
            self._handle_admin_save_dexa_record()
            return
        if self.path == '/api/youtube-comments/analyze':
            length = int(self.headers.get('Content-Length', '0'))
            raw = self.rfile.read(length) if length else b'{}'
            try:
                payload = json.loads(raw.decode('utf-8'))
            except Exception as e:
                self._json(400, {'ok': False, 'error': 'Invalid JSON'})
                return
            video_url = payload.get('videoUrl', '')
            max_comments = int(payload.get('maxComments', 500))
            include_replies = bool(payload.get('includeReplies', True))
            sort_order = payload.get('sortOrder', 'relevance')
            focus_keywords = payload.get('focusKeywords', [])
            analysis_mode = payload.get('analysisMode', 'comments_only')
            import importlib
            importlib.reload(youtube_analyzer)
            result = youtube_analyzer.fetch_youtube_comments(video_url, max_comments, include_replies, sort_order, focus_keywords, analysis_mode)
            status = 200 if result.get('ok') else 400
            self._json(status, result)
            return
        if self.path == '/api/youtube-comments/ai-summary':
            length = int(self.headers.get('Content-Length', '0'))
            raw = self.rfile.read(length) if length else b'{}'
            try:
                payload = json.loads(raw.decode('utf-8'))
            except Exception as e:
                self._json(400, {'ok': False, 'error': 'Invalid JSON'})
                return
            comments = payload.get('comments', [])
            user_context = payload.get('userContext', {})
            transcript = payload.get('transcript', '')
            transcript_error = payload.get('transcriptError', '')
            if 'mode' in payload:
                user_context['mode'] = payload['mode']
            user_context['transcriptError'] = transcript_error
            import importlib
            importlib.reload(youtube_analyzer)
            summary = youtube_analyzer.analyze_ai_summary(comments, user_context, transcript)
            self._json(200, summary)
            return
        if self.path == '/api/youtube-comments/ai-theme':
            length = int(self.headers.get('Content-Length', '0'))
            raw = self.rfile.read(length) if length else b'{}'
            try:
                payload = json.loads(raw.decode('utf-8'))
            except Exception as e:
                self._json(400, {'ok': False, 'error': 'Invalid JSON'})
                return
            comments = payload.get('comments', [])
            theme = payload.get('theme', '')
            import importlib
            importlib.reload(youtube_analyzer)
            summary = youtube_analyzer.analyze_theme_summary(comments, theme)
            self._json(200, summary)
            return
        if self.path != '/api/rebuild':
            self._json(404, {'ok': False, 'error': 'Not found'})
            return
        if not config.REBUILD_LOCK.acquire(blocking=False):
            self._json(409, {'ok': False, 'error': 'Rebuild already in progress'})
            return
        try:
            result, rebuild_error = self._guarded_rebuild()
            if rebuild_error is not None:
                self._json(500, {'ok': False, 'error': rebuild_error, 'stdout': result.stdout, 'stderr': result.stderr})
                return
            self._json(200, {'ok': True, 'stdout': result.stdout})
        finally:
            config.REBUILD_LOCK.release()


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


def _rebuild_env(upload_dir_override: Path | None = None) -> dict[str, str]:
    env = os.environ.copy()
    if env.get("LAB_RESULTS_DIR", "").strip():
        return env
    if upload_dir_override is not None:
        env["LAB_RESULTS_DIR"] = str(upload_dir_override)
        return env
    return env


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


class StaticExporter:
    def __getattr__(self, name: str) -> Any:
        val = getattr(DashboardHandler, name)
        if callable(val):
            return val.__get__(self, StaticExporter)
        return val


def main() -> None:
    args = parse_args()
    if health_storage.status(HEALTH_DATA_DIR, HOSTED_RENDER)["writes_enabled"]:
        health_storage.initialize(PROJECT_DIR, HEALTH_DATA_DIR)
    else:
        print(health_storage.status(HEALTH_DATA_DIR, HOSTED_RENDER)["message"])
    rebuild_if_needed(args.no_rebuild)

    # Export all initial static files to support file:// protocol fallback
    try:
        StaticExporter()._export_static_files()
    except Exception as e:
        print(f"Warning: could not generate initial static JS files: {e}")

    os.chdir(PROJECT_DIR)
    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    print(
        f"Personal Health Manager running at http://{args.host}:{args.port}/index.html"
    )
    print("Press Ctrl+C to stop.")
    server.serve_forever()


if __name__ == "__main__":
    main()
