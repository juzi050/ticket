from __future__ import annotations

import tkinter as tk
from concurrent.futures import Future
from tkinter import messagebox, ttk
from typing import Any

from app.domain import PlatformName
from app.gui.async_runner import AsyncRunner
from app.storage.session_repository import PlatformSessionRepository


PLATFORM_LABELS = {"piaoniu": "票牛", "motianlun": "摩天轮"}
STATUS_LABELS = {
    "logged_in": "已登录",
    "logged_out": "未登录",
    "auth_expired": "登录失效",
}


def display_login_status(status: str) -> str:
    return STATUS_LABELS.get(status, status)


class LoginPanel(ttk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        runner: AsyncRunner,
        session_repository: PlatformSessionRepository,
        login_callback,
        clear_callback,
    ) -> None:
        super().__init__(parent, padding=16)
        self.runner = runner
        self.sessions = session_repository
        self.login_callback = login_callback
        self.clear_callback = clear_callback
        self.status_vars = {
            platform: tk.StringVar(value="检查中…")
            for platform in ("piaoniu", "motianlun")
        }
        self._build()
        self.refresh()

    def _build(self) -> None:
        ttk.Label(self, text="平台登录", style="PageTitle.TLabel").grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 12)
        )
        ttk.Label(
            self,
            text="浏览器只在你主动点击登录时打开；其余查询和下单均使用 HTTP API。",
            style="Muted.TLabel",
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(0, 14))
        for row, platform in enumerate(("piaoniu", "motianlun"), 2):
            ttk.Label(self, text=PLATFORM_LABELS[platform], width=12).grid(
                row=row, column=0, sticky="w", pady=7
            )
            ttk.Label(self, textvariable=self.status_vars[platform], width=14).grid(
                row=row, column=1, sticky="w", pady=7
            )
            ttk.Button(
                self,
                text="登录 / 重新登录",
                command=lambda current=platform: self.login(current),
            ).grid(row=row, column=2, padx=8, pady=7)
            ttk.Button(
                self,
                text="清除登录状态",
                command=lambda current=platform: self.clear(current),
            ).grid(row=row, column=3, pady=7)

    def refresh(self) -> None:
        async def load() -> dict[PlatformName, str]:
            return {
                platform: await self.sessions.status(platform)
                for platform in ("piaoniu", "motianlun")
            }

        def render(statuses: dict[PlatformName, str]) -> None:
            for platform, status in statuses.items():
                self.status_vars[platform].set(display_login_status(status))

        self._poll(self.runner.submit(load()), render)

    def login(self, platform: PlatformName) -> None:
        self.status_vars[platform].set("等待人工登录…")
        self._poll(self.login_callback(platform), lambda _session: self.refresh())

    def clear(self, platform: PlatformName) -> None:
        if not messagebox.askyesno(
            "清除登录状态",
            f"确定清除{PLATFORM_LABELS[platform]}的本地登录状态吗？",
            parent=self,
        ):
            return
        self._poll(self.clear_callback(platform), lambda _result: self.refresh())

    def _poll(self, future: Future[Any], callback) -> None:
        if not future.done():
            self.after(80, lambda: self._poll(future, callback))
            return
        try:
            callback(future.result())
        except Exception as exc:
            self.refresh()
            messagebox.showerror("登录操作失败", str(exc), parent=self)
