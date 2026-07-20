from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class AppSettings:
    database_path: Path = Path("data/ticket.db")
    browser_channel: str = "msedge"
    audit_log_level: str = "INFO"
    audit_log_retention_days: int = 0
    serverchan_sendkey: str = ""

    @classmethod
    def load(cls, env_file: str | Path = ".env") -> "AppSettings":
        load_dotenv(env_file, override=False)
        return cls(
            database_path=Path(os.getenv("DATABASE_PATH", "data/ticket.db")),
            browser_channel=os.getenv("BROWSER_CHANNEL", "msedge"),
            audit_log_level=os.getenv("AUDIT_LOG_LEVEL", "INFO"),
            audit_log_retention_days=int(os.getenv("AUDIT_LOG_RETENTION_DAYS", "0")),
            serverchan_sendkey=os.getenv("SERVERCHAN_SENDKEY", ""),
        )
