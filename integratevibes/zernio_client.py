from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


class ZernioError(RuntimeError):
    """Safe public error for Zernio API failures."""


Transport = Callable[[str, str, dict[str, str], dict | None], tuple[int, dict]]


def _default_transport(
    method: str,
    url: str,
    headers: dict[str, str],
    body: dict | None,
) -> tuple[int, dict]:
    raw_body = json.dumps(body).encode("utf-8") if body is not None else None
    request = Request(url, data=raw_body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read(2_000_000)
            status = response.status
    except HTTPError as exc:
        status = exc.code
        raw = exc.read(2_000_000)
    except (TimeoutError, URLError) as exc:
        raise ZernioError("Zernio is temporarily unavailable") from exc
    try:
        payload = json.loads(raw or b"{}")
    except json.JSONDecodeError as exc:
        raise ZernioError(f"Zernio returned an invalid response ({status})") from exc
    return status, payload if isinstance(payload, dict) else {"data": payload}


class ZernioClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://zernio.com/api",
        transport: Transport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("Zernio API key is required")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.transport = transport or _default_transport

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        body: dict | None = None,
        extra_headers: dict[str, str] | None = None,
        accepted_statuses: set[int] | None = None,
    ) -> tuple[int, dict]:
        url = f"{self.base_url}{path}"
        if query:
            clean_query = {key: value for key, value in query.items() if value is not None}
            url = f"{url}?{urlencode(clean_query)}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            **(extra_headers or {}),
        }
        status, payload = self.transport(method, url, headers, body)
        allowed = accepted_statuses or {200}
        if status not in allowed:
            raise ZernioError(f"Zernio request failed ({status})")
        return status, payload

    def create_profile(self, name: str, *, idempotency_key: str) -> str:
        status, payload = self._request(
            "POST",
            "/v1/profiles",
            body={"name": name, "description": "Integratevibes Telegram Mini App"},
            extra_headers={"Idempotency-Key": idempotency_key},
            accepted_statuses={201, 409},
        )
        if status == 409:
            details = payload.get("details")
            existing = details.get("existingProfileId") if isinstance(details, dict) else None
            if payload.get("code") == "profile_name_conflict" and existing:
                return str(existing)
            raise ZernioError("Zernio profile could not be resolved (409)")
        profile = payload.get("profile")
        profile_id = profile.get("_id") if isinstance(profile, dict) else None
        if not profile_id:
            raise ZernioError("Zernio did not return a profile identifier")
        return str(profile_id)

    def list_accounts(self, profile_id: str) -> list[dict]:
        accounts: list[dict] = []
        page = 1
        while True:
            _, payload = self._request(
                "GET",
                "/v1/accounts",
                query={"profileId": profile_id, "page": page, "limit": 100},
            )
            raw_accounts = payload.get("accounts", payload.get("data", []))
            if isinstance(raw_accounts, list):
                accounts.extend(item for item in raw_accounts if isinstance(item, dict))
            pagination = payload.get("pagination")
            if not isinstance(pagination, dict):
                break
            current_page = int(pagination.get("page", page))
            total_pages = int(pagination.get("totalPages", current_page))
            if current_page >= total_pages:
                break
            page = current_page + 1
        return accounts

    def get_connect_url(
        self,
        platform: str,
        *,
        profile_id: str,
        redirect_url: str,
    ) -> str:
        query: dict[str, Any] = {
            "profileId": profile_id,
            "redirect_url": redirect_url,
            "headless": "false",
        }
        if platform == "whatsapp":
            query.update({"onboarding": "api", "signup": "hosted"})
        _, payload = self._request("GET", f"/v1/connect/{platform}", query=query)
        auth_url = payload.get("authUrl")
        parsed = urlparse(str(auth_url or ""))
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise ZernioError("Zernio did not return a valid HTTPS authorization URL")
        return str(auth_url)

    def start_telegram_connect(self, profile_id: str) -> dict:
        _, payload = self._request(
            "GET",
            "/v1/connect/telegram",
            query={"profileId": profile_id},
        )
        return payload

    def check_telegram_connect(self, code: str) -> dict:
        _, payload = self._request("PATCH", "/v1/connect/telegram", query={"code": code})
        return payload
