from __future__ import annotations

import hashlib
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ConnectState:
    state: str
    telegram_user_id: int
    zernio_profile_id: str
    platform: str
    expires_at: int


class IntegrationsStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS zernio_profiles (
                    telegram_user_id INTEGER PRIMARY KEY,
                    zernio_profile_id TEXT NOT NULL UNIQUE,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS zernio_connect_states (
                    state TEXT PRIMARY KEY,
                    telegram_user_id INTEGER NOT NULL,
                    zernio_profile_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    expires_at INTEGER NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS zernio_webhook_events (
                    event_id TEXT PRIMARY KEY,
                    received_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS zernio_telegram_codes (
                    code_hash TEXT PRIMARY KEY,
                    telegram_user_id INTEGER NOT NULL,
                    zernio_profile_id TEXT NOT NULL,
                    expires_at INTEGER NOT NULL,
                    created_at INTEGER NOT NULL
                );
                """
            )

    def get_profile(self, telegram_user_id: int) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT zernio_profile_id FROM zernio_profiles WHERE telegram_user_id = ?",
                (telegram_user_id,),
            ).fetchone()
        return str(row["zernio_profile_id"]) if row else None

    def save_profile(self, *, telegram_user_id: int, zernio_profile_id: str) -> None:
        now = int(time.time())
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT zernio_profile_id FROM zernio_profiles WHERE telegram_user_id = ?",
                    (telegram_user_id,),
                ).fetchone()
                if existing:
                    if str(existing["zernio_profile_id"]) != zernio_profile_id:
                        raise ValueError("Telegram user mapping cannot be changed")
                    connection.execute(
                        "UPDATE zernio_profiles SET updated_at = ? WHERE telegram_user_id = ?",
                        (now, telegram_user_id),
                    )
                    return
                connection.execute(
                    """
                    INSERT INTO zernio_profiles (
                        telegram_user_id, zernio_profile_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (telegram_user_id, zernio_profile_id, now, now),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("Zernio profile is already mapped to another Telegram user") from exc

    def create_connect_state(
        self,
        *,
        state: str,
        telegram_user_id: int,
        zernio_profile_id: str,
        platform: str,
        expires_at: int,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM zernio_connect_states WHERE expires_at < ?",
                (int(time.time()),),
            )
            connection.execute(
                """
                INSERT INTO zernio_connect_states (
                    state, telegram_user_id, zernio_profile_id, platform, expires_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    state,
                    telegram_user_id,
                    zernio_profile_id,
                    platform,
                    expires_at,
                    int(time.time()),
                ),
            )

    def delete_connect_state(self, state: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM zernio_connect_states WHERE state = ?", (state,))

    def consume_connect_state(self, state: str, *, now: int | None = None) -> ConnectState | None:
        current_time = int(time.time()) if now is None else now
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT state, telegram_user_id, zernio_profile_id, platform, expires_at
                FROM zernio_connect_states
                WHERE state = ?
                """,
                (state,),
            ).fetchone()
            if row is None:
                return None
            connection.execute("DELETE FROM zernio_connect_states WHERE state = ?", (state,))
            if int(row["expires_at"]) < current_time:
                return None
            return ConnectState(
                state=str(row["state"]),
                telegram_user_id=int(row["telegram_user_id"]),
                zernio_profile_id=str(row["zernio_profile_id"]),
                platform=str(row["platform"]),
                expires_at=int(row["expires_at"]),
            )

    @staticmethod
    def _code_hash(code: str) -> str:
        return hashlib.sha256(code.encode("utf-8")).hexdigest()

    def save_telegram_code(
        self,
        *,
        code: str,
        telegram_user_id: int,
        zernio_profile_id: str,
        expires_at: int,
    ) -> None:
        now = int(time.time())
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM zernio_telegram_codes WHERE expires_at < ? OR telegram_user_id = ?",
                (now, telegram_user_id),
            )
            connection.execute(
                """
                INSERT INTO zernio_telegram_codes (
                    code_hash, telegram_user_id, zernio_profile_id, expires_at, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    self._code_hash(code),
                    telegram_user_id,
                    zernio_profile_id,
                    expires_at,
                    now,
                ),
            )

    def telegram_code_belongs_to(
        self,
        *,
        code: str,
        telegram_user_id: int,
        zernio_profile_id: str,
        now: int | None = None,
    ) -> bool:
        current_time = int(time.time()) if now is None else now
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM zernio_telegram_codes
                WHERE code_hash = ? AND telegram_user_id = ?
                  AND zernio_profile_id = ? AND expires_at >= ?
                """,
                (
                    self._code_hash(code),
                    telegram_user_id,
                    zernio_profile_id,
                    current_time,
                ),
            ).fetchone()
        return row is not None

    def delete_telegram_code(self, code: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM zernio_telegram_codes WHERE code_hash = ?",
                (self._code_hash(code),),
            )

    def record_webhook_event(self, event_id: str) -> bool:
        now = int(time.time())
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM zernio_webhook_events WHERE received_at < ?",
                (now - 90 * 24 * 60 * 60,),
            )
            cursor = connection.execute(
                "INSERT OR IGNORE INTO zernio_webhook_events (event_id, received_at) VALUES (?, ?)",
                (event_id, now),
            )
            return cursor.rowcount == 1
