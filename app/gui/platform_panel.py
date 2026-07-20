from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from app.gui.ui_events import display_status


class PlatformPanel(ttk.Frame):
    def __init__(self, parent: tk.Misc, platform: str, login_callback, home_callback) -> None:
        super().__init__(parent, padding=18)
        self.platform = platform
        self.display_name = "票牛" if platform == "piaoniu" else "摩天轮票务"
        self.login_callback = login_callback
        self.home_callback = home_callback
        self.status_var = tk.StringVar(value="未检查")
        self.running_var = tk.StringVar(value="0")
        self.paused_var = tk.StringVar(value="0")
        self.error_var = tk.StringVar(value="0")
        self._build()

    def _build(self) -> None:
        ttk.Label(self, text=self.display_name, style="PageTitle.TLabel").pack(anchor="w")
        ttk.Label(
            self, text="本平台固定复用一个浏览器会话，所有任务共享登录状态。", style="Muted.TLabel"
        ).pack(anchor="w", pady=(2, 16))
        card = ttk.Frame(self, style="DarkCard.TFrame", padding=20)
        card.pack(fill="x")
        top = ttk.Frame(card, style="DarkCard.TFrame")
        top.pack(fill="x")
        ttk.Label(top, text="账号状态", style="DarkMuted.TLabel").pack(side="left")
        ttk.Label(top, textvariable=self.status_var, style="Status.TLabel").pack(side="left", padx=12)
        ttk.Button(top, text="登录 / 重新登录", command=self.login_callback).pack(side="right")
        ttk.Button(top, text="打开平台首页", command=self.home_callback).pack(side="right", padx=8)
        metrics = ttk.Frame(card, style="DarkCard.TFrame")
        metrics.pack(fill="x", pady=(24, 0))
        for label, variable in (
            ("运行任务", self.running_var),
            ("暂停任务", self.paused_var),
            ("异常任务", self.error_var),
        ):
            box = ttk.Frame(metrics, style="Metric.TFrame", padding=14)
            box.pack(side="left", fill="x", expand=True, padx=(0, 10))
            ttk.Label(box, textvariable=variable, style="MetricValue.TLabel").pack(anchor="w")
            ttk.Label(box, text=label, style="DarkMuted.TLabel").pack(anchor="w")

        ttk.Label(self, text="该平台任务", style="Section.TLabel").pack(anchor="w", pady=(24, 8))
        self.tree = ttk.Treeview(
            self, columns=("name", "event", "status", "last_price", "quantity"), show="headings"
        )
        for key, label, width in (
            ("name", "任务", 180),
            ("event", "演出", 260),
            ("status", "状态", 130),
            ("last_price", "最近价格", 100),
            ("quantity", "可购数量", 90),
        ):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width)
        self.tree.pack(fill="both", expand=True)

    def update_status(self, status: str) -> None:
        self.status_var.set(status)

    def refresh(self, rows: list[dict[str, object]]) -> None:
        filtered = [row for row in rows if row["task"].platform == self.platform]
        running = sum(bool(row.get("is_running")) for row in filtered)
        paused = sum(not row["task"].enabled for row in filtered)
        errors = sum("异常" in str(row.get("status", "")) or "error" in str(row.get("status", "")).lower() for row in filtered)
        self.running_var.set(str(running))
        self.paused_var.set(str(paused))
        self.error_var.set(str(errors))
        self.tree.delete(*self.tree.get_children())
        for row in filtered:
            task = row["task"]
            self.tree.insert(
                "",
                "end",
                iid=task.task_id,
                values=(
                    task.task_name,
                    task.event_name,
                    display_status(row.get("status", "待启动")),
                    row.get("last_price") or "-",
                    row.get("available_quantity") if row.get("available_quantity") is not None else "-",
                ),
            )
