from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from integratevibes.integrations_store import IntegrationsStore


class IntegrationsStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = IntegrationsStore(Path(self.tempdir.name) / "integrations.db")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_profile_mapping_is_isolated_by_telegram_user(self) -> None:
        self.store.save_profile(telegram_user_id=10, zernio_profile_id="profile-a")
        self.store.save_profile(telegram_user_id=20, zernio_profile_id="profile-b")
        self.assertEqual(self.store.get_profile(10), "profile-a")
        self.assertEqual(self.store.get_profile(20), "profile-b")

    def test_profile_mapping_cannot_be_reassigned_to_another_user(self) -> None:
        self.store.save_profile(telegram_user_id=10, zernio_profile_id="profile-a")
        with self.assertRaisesRegex(ValueError, "already mapped"):
            self.store.save_profile(telegram_user_id=20, zernio_profile_id="profile-a")

    def test_user_mapping_cannot_be_changed_to_a_different_profile(self) -> None:
        self.store.save_profile(telegram_user_id=10, zernio_profile_id="profile-a")
        with self.assertRaisesRegex(ValueError, "cannot be changed"):
            self.store.save_profile(telegram_user_id=10, zernio_profile_id="profile-b")

    def test_connect_state_is_single_use_and_bound_to_profile(self) -> None:
        self.store.save_profile(telegram_user_id=10, zernio_profile_id="profile-a")
        self.store.create_connect_state(
            state="nonce",
            telegram_user_id=10,
            zernio_profile_id="profile-a",
            platform="instagram",
            expires_at=200,
        )
        state = self.store.consume_connect_state("nonce", now=100)
        self.assertEqual(state.telegram_user_id, 10)
        self.assertEqual(state.zernio_profile_id, "profile-a")
        self.assertEqual(state.platform, "instagram")
        self.assertIsNone(self.store.consume_connect_state("nonce", now=100))

    def test_expired_connect_state_cannot_be_consumed(self) -> None:
        self.store.create_connect_state(
            state="expired",
            telegram_user_id=1,
            zernio_profile_id="profile-a",
            platform="linkedin",
            expires_at=1,
        )
        self.assertIsNone(self.store.consume_connect_state("expired", now=2))

    def test_creating_state_purges_expired_rows(self) -> None:
        self.store.create_connect_state(
            state="expired-row",
            telegram_user_id=1,
            zernio_profile_id="profile-a",
            platform="linkedin",
            expires_at=1,
        )
        self.store.create_connect_state(
            state="active-row",
            telegram_user_id=1,
            zernio_profile_id="profile-a",
            platform="linkedin",
            expires_at=4_000_000_000,
        )
        with self.store._connect() as connection:
            states = [row[0] for row in connection.execute("SELECT state FROM zernio_connect_states")]
        self.assertEqual(states, ["active-row"])

    def test_webhook_event_ids_are_deduplicated(self) -> None:
        self.assertTrue(self.store.record_webhook_event("evt-1"))
        self.assertFalse(self.store.record_webhook_event("evt-1"))

    def test_webhook_retention_purges_old_event_ids(self) -> None:
        with self.store._connect() as connection:
            connection.execute(
                "INSERT INTO zernio_webhook_events (event_id, received_at) VALUES (?, ?)",
                ("old-event", 1),
            )
        self.assertTrue(self.store.record_webhook_event("fresh-event"))
        with self.store._connect() as connection:
            ids = [row[0] for row in connection.execute("SELECT event_id FROM zernio_webhook_events")]
        self.assertNotIn("old-event", ids)
        self.assertIn("fresh-event", ids)

    def test_telegram_code_is_hashed_expiring_and_bound_to_user_profile(self) -> None:
        self.store.save_telegram_code(
            code="ZRN-SECRET",
            telegram_user_id=10,
            zernio_profile_id="profile-a",
            expires_at=200,
        )
        self.assertTrue(
            self.store.telegram_code_belongs_to(
                code="ZRN-SECRET",
                telegram_user_id=10,
                zernio_profile_id="profile-a",
                now=100,
            )
        )
        self.assertFalse(
            self.store.telegram_code_belongs_to(
                code="ZRN-SECRET",
                telegram_user_id=20,
                zernio_profile_id="profile-b",
                now=100,
            )
        )
        self.assertFalse(
            self.store.telegram_code_belongs_to(
                code="ZRN-SECRET",
                telegram_user_id=10,
                zernio_profile_id="profile-a",
                now=201,
            )
        )
        with self.store._connect() as connection:
            stored = connection.execute("SELECT code_hash FROM zernio_telegram_codes").fetchone()[0]
        self.assertNotEqual(stored, "ZRN-SECRET")


if __name__ == "__main__":
    unittest.main()
