from __future__ import annotations

import tkinter as tk
from concurrent.futures import Future
from tkinter import messagebox, ttk
from typing import Any

from app.domain import MonitorTask
from app.gui.async_runner import AsyncRunner
from app.storage.audit_repository import AuditEntry, AuditRepository
from app.storage.order_repository import OrderRepository
from app.storage.task_repository import TaskRepository


PLATFORM_LABELS = {"piaoniu": "票牛", "motianlun": "摩天轮"}


def task_row(task: MonitorTask) -> tuple[str, ...]:
    return (
        PLATFORM_LABELS[task.ticket.platform],
        task.ticket.event_name,
        task.ticket.session_name,
        task.ticket.ticket_name,
        str(task.quantity),
        f"¥{task.ideal_price}",
        f"{task.query_interval_seconds:g}",
        f"¥{task.last_unit_price}" if task.last_unit_price is not None else "-",
        f"¥{task.last_estimated_total}"
        if task.last_estimated_total is not None
        else "-",
        task.status,
        task.last_checked_at.astimezone().strftime("%m-%d %H:%M:%S")
        if task.last_checked_at
        else "-",
        task.next_check_at.astimezone().strftime("%m-%d %H:%M:%S")
        if task.next_check_at
        else "-",
    )


def sync_task_rows(tree: ttk.Treeview, tasks: list[MonitorTask]) -> None:
    existing_ids = set(tree.get_children())
    current_ids = {task.task_id for task in tasks}
    for task in tasks:
        values = task_row(task)
        if task.task_id in existing_ids:
            tree.item(task.task_id, values=values)
        else:
            tree.insert("", "end", iid=task.task_id, values=values)
    stale_ids = existing_ids - current_ids
    if stale_ids:
        tree.delete(*stale_ids)


class MvpTaskList(ttk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        runner: AsyncRunner,
        task_repository: TaskRepository,
        order_repository: OrderRepository,
        audit_repository: AuditRepository,
        create_callback,
        edit_callback,
        schedule_callback,
        check_callback,
        logs_callback,
    ) -> None:
        super().__init__(parent, padding=16)
        self.runner = runner
        self.tasks_repository = task_repository
        self.orders = order_repository
        self.audit = audit_repository
        self.create_callback = create_callback
        self.edit_callback = edit_callback
        self.schedule_callback = schedule_callback
        self.check_callback = check_callback
        self.logs_callback = logs_callback
        self.tasks: dict[str, MonitorTask] = {}
        self._build()
        self.refresh()

    def _build(self) -> None:
        header = ttk.Frame(self)
        header.pack(fill="x", pady=(0, 10))
        ttk.Label(header, text="监控任务", style="PageTitle.TLabel").pack(side="left")
        ttk.Button(header, text="新建任务", command=self.create_callback).pack(side="right")
        ttk.Button(header, text="刷新", command=self.refresh).pack(side="right", padx=6)

        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=(0, 10))
        for label, command in (
            ("暂停/继续", self.toggle),
            ("编辑", self.edit),
            ("删除", self.delete),
            ("立即检查", self.check_now),
            ("查看日志", self.view_logs),
            ("复制支付链接", self.copy_payment_url),
        ):
            ttk.Button(actions, text=label, command=command).pack(side="left", padx=(0, 6))

        columns = (
            "platform",
            "event",
            "session",
            "ticket",
            "quantity",
            "ideal",
            "interval",
            "unit",
            "estimated",
            "status",
            "last",
            "next",
        )
        self.tree = ttk.Treeview(self, columns=columns, show="headings", selectmode="browse")
        definitions = (
            ("platform", "平台", 70),
            ("event", "演出", 260),
            ("session", "场次", 180),
            ("ticket", "票品", 190),
            ("quantity", "数量", 55),
            ("ideal", "理想总价", 85),
            ("interval", "间隔(秒)", 75),
            ("unit", "当前单价", 85),
            ("estimated", "预估总价", 90),
            ("status", "状态", 130),
            ("last", "最后检查", 115),
            ("next", "下次检查", 115),
        )
        for column, label, width in definitions:
            self.tree.heading(column, text=label)
            self.tree.column(column, width=width, anchor="w")
        scrollbar_y = ttk.Scrollbar(self, command=self.tree.yview)
        scrollbar_x = ttk.Scrollbar(self, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=scrollbar_y.set, xscrollcommand=scrollbar_x.set)
        self.tree.pack(fill="both", expand=True)
        scrollbar_y.place(relx=1, rely=0.15, relheight=0.8, anchor="ne")
        scrollbar_x.pack(fill="x")
        self.tree.bind("<Double-1>", lambda _event: self.edit())

    def selected(self) -> MonitorTask | None:
        selection = self.tree.selection()
        return self.tasks.get(selection[0]) if selection else None

    def refresh(self) -> None:
        self._poll(self.runner.submit(self.tasks_repository.list()), self._render)

    def _render(self, tasks: list[MonitorTask]) -> None:
        self.tasks = {task.task_id: task for task in tasks}
        sync_task_rows(self.tree, tasks)

    def toggle(self) -> None:
        task = self.selected()
        if not task:
            self._choose_first()
            return
        enabled = not task.enabled
        status = "monitoring" if enabled else "paused"

        async def update() -> None:
            await self.tasks_repository.set_enabled(task.task_id, enabled, status)
            await self.audit.append(
                AuditEntry(
                    level="INFO",
                    category="task",
                    action="task_resumed" if enabled else "task_paused",
                    platform=task.ticket.platform,
                    task_id=task.task_id,
                    message="监控任务已继续" if enabled else "监控任务已暂停",
                )
            )

        def completed(_result) -> None:
            self.schedule_callback(task.task_id, enabled)
            self.refresh()

        self._poll(self.runner.submit(update()), completed)

    def edit(self) -> None:
        task = self.selected()
        if task:
            self.edit_callback(task)
        else:
            self._choose_first()

    def delete(self) -> None:
        task = self.selected()
        if not task:
            self._choose_first()
            return
        if not messagebox.askyesno(
            "删除任务",
            f"确定删除“{task.ticket.event_name} / {task.ticket.session_name} / {task.ticket.ticket_name}”吗？",
            parent=self,
        ):
            return

        async def remove() -> None:
            blocking = await self.orders.find_blocking(task)
            if blocking:
                raise RuntimeError("该任务存在创建中、待支付或状态未知订单，不能删除")
            await self.tasks_repository.delete(task.task_id)
            await self.audit.append(
                AuditEntry(
                    level="INFO",
                    category="task",
                    action="task_deleted",
                    platform=task.ticket.platform,
                    task_id=task.task_id,
                    message="监控任务已删除",
                    context={"task": task.model_dump(mode="json")},
                )
            )

        self._poll(self.runner.submit(remove()), lambda _result: self.refresh())

    def check_now(self) -> None:
        task = self.selected()
        if task:
            self.check_callback(task.task_id)
        else:
            self._choose_first()

    def view_logs(self) -> None:
        task = self.selected()
        if task:
            self.logs_callback(task.task_id)
        else:
            self._choose_first()

    def copy_payment_url(self) -> None:
        task = self.selected()
        if not task:
            self._choose_first()
            return

        async def load_url() -> str:
            order = await self.orders.find_blocking(task)
            if order is None or not order.payment_url:
                raise RuntimeError("该任务没有可复制的待支付链接")
            return order.payment_url

        def copy(url: str) -> None:
            self.clipboard_clear()
            self.clipboard_append(url)
            messagebox.showinfo("复制成功", "支付链接已复制。", parent=self)

        self._poll(self.runner.submit(load_url()), copy)

    def _choose_first(self) -> None:
        messagebox.showinfo("请选择任务", "请先选择一条监控任务。", parent=self)

    def _poll(self, future: Future[Any], callback) -> None:
        if not future.done():
            self.after(80, lambda: self._poll(future, callback))
            return
        try:
            callback(future.result())
        except Exception as exc:
            messagebox.showerror("操作失败", str(exc), parent=self)
