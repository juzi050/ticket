from __future__ import annotations

import json

from app.domain import BuyerPlatformBinding, BuyerProfile, PlatformName, utc_now
from app.storage.database import MvpDatabase


class BuyerRepository:
    def __init__(self, database: MvpDatabase) -> None:
        self.database = database

    async def save(self, buyer: BuyerProfile) -> BuyerProfile:
        saved = buyer.model_copy(update={"updated_at": utc_now()})
        async with self.database.connect() as connection:
            await connection.execute(
                """
                INSERT INTO buyers (
                    buyer_id, name, certificate_type, certificate_number,
                    phone, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(buyer_id) DO UPDATE SET
                    name=excluded.name,
                    certificate_type=excluded.certificate_type,
                    certificate_number=excluded.certificate_number,
                    phone=excluded.phone,
                    updated_at=excluded.updated_at
                """,
                (
                    saved.buyer_id,
                    saved.name,
                    saved.certificate_type,
                    saved.certificate_number,
                    saved.phone,
                    saved.created_at.isoformat(),
                    saved.updated_at.isoformat(),
                ),
            )
            await connection.commit()
        return saved

    async def get(self, buyer_id: str) -> BuyerProfile | None:
        async with self.database.connect() as connection:
            cursor = await connection.execute(
                "SELECT * FROM buyers WHERE buyer_id=?", (buyer_id,)
            )
            row = await cursor.fetchone()
        return BuyerProfile.model_validate(dict(row)) if row else None

    async def list(self) -> list[BuyerProfile]:
        async with self.database.connect() as connection:
            cursor = await connection.execute(
                "SELECT * FROM buyers ORDER BY created_at, buyer_id"
            )
            rows = await cursor.fetchall()
        return [BuyerProfile.model_validate(dict(row)) for row in rows]

    async def delete(self, buyer_id: str) -> None:
        async with self.database.connect() as connection:
            await connection.execute("DELETE FROM buyers WHERE buyer_id=?", (buyer_id,))
            await connection.commit()


class BuyerBindingRepository:
    def __init__(self, database: MvpDatabase) -> None:
        self.database = database

    async def save(self, binding: BuyerPlatformBinding) -> BuyerPlatformBinding:
        saved = binding.model_copy(update={"updated_at": utc_now()})
        async with self.database.connect() as connection:
            await connection.execute(
                """
                INSERT INTO buyer_platform_bindings (
                    buyer_id, platform, remote_buyer_id, remote_payload_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(buyer_id, platform) DO UPDATE SET
                    remote_buyer_id=excluded.remote_buyer_id,
                    remote_payload_json=excluded.remote_payload_json,
                    updated_at=excluded.updated_at
                """,
                (
                    saved.buyer_id,
                    saved.platform,
                    saved.remote_buyer_id,
                    json.dumps(saved.remote_payload, ensure_ascii=False),
                    saved.created_at.isoformat(),
                    saved.updated_at.isoformat(),
                ),
            )
            await connection.commit()
        return saved

    async def get(
        self, buyer_id: str, platform: PlatformName
    ) -> BuyerPlatformBinding | None:
        async with self.database.connect() as connection:
            cursor = await connection.execute(
                """
                SELECT * FROM buyer_platform_bindings
                WHERE buyer_id=? AND platform=?
                """,
                (buyer_id, platform),
            )
            row = await cursor.fetchone()
        return self._from_row(row) if row else None

    async def list_for_buyer(self, buyer_id: str) -> list[BuyerPlatformBinding]:
        async with self.database.connect() as connection:
            cursor = await connection.execute(
                """
                SELECT * FROM buyer_platform_bindings
                WHERE buyer_id=? ORDER BY platform
                """,
                (buyer_id,),
            )
            rows = await cursor.fetchall()
        return [self._from_row(row) for row in rows]

    @staticmethod
    def _from_row(row: object) -> BuyerPlatformBinding:
        values = dict(row)  # type: ignore[arg-type]
        values["remote_payload"] = json.loads(values.pop("remote_payload_json"))
        return BuyerPlatformBinding.model_validate(values)
