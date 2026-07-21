from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class AppSettings:
    database_path: Path = Path("data/ticket.db")
    browser_channel: str = "msedge"
    serverchan_sendkey: str = ""

    @classmethod
    def load(cls, env_file: str | Path = ".env") -> "AppSettings":
        load_dotenv(env_file, override=False)
        return cls(
            serverchan_sendkey=os.getenv("SERVERCHAN_SENDKEY", ""),
        )
