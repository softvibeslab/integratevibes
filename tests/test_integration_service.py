from __future__ import annotations

import hashlib
import hmac
import tempfile
import threading
import time
import unittest
from pathlib import Path

from integratevibes.integration_service import (
    IntegrationService,
    IntegrationServiceError,
    verify_zernio_webhook,
)
from integratevibes.integrations_store import IntegrationsStore


class FakeZernio:
    def __init__(self) -> None:
        self.accounts: list[dict] = []
        self.connect_calls: list[tuple[str, str, str]] = []
        self.created: list[str] = []

    def create_profile(self, name: str, *, idempotency_key: str) -> str:
        self.created.append(name)
        return f"profile-{name}"

    def list_accounts(self, profile_id: str) -> list[dict]:
        return list(self.accounts)

    def get_connect_url(self, platform: str, *, profile_id: str, redirect_url: str) -> str:
        self.connect_calls.append((platform, profile_id, redirect_url))
        return f"https://zernio.com/connect/{platform}"

    def start_telegram_connect(self, profile_id: str) -> dict:
        return {"code": "ZRN-ABC123", "expiresIn": 900, "botUsername": "ZernioScheduleBot"}

    def check_telegram_connect(self, code: str) -> dict:
        return {
            "status": "connected" if code == "ZRN-ABC123" else "pending",
            "account": {"_id": "telegram-1"},
        }


class IntegrationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = IntegrationsStore(Path(self.tempdir.name) / "integrations.db")
        self.client = FakeZernio()
        self.service = IntegrationService(
            self.client,
            self.store,
            public_base_url="https://auth.softvibes.art",
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_status_creates_one_profile_and_normalizes_accounts(self) -> None:
        self.client.accounts = [
            {
                "_id": "account-1",
                "platform": "instagram",
                "username": "softvibes",
                "isActive": True,
                "needsReconnection": False,
            }
        ]
        first = self.service.status(42)
        second = self.service.status(42)
        self.assertEqual(self.client.created, ["tg_42"])
        self.assertEqual(first["accounts"][0]["status"], "connected")
        self.assertEqual(first["accounts"], second["accounts"])
        self.assertNotIn("profileId", first)

    def test_concurrent_status_creates_only_one_profile(self) -> None:
        original_create = self.client.create_profile

        def slow_create(name: str, *, idempotency_key: str) -> str:
            time.sleep(0.03)
            return original_create(name, idempotency_key=idempotency_key)

        self.client.create_profile = slow_create
        barrier = threading.Barrier(3)
        errors: list[Exception] = []

        def worker() -> None:
            barrier.wait()
            try:
                self.service.status(99)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(self.client.created, ["tg_99"])

    def test_connect_creates_bound_callback_state(self) -> None:
        result = self.service.create_connect(42, "linkedin")
        self.assertEqual(result["authUrl"], "https://zernio.com/connect/linkedin")
        platform, profile_id, redirect_url = self.client.connect_calls[0]
        self.assertEqual(platform, "linkedin")
        self.assertIn("state=", redirect_url)
        state = redirect_url.split("state=", 1)[1]
        stored = self.store.consume_connect_state(state)
        self.assertEqual(stored.telegram_user_id, 42)
        self.assertEqual(stored.zernio_profile_id, profile_id)

    def test_connect_rejects_unknown_platform(self) -> None:
        with self.assertRaisesRegex(IntegrationServiceError, "unsupported"):
            self.service.create_connect(42, "gmail")

    def test_connect_failure_removes_orphaned_state(self) -> None:
        def fail_connect(*args, **kwargs):
            raise RuntimeError("provider unavailable")

        self.client.get_connect_url = fail_connect
        with self.assertRaisesRegex(RuntimeError, "provider unavailable"):
            self.service.create_connect(42, "linkedin")
        with self.store._connect() as connection:
            count = connection.execute("SELECT COUNT(*) FROM zernio_connect_states").fetchone()[0]
        self.assertEqual(count, 0)

    def test_telegram_uses_code_flow_instead_of_oauth(self) -> None:
        with self.assertRaisesRegex(IntegrationServiceError, "code flow"):
            self.service.create_connect(42, "telegram")
        result = self.service.start_telegram(42)
        self.assertEqual(result["code"], "ZRN-ABC123")
        with self.assertRaisesRegex(IntegrationServiceError, "does not belong"):
            self.service.check_telegram(43, result["code"])
        self.client.accounts = [
            {"_id": "telegram-1", "platform": "telegram", "isActive": True}
        ]
        checked = self.service.check_telegram(42, result["code"])
        self.assertEqual(checked["status"], "connected")
        self.assertNotIn("account", checked)

    def test_telegram_connected_account_must_belong_to_expected_profile(self) -> None:
        result = self.service.start_telegram(42)
        self.client.accounts = [
            {"_id": "another-account", "platform": "telegram", "isActive": True}
        ]
        with self.assertRaisesRegex(IntegrationServiceError, "could not be confirmed"):
            self.service.check_telegram(42, result["code"])

    def test_callback_reconciles_against_accounts(self) -> None:
        self.client.accounts = [{"accountId": "a1", "platform": "instagram", "isActive": True}]
        result = self.service.create_connect(42, "instagram")
        state = self.client.connect_calls[-1][2].split("state=", 1)[1]
        profile_id = self.store.get_profile(42)
        callback = self.service.finish_callback(
            state=state,
            returned_profile_id=profile_id,
            returned_platform="instagram",
            error=None,
        )
        self.assertTrue(callback["ok"])
        self.assertEqual(callback["accountId"], "a1")

    def test_callback_skips_inactive_account_before_active_account(self) -> None:
        self.client.accounts = [
            {"accountId": "old", "platform": "instagram", "isActive": False},
            {"accountId": "active", "platform": "instagram", "isActive": True},
        ]
        self.service.create_connect(42, "instagram")
        state = self.client.connect_calls[-1][2].split("state=", 1)[1]
        callback = self.service.finish_callback(
            state=state,
            returned_profile_id=self.store.get_profile(42),
            returned_platform="instagram",
            error=None,
        )
        self.assertTrue(callback["ok"])
        self.assertEqual(callback["accountId"], "active")

    def test_callback_rejects_profile_mismatch(self) -> None:
        self.service.create_connect(42, "instagram")
        state = self.client.connect_calls[-1][2].split("state=", 1)[1]
        with self.assertRaisesRegex(IntegrationServiceError, "mismatch"):
            self.service.finish_callback(
                state=state,
                returned_profile_id="another-profile",
                returned_platform="instagram",
                error=None,
            )

    def test_callback_success_requires_profile_and_platform(self) -> None:
        self.service.create_connect(42, "instagram")
        state = self.client.connect_calls[-1][2].split("state=", 1)[1]
        with self.assertRaisesRegex(IntegrationServiceError, "missing required"):
            self.service.finish_callback(
                state=state,
                returned_profile_id=None,
                returned_platform=None,
                error=None,
            )

    def test_webhook_hmac_verification(self) -> None:
        body = b'{"id":"evt-1"}'
        signature = hmac.new(b"webhook-secret", body, hashlib.sha256).hexdigest()
        self.assertTrue(verify_zernio_webhook(body, signature, "webhook-secret"))
        self.assertFalse(verify_zernio_webhook(body + b"x", signature, "webhook-secret"))


if __name__ == "__main__":
    unittest.main()
