from __future__ import annotations

import unittest
from urllib.parse import parse_qs, urlparse

from integratevibes.zernio_client import ZernioClient, ZernioError


class FakeTransport:
    def __init__(self, responses: list[tuple[int, dict]]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, str], dict | None]] = []

    def __call__(self, method: str, url: str, headers: dict[str, str], body: dict | None):
        self.calls.append((method, url, headers, body))
        return self.responses.pop(0)


class ZernioClientTests(unittest.TestCase):
    def test_create_profile_uses_bearer_and_idempotency(self) -> None:
        transport = FakeTransport([(201, {"profile": {"_id": "profile-1"}})])
        client = ZernioClient("secret", transport=transport)
        profile_id = client.create_profile("tg_42", idempotency_key="telegram-42")
        self.assertEqual(profile_id, "profile-1")
        method, url, headers, body = transport.calls[0]
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/v1/profiles"))
        self.assertEqual(headers["Authorization"], "Bearer secret")
        self.assertEqual(headers["Idempotency-Key"], "telegram-42")
        self.assertEqual(body["name"], "tg_42")

    def test_profile_name_conflict_reuses_existing_profile(self) -> None:
        transport = FakeTransport([
            (409, {"code": "profile_name_conflict", "details": {"existingProfileId": "existing"}})
        ])
        client = ZernioClient("secret", transport=transport)
        self.assertEqual(client.create_profile("tg_42", idempotency_key="telegram-42"), "existing")

    def test_connect_url_uses_standard_flow_and_whatsapp_hosted_signup(self) -> None:
        transport = FakeTransport([(200, {"authUrl": "https://zernio.com/connect/example"})])
        client = ZernioClient("secret", transport=transport)
        result = client.get_connect_url(
            "whatsapp",
            profile_id="profile-1",
            redirect_url="https://auth.softvibes.art/integrations/zernio/callback?state=nonce",
        )
        self.assertEqual(result, "https://zernio.com/connect/example")
        query = parse_qs(urlparse(transport.calls[0][1]).query)
        self.assertEqual(query["profileId"], ["profile-1"])
        self.assertEqual(query["headless"], ["false"])
        self.assertEqual(query["onboarding"], ["api"])
        self.assertEqual(query["signup"], ["hosted"])
        self.assertNotIn("language", query)

    def test_non_https_connect_url_is_rejected(self) -> None:
        transport = FakeTransport([(200, {"authUrl": "http://unsafe.example/connect"})])
        client = ZernioClient("secret", transport=transport)
        with self.assertRaisesRegex(ZernioError, "HTTPS"):
            client.get_connect_url("instagram", profile_id="p", redirect_url="https://example.com/cb")

    def test_list_accounts_follows_pagination(self) -> None:
        transport = FakeTransport([
            (200, {"accounts": [{"_id": "a"}], "pagination": {"page": 1, "totalPages": 2}}),
            (200, {"accounts": [{"_id": "b"}], "pagination": {"page": 2, "totalPages": 2}}),
        ])
        client = ZernioClient("secret", transport=transport)
        self.assertEqual([a["_id"] for a in client.list_accounts("profile-1")], ["a", "b"])

    def test_provider_error_is_redacted(self) -> None:
        transport = FakeTransport([(401, {"message": "leaked provider detail"})])
        client = ZernioClient("secret", transport=transport)
        with self.assertRaisesRegex(ZernioError, "401") as caught:
            client.list_accounts("profile-1")
        self.assertNotIn("leaked", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
