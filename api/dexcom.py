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

    def _handle_dexcom_sync(self) -> None:
        cfg = _load_cfg()
        if not (cfg.get("client_id") and cfg.get("client_secret")):
            self._json(400, {"ok": False, "error": "not_configured"})
            return
        try:
            token = self._dexcom_access_token(cfg)
        except RuntimeError as e:
            code = 400 if str(e) == "not_connected" else 502
            self._json(code, {"ok": False, "error": str(e)})
            return
        try:
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
        except RuntimeError as e:
            self._json(502, {"ok": False, "error": str(e)})
            return
        if not egvs:
            self._json(502, {"ok": False, "error": "Dexcom returned no glucose readings."})
            return
        egvs.sort()
        insulin_by_day, meal_by_day = defaultdict(list), defaultdict(list)
        insulin_events, meal_events = [], []
        for t, rec in events:
            et = str(rec.get("eventType", "")).lower()
            sub = str(rec.get("eventSubType", "") or "")
            val = rec.get("value")
            if not isinstance(val, (int, float)):
                continue
            if et == "insulin":
                sub_label = "Long-Acting" if "long" in sub.lower() else "Fast-Acting"
                insulin_by_day[t.date().isoformat()].append([t.strftime("%H:%M"), float(val), sub_label])
                insulin_events.append((t, float(val), sub_label))
            elif et == "carbs":
                meal_by_day[t.date().isoformat()].append([t.strftime("%H:%M"), float(val)])
                meal_events.append((t, float(val)))
        data = glucose_compute.build_glucose_data(
            egvs, insulin_by_day, meal_by_day, insulin_events, meal_events,
            "Dexcom API sync")
        DATA_PATH.write_text("window.GLUCOSE_DATA = " + json.dumps(data) + ";\n", encoding="utf-8")
        cfg = _load_cfg()
        cfg["last_sync"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        _save_cfg(cfg)
        self._json(200, {"ok": True, "readings": data["total_readings"],
                         "range": data["range"], "synced_at": cfg["last_sync"]})
