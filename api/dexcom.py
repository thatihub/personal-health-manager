"""Dexcom in-app sync: OAuth connect + pull EGVs/events from the Dexcom
API and rebuild glucose_data.js locally (same computation as the Clarity
CSV path, via glucose_compute).

Credentials and tokens live ONLY in dexcom_config.json in the project
folder (git-ignored, never committed). The Glucose page drives it:
  GET  /api/dexcom/status    -> connection state + authorize URL
  POST /api/dexcom/config    -> save {client_id, client_secret}
  GET  /api/dexcom/callback  -> OAuth return; exchanges the code
  POST /api/dexcom/sync      -> refresh token if needed, pull, rebuild
"""
from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
from collections import defaultdict
from datetime import datetime, timedelta

import glucose_compute
from api import config

AUTH_URL = "https://api.dexcom.com/v2/oauth2/login"
TOKEN_URL = "https://api.dexcom.com/v2/oauth2/token"
API = "https://api.dexcom.com"
CONFIG_PATH = config.PROJECT_DIR / "dexcom_config.json"
DATA_PATH = config.PROJECT_DIR / "glucose_data.js"
PULL_DAYS = 120  # how far back each sync reaches
SYNC_LOCK = threading.Lock()  # one sync at a time (manual or automatic)
AUTO_SYNC_HOUR = 7  # daily auto-sync: first server tick at/after 7 AM local
_last_auto_attempt: datetime | None = None


def _load_cfg() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_cfg(cfg: dict) -> None:
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    tmp.replace(CONFIG_PATH)


def _token_request(form: dict) -> dict:
    body = urllib.parse.urlencode(form).encode("utf-8")
    req = urllib.request.Request(TOKEN_URL, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8"))
            msg = detail.get("message") or detail.get("error") or str(e)
        except Exception:
            msg = f"Dexcom token error (HTTP {e.code})"
        raise RuntimeError(msg) from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not reach Dexcom ({e.reason}). Check the internet connection and try again.") from e


def _api_get(path: str, token: str) -> dict:
    req = urllib.request.Request(API + path, method="GET")
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8"))
            msg = detail.get("message") or detail.get("error") or str(e)
        except Exception:
            msg = f"Dexcom API error (HTTP {e.code})"
        raise RuntimeError(msg) from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not reach Dexcom ({e.reason}). Check the internet connection and try again.") from e


def _parse_dt(s: str):
    try:
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def _access_token(cfg: dict) -> str:
    exp = cfg.get("expires_at")
    if cfg.get("access_token") and exp and _parse_dt(exp) and _parse_dt(exp) > datetime.now():
        return cfg["access_token"]
    if not cfg.get("refresh_token"):
        raise RuntimeError("not_connected")
    tok = _token_request({
        "client_id": cfg.get("client_id", ""),
        "client_secret": cfg.get("client_secret", ""),
        "grant_type": "refresh_token",
        "refresh_token": cfg["refresh_token"],
    })
    cfg["access_token"] = tok.get("access_token")
    if tok.get("refresh_token"):
        cfg["refresh_token"] = tok["refresh_token"]
    cfg["expires_at"] = (datetime.now() + timedelta(seconds=int(tok.get("expires_in", 7200)) - 60)).isoformat()
    _save_cfg(cfg)
    return cfg["access_token"]


def run_sync() -> dict:
    """Pull the last PULL_DAYS of EGVs + events and rebuild glucose_data.js.
    Shared by the Sync button handler and the daily auto-sync thread.
    Raises RuntimeError with the same error strings the API has always used."""
    cfg = _load_cfg()
    if not (cfg.get("client_id") and cfg.get("client_secret")):
        raise RuntimeError("not_configured")
    token = _access_token(cfg)
    end = datetime.now()
    start = end - timedelta(days=PULL_DAYS)
    egvs, events = [], []
    cur = start
    while cur < end:
        nxt = min(cur + timedelta(days=30), end)
        qs = urllib.parse.urlencode({
            "startDate": cur.strftime("%Y-%m-%dT%H:%M:%S"),
            "endDate": nxt.strftime("%Y-%m-%dT%H:%M:%S"),
        })
        for rec in _api_get(f"/v3/users/self/egvs?{qs}", token).get("records", []):
            t = _parse_dt(rec.get("displayTime") or rec.get("systemTime"))
            if t and isinstance(rec.get("value"), (int, float)):
                egvs.append((t, float(rec["value"])))
        for rec in _api_get(f"/v3/users/self/events?{qs}", token).get("records", []):
            t = _parse_dt(rec.get("displayTime") or rec.get("systemTime"))
            if t:
                events.append((t, rec))
        cur = nxt
    if not egvs:
        raise RuntimeError("Dexcom returned no glucose readings.")
    egvs.sort()
    insulin_by_day, meal_by_day = defaultdict(list), defaultdict(list)
    insulin_events, meal_events = [], []
    for t, rec in events:
        et = str(rec.get("eventType", "")).lower()
        sub = str(rec.get("eventSubType", "") or "")
        if str(rec.get("eventStatus", "")).lower() == "deleted":
            continue
        try:
            val = float(rec.get("value"))
        except (TypeError, ValueError):
            continue
        if et == "insulin":
            sub_label = "Long-Acting" if "long" in sub.lower() else "Fast-Acting"
            insulin_by_day[t.date().isoformat()].append([t.strftime("%H:%M"), float(val), sub_label])
            insulin_events.append((t, float(val), sub_label))
        elif et in ("carbs", "meal", "meals", "food") or "carb" in et:
            meal_by_day[t.date().isoformat()].append([t.strftime("%H:%M"), float(val)])
            meal_events.append((t, float(val)))
    from collections import Counter
    et_counts = Counter(str(rec.get("eventType", "?")) for _, rec in events)
    diag_path = DATA_PATH.parent / "dexcom_event_types.json"
    diag_path.write_text(json.dumps({"generated": datetime.now().strftime("%Y-%m-%d %H:%M"), "event_types": dict(et_counts)}, indent=2), encoding="utf-8")
    data = glucose_compute.build_glucose_data(
        egvs, insulin_by_day, meal_by_day, insulin_events, meal_events,
        "Dexcom API sync")
    DATA_PATH.write_text("window.GLUCOSE_DATA = " + json.dumps(data) + ";\n", encoding="utf-8")
    cfg = _load_cfg()
    cfg["last_sync"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    _save_cfg(cfg)
    return {"readings": data["total_readings"], "range": data["range"],
            "synced_at": cfg["last_sync"]}


def _auto_sync_due(cfg: dict) -> bool:
    if not cfg.get("refresh_token"):
        return False
    now = datetime.now()
    if now.hour < AUTO_SYNC_HOUR:
        return False
    last = cfg.get("last_sync") or ""
    return not last.startswith(now.strftime("%Y-%m-%d"))


def auto_sync_loop() -> None:
    """Daily in-app auto-sync: checks every 10 minutes; on the first tick
    at/after AUTO_SYNC_HOUR with no sync yet today, runs one sync. Retries a
    failed attempt at most hourly. Started once from the server's main()."""
    global _last_auto_attempt
    while True:
        time.sleep(600)
        try:
            cfg = _load_cfg()
            if not _auto_sync_due(cfg):
                continue
            if _last_auto_attempt and datetime.now() - _last_auto_attempt < timedelta(hours=1):
                continue
            if not SYNC_LOCK.acquire(blocking=False):
                continue
            _last_auto_attempt = datetime.now()
            try:
                res = run_sync()
                print(f"[dexcom] auto-sync ok: {res['readings']} readings", flush=True)
            except Exception as e:
                print(f"[dexcom] auto-sync failed: {e}", flush=True)
            finally:
                SYNC_LOCK.release()
        except Exception as e:
            print(f"[dexcom] auto-sync tick skipped: {e}", flush=True)


class DexcomMixin:
    def _dexcom_redirect_uri(self) -> str:
        host = self.headers.get("Host", "localhost:3002")
        scheme = "https" if (".ts.net" in host or ".ts.net:" in host) else "http"
        return f"{scheme}://{host}/api/dexcom/callback"

    def _dexcom_authorize_url(self, cfg: dict) -> str | None:
        if not cfg.get("client_id"):
            return None
        q = urllib.parse.urlencode({
            "client_id": cfg["client_id"],
            "redirect_uri": self._dexcom_redirect_uri(),
            "response_type": "code",
            "scope": "offline_access",
        })
        return f"{AUTH_URL}?{q}"

    def _handle_dexcom_status(self) -> None:
        cfg = _load_cfg()
        self._json(200, {
            "ok": True,
            "configured": bool(cfg.get("client_id") and cfg.get("client_secret")),
            "connected": bool(cfg.get("refresh_token")),
            "last_sync": cfg.get("last_sync"),
            "auto_sync": "daily",
            "redirect_uri": self._dexcom_redirect_uri(),
            "authorize_url": self._dexcom_authorize_url(cfg),
        })

    def _handle_dexcom_config(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            self._json(400, {"ok": False, "error": "Invalid JSON"})
            return
        cid = str(payload.get("client_id", "")).strip()
        secret = str(payload.get("client_secret", "")).strip()
        if not cid or not secret:
            self._json(400, {"ok": False, "error": "Client ID and Client Secret are both required."})
            return
        cfg = _load_cfg()
        if cfg.get("client_id") != cid:
            cfg.pop("access_token", None)
            cfg.pop("refresh_token", None)
            cfg.pop("expires_at", None)
        cfg["client_id"] = cid
        cfg["client_secret"] = secret
        _save_cfg(cfg)
        self._json(200, {"ok": True, "authorize_url": self._dexcom_authorize_url(cfg)})

    def _handle_dexcom_callback(self) -> None:
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        code = (qs.get("code") or [None])[0]
        err = (qs.get("error") or [None])[0]
        dest = "/glucose.html?dexcom="
        if err or not code:
            self.send_response(302)
            self.send_header("Location", dest + "error&msg=" + urllib.parse.quote(err or "no code returned"))
            self.end_headers()
            return
        cfg = _load_cfg()
        try:
            tok = _token_request({
                "client_id": cfg.get("client_id", ""),
                "client_secret": cfg.get("client_secret", ""),
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": self._dexcom_redirect_uri(),
            })
        except RuntimeError as e:
            self.send_response(302)
            self.send_header("Location", dest + "error&msg=" + urllib.parse.quote(str(e)))
            self.end_headers()
            return
        cfg["access_token"] = tok.get("access_token")
        if tok.get("refresh_token"):
            cfg["refresh_token"] = tok["refresh_token"]
        cfg["expires_at"] = (datetime.now() + timedelta(seconds=int(tok.get("expires_in", 7200)) - 60)).isoformat()
        _save_cfg(cfg)
        self.send_response(302)
        self.send_header("Location", dest + "connected")
        self.end_headers()

    def _dexcom_access_token(self, cfg: dict) -> str:
        return _access_token(cfg)

    def _handle_dexcom_sync(self) -> None:
        if not SYNC_LOCK.acquire(blocking=False):
            self._json(409, {"ok": False, "error": "A sync is already running — one moment."})
            return
        try:
            res = run_sync()
        except RuntimeError as e:
            code = 400 if str(e) in ("not_configured", "not_connected") else 502
            self._json(code, {"ok": False, "error": str(e)})
            return
        finally:
            SYNC_LOCK.release()
        self._json(200, {"ok": True, **res})
