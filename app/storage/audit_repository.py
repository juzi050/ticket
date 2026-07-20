from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.domain import PlatformName, utc_now
from app.storage.database import MvpDatabase


REDACTED = "[REDACTED]"
SECRET_KEYS = {
    "authorization",
    "accesstoken",
    "cookie",
    "csrf",
    "csrftoken",
    "sendkey",
    "setcookie",
    "token",
    "refreshtoken",
    "sessiontoken",
    "xcsrftoken",
    "xxsrftoken",
}


def _normalized_key(value: str) -> str:
    return "".join(character for character in value.lower() if character.isalnum())


def _is_secret_key(value: str) -> bool:
    normalized = _normalized_key(value)
    return (
        normalized in SECRET_KEYS
        or normalized.endswith("token")
        or "password" in normalized
        or normalized in {"otp", "smscode", "verificationcode"}
    )


def scrub_url(url: str | None) -> str | None:
    if not url:
        return url
    parsed = urlsplit(url)
    query = urlencode(
        [
            (key, REDACTED if _is_secret_key(key) else value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        ]
    )
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment))


def scrub_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: REDACTED
            if _is_secret_key(str(key))
            else scrub_secrets(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [scrub_secrets(item) for item in value]
    return value


@dataclass(slots=True)
class AuditEntry:
    level: str
    category: str
    action: str
    message: str
    timestamp: datetime = field(default_factory=utc_now)
    platform: PlatformName | None = None
    task_id: str | None = None
    buyer_id: str | None = None
    order_id: str | None = None
    request_url: str | None = None
    request_method: str | None = None
    request_headers: dict[str, Any] | None = None
    request_body: Any = None
    response_status: int | None = None
    response_headers: dict[str, Any] | None = None
    response_body: Any = None
    context: dict[str, Any] | None = None
    exception_type: str | None = None
    exception_message: str | None = None
    exception_stack: str | None = None
    id: int | None = None


@dataclass(slots=True)
class AuditQuery:
    platform: PlatformName | None = None
    task_id: str | None = None
    order_id: str | None = None
    level: str | None = None
    category: str | None = None
    keyword: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    limit: int = 500


class AuditRepository:
    def __init__(self, database: MvpDatabase) -> None:
        self.database = database

    async def append(self, entry: AuditEntry) -> int:
        async with self.database.connect() as connection:
            cursor = await connection.execute(
                """
                INSERT INTO audit_logs (
                    timestamp, level, category, action, platform, task_id,
                    buyer_id, order_id, message, request_url, request_method,
                    request_headers_json, request_body_json, response_status,
                    response_headers_json, response_body_json, context_json,
                    exception_type, exception_message, exception_stack
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.timestamp.isoformat(),
                    entry.level,
                    entry.category,
                    entry.action,
                    entry.platform,
                    entry.task_id,
                    entry.buyer_id,
                    entry.order_id,
                    entry.message,
                    scrub_url(entry.request_url),
                    entry.request_method,
                    self._json(entry.request_headers),
                    self._json(entry.request_body),
                    entry.response_status,
                    self._json(entry.response_headers),
                    self._json(entry.response_body),
                    self._json(entry.context),
                    entry.exception_type,
                    entry.exception_message,
                    entry.exception_stack,
                ),
            )
            await connection.commit()
            return int(cursor.lastrowid)

    async def query(self, query: AuditQuery | None = None) -> list[AuditEntry]:
        current = query or AuditQuery()
        clauses: list[str] = []
        parameters: list[Any] = []
        for column, value in (
            ("platform", current.platform),
            ("task_id", current.task_id),
            ("order_id", current.order_id),
            ("level", current.level),
            ("category", current.category),
        ):
            if value is not None:
                clauses.append(f"{column}=?")
                parameters.append(value)
        if current.started_at:
            clauses.append("timestamp>=?")
            parameters.append(current.started_at.isoformat())
        if current.ended_at:
            clauses.append("timestamp<=?")
            parameters.append(current.ended_at.isoformat())
        if current.keyword:
            clauses.append("(message LIKE ? OR action LIKE ? OR request_url LIKE ?)")
            pattern = f"%{current.keyword}%"
            parameters.extend((pattern, pattern, pattern))

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        limit = max(1, min(current.limit, 5000))
        async with self.database.connect() as connection:
            cursor = await connection.execute(
                f"SELECT * FROM audit_logs {where} ORDER BY timestamp DESC LIMIT ?",
                (*parameters, limit),
            )
            rows = await cursor.fetchall()
        return [self._from_row(row) for row in rows]

    async def export_json(
        self, path: str | Path, query: AuditQuery | None = None
    ) -> Path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        rows = [self._serializable(row) for row in await self.query(query)]
        destination.write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return destination

    async def export_csv(
        self, path: str | Path, query: AuditQuery | None = None
    ) -> Path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        rows = [self._serializable(row) for row in await self.query(query)]
        fieldnames = list(self._serializable(AuditEntry("", "", "", "")).keys())
        with destination.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(
                    {
                        key: json.dumps(value, ensure_ascii=False)
                        if isinstance(value, (dict, list))
                        else value
                        for key, value in row.items()
                    }
                )
        return destination

    async def clear(self) -> int:
        async with self.database.connect() as connection:
            cursor = await connection.execute("DELETE FROM audit_logs")
            await connection.commit()
            return max(cursor.rowcount, 0)

    @staticmethod
    def _json(value: Any) -> str | None:
        if value is None:
            return None
        return json.dumps(scrub_secrets(value), ensure_ascii=False, default=str)

    @staticmethod
    def _load(value: str | None) -> Any:
        return json.loads(value) if value else None

    @classmethod
    def _from_row(cls, row: object) -> AuditEntry:
        values = dict(row)  # type: ignore[arg-type]
        return AuditEntry(
            id=values["id"],
            timestamp=datetime.fromisoformat(values["timestamp"]),
            level=values["level"],
            category=values["category"],
            action=values["action"],
            platform=values["platform"],
            task_id=values["task_id"],
            buyer_id=values["buyer_id"],
            order_id=values["order_id"],
            message=values["message"],
            request_url=values["request_url"],
            request_method=values["request_method"],
            request_headers=cls._load(values["request_headers_json"]),
            request_body=cls._load(values["request_body_json"]),
            response_status=values["response_status"],
            response_headers=cls._load(values["response_headers_json"]),
            response_body=cls._load(values["response_body_json"]),
            context=cls._load(values["context_json"]),
            exception_type=values["exception_type"],
            exception_message=values["exception_message"],
            exception_stack=values["exception_stack"],
        )

    @staticmethod
    def _serializable(entry: AuditEntry) -> dict[str, Any]:
        value = asdict(entry)
        value["timestamp"] = entry.timestamp.isoformat()
        return value
