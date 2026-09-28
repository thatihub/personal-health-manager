""" Authentication guards for the vault and admin areas. """
from __future__ import annotations

import base64
import binascii
import hmac

from api import config


class AuthMixin:
        def _admin_auth_enabled(self) -> bool:
            return bool(config.ADMIN_AUTH_USER and config.ADMIN_AUTH_PASS)

        def _vault_auth_enabled(self) -> bool:
            return bool(config.VAULT_AUTH_USER and config.VAULT_AUTH_PASS)

        def _is_vault_protected_path(self) -> bool:
            path = self.path.split("?", 1)[0]
            return path in {"/vault.html", "/vault_data.json", "/medications.html", "/api/medications"}

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
            if self._is_authorized(config.VAULT_AUTH_USER, config.VAULT_AUTH_PASS):
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
            if self._is_authorized(config.ADMIN_AUTH_USER, config.ADMIN_AUTH_PASS):
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
