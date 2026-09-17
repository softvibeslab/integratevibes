from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import http.client
import sqlite3
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from tests.test_telegram_auth import BOT_TOKEN, signed_init_data


ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = ROOT / "composio-telegram-miniapp" / "server.py"
SPEC = importlib.util.spec_from_file_location("integratevibes_http_server", SERVER_PATH)
assert SPEC and SPEC.loader
server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(server)


class FakeStore:
    def __init__(self) -> None:
        self.fail = False
        self.sessions: dict[str, int] = {}

    def save_session(self, token: str, telegram_user_id: int, expires_at: int) -> None:
        self.sessions[token] = telegram_user_id

    def get_session_user(self, token: str) -> int | None:
        return self.sessions.get(token)

    def record_webhook_event(self, event_id: str) -> bool:
        if self.fail:
            raise sqlite3.OperationalError("database is locked")
        return True


class FakeService:
    def __init__(self) -> None:
        self.status_users: list[int] = []
        self.connect_calls: list[tuple[int, str]] = []
        self.store = FakeStore()

    def status(self, user_id: int) -> dict:
        self.status_users.append(user_id)
        return {"ok": True, "platforms": []}

    def create_connect(self, user_id: int, platform: str) -> dict:
        self.connect_calls.append((user_id, platform))
        return {"ok": True, "authUrl": "https://zernio.com/connect/example"}


class HttpRoutesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fake = FakeService()
        server.ZERNIO_SERVICE = cls.fake
        server.ZERNIO_WEBHOOK_SECRET = "webhook-secret"
        server.TELEGRAM_BOT_TOKEN = BOT_TOKEN
        server.ALLOWED_TELEGRAM_USERS = {42}
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.thread.join(timeout=2)

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        body: dict | None = None,
        auth: bool = True,
        extra_headers: dict[str, str] | None = None,
    ):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json", **(extra_headers or {})}
        if auth:
            headers["X-Telegram-Init-Data"] = signed_init_data(user_id=42)
        request = Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def raw_request(self, path: str, *, body: bytes, headers: dict[str, str]):
        request = Request(self.base + path, data=body, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_zernio_status_rejects_missing_telegram_identity(self) -> None:
        status, payload = self.request("/api/zernio/status", auth=False)
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"], "telegram_auth_required")

    def test_zernio_auth_fails_closed_without_allowlist(self) -> None:
        original = server.ALLOWED_TELEGRAM_USERS
        server.ALLOWED_TELEGRAM_USERS = set()
        try:
            status, payload = self.request("/api/zernio/status")
        finally:
            server.ALLOWED_TELEGRAM_USERS = original
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"], "telegram_auth_required")

    def test_zernio_status_uses_verified_telegram_user(self) -> None:
        status, payload = self.request("/api/zernio/status")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(self.fake.status_users[-1], 42)

    def test_zernio_session_allows_requests_without_reusing_init_data(self) -> None:
        status, payload = self.request("/api/zernio/session", method="POST", body={})
        self.assertEqual(status, 200)
        self.assertTrue(payload["sessionToken"])

        status, payload = self.request(
            "/api/zernio/status",
            auth=False,
            extra_headers={"X-Zernio-Session": payload["sessionToken"]},
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(self.fake.status_users[-1], 42)

    def test_zernio_rejects_invalid_session_token(self) -> None:
        status, payload = self.request(
            "/api/zernio/status",
            auth=False,
            extra_headers={"X-Zernio-Session": "invalid"},
        )
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"], "telegram_auth_required")

    def test_zernio_session_rechecks_allowlist(self) -> None:
        status, payload = self.request("/api/zernio/session", method="POST", body={})
        self.assertEqual(status, 200)
        original = server.ALLOWED_TELEGRAM_USERS
        server.ALLOWED_TELEGRAM_USERS = set()
        try:
            status, denied = self.request(
                "/api/zernio/status",
                auth=False,
                extra_headers={"X-Zernio-Session": payload["sessionToken"]},
            )
        finally:
            server.ALLOWED_TELEGRAM_USERS = original
        self.assertEqual(status, 401)
        self.assertEqual(denied["error"], "telegram_auth_required")

    def test_zernio_connect_uses_verified_user_and_platform(self) -> None:
        status, payload = self.request(
            "/api/zernio/connect",
            method="POST",
            body={"platform": "instagram"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["authUrl"].startswith("https://"))
        self.assertEqual(self.fake.connect_calls[-1], (42, "instagram"))

    def test_webhook_rejects_non_object_json(self) -> None:
        body = b"[]"
        signature = hmac.new(b"webhook-secret", body, hashlib.sha256).hexdigest()
        status, payload = self.raw_request(
            "/webhooks/zernio",
            body=body,
            headers={"X-Zernio-Signature": signature, "Content-Type": "application/json"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "invalid_webhook")

    def test_webhook_rejects_invalid_content_length_with_json_response(self) -> None:
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.httpd.server_address[1], timeout=5
        )
        connection.putrequest("POST", "/webhooks/zernio")
        connection.putheader("Content-Length", "abc")
        connection.endheaders()
        response = connection.getresponse()
        payload = json.loads(response.read())
        connection.close()
        self.assertEqual(response.status, 400)
        self.assertEqual(payload["error"], "invalid_webhook")

    def test_webhook_returns_503_for_transient_store_failure(self) -> None:
        body = b'{"id":"event-locked"}'
        signature = hmac.new(b"webhook-secret", body, hashlib.sha256).hexdigest()
        self.fake.store.fail = True
        try:
            status, payload = self.raw_request(
                "/webhooks/zernio",
                body=body,
                headers={"X-Zernio-Signature": signature, "Content-Type": "application/json"},
            )
        finally:
            self.fake.store.fail = False
        self.assertEqual(status, 503)
        self.assertEqual(payload["error"], "webhook_temporarily_unavailable")


if __name__ == "__main__":
    unittest.main()
