from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from typing import Protocol
from urllib.parse import urlencode

from .integrations_store import IntegrationsStore


PLATFORMS = {
    "instagram": {"label": "Instagram", "category": "Meta"},
    "facebook": {"label": "Facebook", "category": "Meta"},
    "linkedin": {"label": "LinkedIn", "category": "Profesional"},
    "tiktok": {"label": "TikTok", "category": "Video"},
    "youtube": {"label": "YouTube", "category": "Video"},
    "twitter": {"label": "X / Twitter", "category": "Social"},
    "threads": {"label": "Threads", "category": "Meta"},
    "telegram": {"label": "Telegram", "category": "Mensajería"},
    "whatsapp": {"label": "WhatsApp", "category": "Mensajería"},
}


class IntegrationServiceError(ValueError):
    pass


class ZernioProtocol(Protocol):
    def create_profile(self, name: str, *, idempotency_key: str) -> str: ...
    def list_accounts(self, profile_id: str) -> list[dict]: ...
    def get_connect_url(self, platform: str, *, profile_id: str, redirect_url: str) -> str: ...
    def start_telegram_connect(self, profile_id: str) -> dict: ...
    def check_telegram_connect(self, code: str) -> dict: ...


def verify_zernio_webhook(body: bytes, signature: str, secret: str) -> bool:
    if not signature or not secret:
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


def _normalize_account(account: dict) -> dict:
    needs_reconnection = bool(account.get("needsReconnection"))
    raw_status = str(account.get("status", "")).lower()
    is_active = bool(account.get("isActive")) or raw_status in {"active", "connected"}
    if needs_reconnection:
        status = "needs_reconnection"
    elif is_active:
        status = "connected"
    else:
        status = "disconnected"
    return {
        "accountId": str(account.get("accountId") or account.get("_id") or ""),
        "platform": str(account.get("platform") or "").lower(),
        "username": str(account.get("username") or account.get("displayName") or ""),
        "status": status,
        "needsReconnection": needs_reconnection,
    }


class IntegrationService:
    def __init__(
        self,
        client: ZernioProtocol,
        store: IntegrationsStore,
        *,
        public_base_url: str,
    ) -> None:
        self.client = client
        self.store = store
        self.public_base_url = public_base_url.rstrip("/")
        self._profile_lock = threading.Lock()

    def _profile_for(self, telegram_user_id: int) -> str:
        existing = self.store.get_profile(telegram_user_id)
        if existing:
            return existing
        with self._profile_lock:
            existing = self.store.get_profile(telegram_user_id)
            if existing:
                return existing
            name = f"tg_{telegram_user_id}"
            profile_id = self.client.create_profile(name, idempotency_key=f"integratevibes-{name}")
            self.store.save_profile(
                telegram_user_id=telegram_user_id,
                zernio_profile_id=profile_id,
            )
            return profile_id

    def status(self, telegram_user_id: int) -> dict:
        profile_id = self._profile_for(telegram_user_id)
        accounts = [_normalize_account(account) for account in self.client.list_accounts(profile_id)]
        account_by_platform = {account["platform"]: account for account in accounts if account["platform"]}
        platforms = []
        for slug, metadata in PLATFORMS.items():
            account = account_by_platform.get(slug)
            platforms.append(
                {
                    "platform": slug,
                    **metadata,
                    "status": account["status"] if account else "disconnected",
                    "accountId": account["accountId"] if account else "",
                    "username": account["username"] if account else "",
                    "needsReconnection": account["needsReconnection"] if account else False,
                    "connectionMode": "code" if slug == "telegram" else "oauth",
                }
            )
        return {"ok": True, "accounts": accounts, "platforms": platforms}

    def create_connect(self, telegram_user_id: int, platform: str) -> dict:
        platform = platform.strip().lower()
        if platform not in PLATFORMS:
            raise IntegrationServiceError("unsupported Zernio platform")
        if platform == "telegram":
            raise IntegrationServiceError("Telegram uses a code flow")
        profile_id = self._profile_for(telegram_user_id)
        state = secrets.token_urlsafe(24)
        self.store.create_connect_state(
            state=state,
            telegram_user_id=telegram_user_id,
            zernio_profile_id=profile_id,
            platform=platform,
            expires_at=int(time.time()) + 900,
        )
        callback = f"{self.public_base_url}/integrations/zernio/callback?{urlencode({'state': state})}"
        try:
            auth_url = self.client.get_connect_url(
                platform,
                profile_id=profile_id,
                redirect_url=callback,
            )
        except Exception:
            self.store.delete_connect_state(state)
            raise
        return {"ok": True, "platform": platform, "authUrl": auth_url}

    def start_telegram(self, telegram_user_id: int) -> dict:
        profile_id = self._profile_for(telegram_user_id)
        payload = self.client.start_telegram_connect(profile_id)
        code = str(payload.get("code", ""))
        if not code.startswith("ZRN-") or len(code) > 64:
            raise IntegrationServiceError("Zernio returned an invalid Telegram connection code")
        try:
            expires_in = int(payload.get("expiresIn", 900))
        except (TypeError, ValueError):
            expires_in = 900
        self.store.save_telegram_code(
            code=code,
            telegram_user_id=telegram_user_id,
            zernio_profile_id=profile_id,
            expires_at=int(time.time()) + max(1, min(expires_in, 900)),
        )
        return {"ok": True, **payload}

    def check_telegram(self, telegram_user_id: int, code: str) -> dict:
        if not code.startswith("ZRN-") or len(code) > 64:
            raise IntegrationServiceError("invalid Telegram connection code")
        profile_id = self._profile_for(telegram_user_id)
        if not self.store.telegram_code_belongs_to(
            code=code,
            telegram_user_id=telegram_user_id,
            zernio_profile_id=profile_id,
        ):
            raise IntegrationServiceError("Telegram connection code does not belong to this user")
        payload = self.client.check_telegram_connect(code)
        status = str(payload.get("status", ""))
        if status == "connected":
            returned_account = payload.get("account")
            returned_account_id = ""
            if isinstance(returned_account, dict):
                returned_account_id = str(returned_account.get("_id") or returned_account.get("id") or "")
            matching_account = None
            for candidate in self.client.list_accounts(profile_id):
                normalized = _normalize_account(candidate)
                if normalized["platform"] != "telegram" or normalized["status"] != "connected":
                    continue
                if returned_account_id and normalized["accountId"] != returned_account_id:
                    continue
                matching_account = normalized
                break
            if matching_account is None:
                raise IntegrationServiceError("Telegram connection could not be confirmed for this profile")
            self.store.delete_telegram_code(code)
        elif status == "expired":
            self.store.delete_telegram_code(code)
        return {"ok": True, "status": status}

    def finish_callback(
        self,
        *,
        state: str,
        returned_profile_id: str | None,
        returned_platform: str | None,
        error: str | None,
    ) -> dict:
        connect_state = self.store.consume_connect_state(state)
        if connect_state is None:
            raise IntegrationServiceError("connection state is invalid or expired")
        if error:
            return {
                "ok": False,
                "platform": connect_state.platform,
                "error": "provider_authorization_failed",
            }
        if not returned_profile_id or not returned_platform:
            raise IntegrationServiceError("callback is missing required Zernio identity parameters")
        if returned_profile_id != connect_state.zernio_profile_id:
            raise IntegrationServiceError("Zernio profile mismatch")
        if returned_platform != connect_state.platform:
            raise IntegrationServiceError("Zernio platform mismatch")

        accounts = self.client.list_accounts(connect_state.zernio_profile_id)
        normalized_accounts = [_normalize_account(item) for item in accounts]
        account = next(
            (
                item
                for item in normalized_accounts
                if item["platform"] == connect_state.platform and item["status"] == "connected"
            ),
            None,
        )
        if account is None:
            return {"ok": False, "platform": connect_state.platform, "error": "connection_not_confirmed"}
        return {
            "ok": True,
            "platform": connect_state.platform,
            "accountId": account["accountId"],
            "username": account["username"],
        }
