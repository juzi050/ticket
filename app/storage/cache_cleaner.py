from __future__ import annotations

import shutil
from pathlib import Path

from app.database import Database


class CacheCleaner:
    def __init__(
        self,
        database: Database,
        data_dir: str | Path = "data",
        private_files: tuple[str | Path, ...] = (".env", "purchase_profiles.yaml"),
    ) -> None:
        self.database = database
        self.data_dir = Path(data_dir).resolve()
        self.private_files = tuple(Path(item).resolve() for item in private_files)

    def _safe_child(self, target: Path) -> Path:
        resolved = target.resolve()
        if self.data_dir not in resolved.parents:
            raise ValueError(f"拒绝清理 data 目录外路径：{resolved}")
        return resolved

    async def clear(self) -> None:
        await self.database.clear_all_data()
        for name in ("browser_states", "browser_profiles", "cache"):
            target = self._safe_child(self.data_dir / name)
            if target.exists():
                shutil.rmtree(target)
            target.mkdir(parents=True, exist_ok=True)
        for private_file in self.private_files:
            if private_file.exists() and private_file.is_file():
                private_file.unlink()
