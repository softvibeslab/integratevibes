from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl


class TelegramAuthError(ValueError):
    """Raised when Telegram Mini App identity cannot be verified."""


@dataclass(frozen=True)
class TelegramUser:
    id: int
    first_name: str = ""
    last_name: str = ""
    username: str = ""
    language_code: str = ""


def verify_init_data(
    init_data: str,
    bot_token: str,
    *,
    max_age_seconds: int = 600,
    allowed_user_ids: set[int] | None = None,
    now: int | None = None,
) -> TelegramUser:
    if not init_data or not bot_token:
        raise TelegramAuthError("missing Telegram authentication")

    fields = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    received_hash = fields.pop("hash", "")
    if not received_hash:
        raise TelegramAuthError("missing Telegram signature")

    data_check_string = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(received_hash, expected_hash):
        raise TelegramAuthError("invalid Telegram signature")

    try:
        auth_date = int(fields["auth_date"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TelegramAuthError("invalid Telegram auth date") from exc
    current_time = int(time.time()) if now is None else now
    if auth_date > current_time + 30 or current_time - auth_date > max_age_seconds:
        raise TelegramAuthError("Telegram authentication expired")

    try:
        raw_user = json.loads(fields["user"])
        user_id = int(raw_user["id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise TelegramAuthError("invalid Telegram user") from exc
    if allowed_user_ids is not None and user_id not in allowed_user_ids:
        raise TelegramAuthError("Telegram user is not allowed")

    return TelegramUser(
        id=user_id,
        first_name=str(raw_user.get("first_name", "")),
        last_name=str(raw_user.get("last_name", "")),
        username=str(raw_user.get("username", "")),
        language_code=str(raw_user.get("language_code", "")),
    )
