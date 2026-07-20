from __future__ import annotations

from app.database import Database
from app.models import TicketInfo


class TicketCache:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def save(self, task_id: str, ticket: TicketInfo) -> None:
        await self.database.save_ticket_cache(task_id, ticket)

    async def list(self, task_id: str | None = None) -> list[dict[str, object]]:
        return await self.database.list_ticket_cache(task_id)
