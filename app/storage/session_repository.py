from __future__ import annotations

import json
from datetime import datetime

from app.domain import AuthSession, PlatformName, utc_now
from app.storage.database import MvpDatabase


class PlatformSessionRepository:
    def __init__(self, database: MvpDatabase) -> None:
        self.database = database

    async def save(self, session: AuthSession, status: str = "logged_in") -> None:
        now = utc_now().isoformat()
        payload = json.dumps(
            {
                "platform": session.platform,
                "cookies": session.cookies,
                "headers": session.headers,
                "csrf_token": session.csrf_token,
                "device_id": session.device_id,
                "created_at": session.created_at.isoformat(),
            },
            ensure_ascii=False,
        )
        async with self.database.connect() as connection:
            await connection.execute(
                """
                INSERT INTO platform_sessions (
                    platform, status, auth_session_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(platform) DO UPDATE SET
                    status=excluded.status,
                    auth_session_json=excluded.auth_session_json,
                    updated_at=excluded.updated_at
                """,
                (session.platform, status, payload, now, now),
            )
            await connection.commit()

    async def get(self, platform: PlatformName) -> AuthSession | None:
        async with self.database.connect() as connection:
            cursor = await connection.execute(
                "SELECT auth_session_json FROM platform_sessions WHERE platform=?",
                (platform,),
            )
            row = await cursor.fetchone()
        if row is None or not row["auth_session_json"]:
            return None
        payload = json.loads(row["auth_session_json"])
        return AuthSession(
            platform=payload["platform"],
            cookies=payload["cookies"],
            headers=payload["headers"],
            csrf_token=payload.get("csrf_token"),
            device_id=payload.get("device_id"),
            created_at=datetime.fromisoformat(payload["created_at"]),
        )

    async def status(self, platform: PlatformName) -> str:
        async with self.database.connect() as connection:
            cursor = await connection.execute(
                "SELECT status FROM platform_sessions WHERE platform=?", (platform,)
            )
            row = await cursor.fetchone()
        return row["status"] if row else "logged_out"

    async def mark_expired(self, platform: PlatformName) -> None:
        async with self.database.connect() as connection:
            await connection.execute(
                "UPDATE platform_sessions SET status='auth_expired', updated_at=? WHERE platform=?",
                (utc_now().isoformat(), platform),
            )
            await connection.commit()

    async def clear(self, platform: PlatformName) -> None:
        async with self.database.connect() as connection:
            await connection.execute(
                "DELETE FROM platform_sessions WHERE platform=?", (platform,)
            )
            await connection.commit()
