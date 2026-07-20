from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from app.gui.ui_events import display_status


class TaskListFrame(ttk.Frame):
    columns = (
        "enabled",
        "task_name",
        "platform",
        "event",
        "session",
        "ticket",
        "area",
        "quantity",
        "max_price",
        "status",
    )

    def __init__(self, parent: tk.Misc, callbacks: dict[str, object]) -> None:
        super().__init__(parent, padding=16)
        self.callbacks = callbacks
        self.rows_by_id: dict[str, dict[str, object]] = {}
        self._build()

    def _build(self) -> None:
        heading = ttk.Frame(self)
        heading.pack(fill="x", pady=(0, 12))
        ttk.Label(heading, text="任务管理", style="PageTitle.TLabel").pack(side="left")
        ttk.Label(
            heading, text="SQLite 中的任务是唯一配置来源", style="Muted.TLabel"
        ).pack(side="left", padx=14)

        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=(0, 10))
        for key, text, style in (
            ("new", "＋ 新建", "Accent.TButton"),
            ("edit", "编辑", "TButton"),
            ("copy", "复制", "TButton"),
            ("delete", "删除", "Danger.TButton"),
            ("start", "启用", "TButton"),
            ("pause", "暂停", "TButton"),
            ("stop", "停止", "TButton"),
            ("query", "立即查询", "TButton"),
            ("logs", "查看日志", "TButton"),
        ):
            ttk.Button(
                actions,
                text=text,
                style=style,
                command=lambda name=key: self._invoke(name),
            ).pack(side="left", padx=(0, 7))

        wrapper = ttk.Frame(self, style="Card.TFrame", padding=1)
        wrapper.pack(fill="both", expand=True)
        labels = {
            "enabled": "启用",
            "task_name": "任务名称",
            "platform": "平台",
            "event": "演出",
            "session": "场次",
            "ticket": "票档",
            "area": "区域",
            "quantity": "数量",
            "max_price": "最高单价",
            "status": "状态",
        }
        self.tree = ttk.Treeview(wrapper, columns=self.columns, show="headings", selectmode="browse")
        widths = (58, 150, 82, 210, 170, 100, 110, 55, 84, 112)
        for column, width in zip(self.columns, widths, strict=True):
            self.tree.heading(column, text=labels[column])
            self.tree.column(column, width=width, minwidth=45, anchor="center" if column in {"enabled", "platform", "quantity", "status"} else "w")
        ybar = ttk.Scrollbar(wrapper, orient="vertical", command=self.tree.yview)
        xbar = ttk.Scrollbar(wrapper, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        wrapper.rowconfigure(0, weight=1)
        wrapper.columnconfigure(0, weight=1)
        self.tree.bind("<Double-1>", lambda _event: self._invoke("edit"))
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self._show_detail())

        detail = ttk.Frame(self, style="DarkCard.TFrame", padding=(16, 12))
        detail.pack(fill="x", pady=(10, 0))
        self.detail_var = tk.StringVar(value="选择任务后查看实时查询指标")
        ttk.Label(detail, textvariable=self.detail_var, style="DarkMuted.TLabel").pack(anchor="w")

    def selected_task_id(self) -> str | None:
        selection = self.tree.selection()
        return selection[0] if selection else None

    def _invoke(self, name: str) -> None:
        callback = self.callbacks.get(name)
        if not callable(callback):
            return
        if name == "new":
            callback()
            return
        task_id = self.selected_task_id()
        if task_id:
            callback(task_id)

    def refresh(self, rows: list[dict[str, object]]) -> None:
        self.rows_by_id = {row["task"].task_id: row for row in rows}
        selected = self.selected_task_id()
        self.tree.delete(*self.tree.get_children())
        for row in rows:
            task = row["task"]
            values = (
                "是" if task.enabled else "否",
                task.task_name,
                "票牛" if task.platform == "piaoniu" else "摩天轮",
                task.event_name,
                task.target_sessions[0] if task.target_sessions else "不限",
                task.target_ticket_levels[0] if task.target_ticket_levels else "不限",
                task.target_areas[0] if task.target_areas else "不限",
                task.quantity,
                task.max_unit_price,
                display_status(row.get("status", "待启动")),
            )
            self.tree.insert("", "end", iid=task.task_id, values=values)
        if selected and self.tree.exists(selected):
            self.tree.selection_set(selected)
        self._show_detail()

    def _show_detail(self) -> None:
        task_id = self.selected_task_id()
        row = self.rows_by_id.get(task_id or "")
        if not row:
            self.detail_var.set("选择任务后查看实时查询指标")
            return
        task = row["task"]
        matched = row.get("is_matched")
        matched_text = "是" if matched == 1 else "否" if matched == 0 else "未知"
        audiences = "、".join(task.platform_audience_labels) or "未选择"
        self.detail_var.set(
            f"查询次数 {row.get('query_count', 0)}  ·  最近查询 {row.get('last_run_at') or '-'}  ·  "
            f"最近/最低价格 {row.get('last_price') or '-'} / {row.get('min_price') or '-'}  ·  "
            f"可购 {row.get('available_quantity') if row.get('available_quantity') is not None else '-'}  ·  "
            f"当前票档 {row.get('current_ticket_level') or '-'}  ·  当前区域 {row.get('current_area') or '-'}  ·  "
            f"匹配 {matched_text}\n指定购票人：{audiences}  ·  不匹配原因：{row.get('last_mismatch') or '-'}  ·  "
            f"最近错误：{row.get('last_error') or '-'}  ·  锁单：{row.get('last_lock_result') or '-'}"
        )
