from __future__ import annotations

import json
import tkinter as tk
from concurrent.futures import Future
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from app.gui.async_runner import AsyncRunner
from app.storage.audit_repository import AuditEntry, AuditQuery, AuditRepository


def audit_query_from_values(values: dict[str, str]) -> AuditQuery:
    platform = values.get("platform") or None
    return AuditQuery(
        platform=platform if platform in {"piaoniu", "motianlun"} else None,
        task_id=values.get("task_id") or None,
        order_id=values.get("order_id") or None,
        level=values.get("level") or None,
        category=values.get("category") or None,
        keyword=values.get("keyword") or None,
        started_at=datetime.fromisoformat(values["started_at"])
        if values.get("started_at")
        else None,
        ended_at=datetime.fromisoformat(values["ended_at"])
        if values.get("ended_at")
        else None,
        limit=1000,
    )


class AuditPanel(ttk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        runner: AsyncRunner,
        repository: AuditRepository,
    ) -> None:
        super().__init__(parent, padding=16)
        self.runner = runner
        self.repository = repository
        self.rows: dict[str, AuditEntry] = {}
        self.vars = {
            key: tk.StringVar()
            for key in (
                "started_at",
                "ended_at",
                "platform",
                "task_id",
                "order_id",
                "level",
                "category",
                "keyword",
            )
        }
        self._build()
        self.refresh()

    def _build(self) -> None:
        header = ttk.Frame(self)
        header.pack(fill="x", pady=(0, 10))
        ttk.Label(header, text="审计日志", style="PageTitle.TLabel").pack(side="left")
        ttk.Label(
            header,
            text="完整业务数据保存在本机；鉴权秘密自动抹除",
            style="Muted.TLabel",
        ).pack(side="left", padx=14)

        filters = ttk.Frame(self)
        filters.pack(fill="x", pady=(0, 10))
        definitions = (
            ("started_at", "开始时间", None, 19),
            ("ended_at", "结束时间", None, 19),
            ("platform", "平台", ("", "piaoniu", "motianlun"), 11),
            ("level", "等级", ("", "INFO", "WARNING", "ERROR"), 10),
            ("category", "分类", None, 12),
        )
        for column, (key, label, choices, width) in enumerate(definitions):
            group = ttk.Frame(filters)
            group.grid(row=0, column=column, sticky="w", padx=(0, 8))
            ttk.Label(group, text=label).pack(anchor="w")
            if choices:
                widget = ttk.Combobox(
                    group,
                    textvariable=self.vars[key],
                    values=choices,
                    state="readonly",
                    width=width,
                )
            else:
                widget = ttk.Entry(group, textvariable=self.vars[key], width=width)
            widget.pack()

        secondary = ttk.Frame(self)
        secondary.pack(fill="x", pady=(0, 10))
        for key, label, width in (
            ("task_id", "任务 ID", 22),
            ("order_id", "订单 ID", 22),
            ("keyword", "关键字", 28),
        ):
            ttk.Label(secondary, text=label).pack(side="left", padx=(0, 4))
            ttk.Entry(secondary, textvariable=self.vars[key], width=width).pack(
                side="left", padx=(0, 10)
            )
        ttk.Button(secondary, text="查询", command=self.refresh).pack(side="left")
        ttk.Button(secondary, text="JSON 导出", command=lambda: self.export("json")).pack(
            side="right"
        )
        ttk.Button(secondary, text="CSV 导出", command=lambda: self.export("csv")).pack(
            side="right", padx=6
        )
        ttk.Button(secondary, text="清空", command=self.clear).pack(side="right")

        columns = ("timestamp", "level", "platform", "category", "action", "message")
        self.tree = ttk.Treeview(self, columns=columns, show="headings", selectmode="browse")
        for column, label, width in (
            ("timestamp", "时间", 180),
            ("level", "等级", 80),
            ("platform", "平台", 90),
            ("category", "分类", 100),
            ("action", "动作", 170),
            ("message", "消息", 420),
        ):
            self.tree.heading(column, text=label)
            self.tree.column(column, width=width, anchor="w")
        scrollbar = ttk.Scrollbar(self, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda _event: self.show_detail())

    def current_query(self) -> AuditQuery:
        return audit_query_from_values(
            {key: value.get().strip() for key, value in self.vars.items()}
        )

    def refresh(self) -> None:
        try:
            query = self.current_query()
        except ValueError as exc:
            messagebox.showerror("时间格式无效", str(exc), parent=self)
            return
        self._poll(self.runner.submit(self.repository.query(query)), self._render)

    def _render(self, rows: list[AuditEntry]) -> None:
        self.tree.delete(*self.tree.get_children())
        self.rows = {}
        for index, row in enumerate(rows):
            key = str(row.id if row.id is not None else index)
            self.rows[key] = row
            self.tree.insert(
                "",
                "end",
                iid=key,
                values=(
                    row.timestamp.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
                    row.level,
                    row.platform or "",
                    row.category,
                    row.action,
                    row.message,
                ),
            )

    def show_detail(self) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        row = self.rows[selected[0]]
        detail = tk.Toplevel(self)
        detail.title("审计详情")
        detail.geometry("900x650")
        text = tk.Text(detail, wrap="word", font=("Cascadia Mono", 9))
        scrollbar = ttk.Scrollbar(detail, command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        payload = asdict(row)
        payload["timestamp"] = row.timestamp.isoformat()
        text.insert("1.0", json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        text.configure(state="disabled")

    def export(self, kind: str) -> None:
        extension = f".{kind}"
        path = filedialog.asksaveasfilename(
            parent=self,
            defaultextension=extension,
            filetypes=[(kind.upper(), f"*{extension}")],
            initialfile=f"audit-{datetime.now():%Y%m%d-%H%M%S}{extension}",
        )
        if not path:
            return
        query = self.current_query()
        operation = (
            self.repository.export_json(Path(path), query)
            if kind == "json"
            else self.repository.export_csv(Path(path), query)
        )
        self._poll(
            self.runner.submit(operation),
            lambda destination: messagebox.showinfo(
                "导出完成", f"已导出到：\n{destination}", parent=self
            ),
        )

    def clear(self) -> None:
        if not messagebox.askyesno(
            "清空审计日志", "确定清空全部本地审计日志吗？", parent=self
        ):
            return
        self._poll(self.runner.submit(self.repository.clear()), lambda _count: self.refresh())

    def _poll(self, future: Future[Any], callback) -> None:
        if not future.done():
            self.after(80, lambda: self._poll(future, callback))
            return
        try:
            callback(future.result())
        except Exception as exc:
            messagebox.showerror("操作失败", str(exc), parent=self)
