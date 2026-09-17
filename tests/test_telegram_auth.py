from __future__ import annotations

import hashlib
import hmac
import json
import time
import unittest
from urllib.parse import parse_qsl, urlencode

from integratevibes.telegram_auth import TelegramAuthError, verify_init_data


BOT_TOKEN = "123456:test-token-for-unit-tests"


def signed_init_data(*, user_id: int = 42, auth_date: int | None = None) -> str:
    fields = {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "query_id": "AAExample",
        "user": json.dumps({"id": user_id, "first_name": "Ada"}, separators=(",", ":")),
    }
    data_check_string = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


class TelegramAuthTests(unittest.TestCase):
    def test_valid_signed_payload_returns_verified_user(self) -> None:
        user = verify_init_data(signed_init_data(user_id=5791501756), BOT_TOKEN)
        self.assertEqual(user.id, 5791501756)
        self.assertEqual(user.first_name, "Ada")

    def test_tampered_user_is_rejected(self) -> None:
        fields = dict(parse_qsl(signed_init_data(user_id=42)))
        fields["user"] = json.dumps({"id": 99, "first_name": "Mallory"}, separators=(",", ":"))
        payload = urlencode(fields)
        with self.assertRaisesRegex(TelegramAuthError, "signature"):
            verify_init_data(payload, BOT_TOKEN)

    def test_expired_payload_is_rejected(self) -> None:
        payload = signed_init_data(auth_date=int(time.time()) - 601)
        with self.assertRaisesRegex(TelegramAuthError, "expired"):
            verify_init_data(payload, BOT_TOKEN, max_age_seconds=600)

    def test_allowlist_is_enforced_after_signature_verification(self) -> None:
        payload = signed_init_data(user_id=42)
        with self.assertRaisesRegex(TelegramAuthError, "allowed"):
            verify_init_data(payload, BOT_TOKEN, allowed_user_ids={7})


if __name__ == "__main__":
    unittest.main()
