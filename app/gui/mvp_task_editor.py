from __future__ import annotations

import tkinter as tk
from concurrent.futures import Future
from decimal import Decimal
from tkinter import messagebox, ttk
from typing import Any

from app.domain import (
    BuyerProfile,
    MonitorTask,
    PlatformName,
    SessionInfo,
    TicketOption,
)
from app.gui.async_runner import AsyncRunner
from app.platform_url import detect_platform
from app.platforms.http_api import TicketPlatformApi
from app.storage.audit_repository import AuditEntry, AuditRepository
from app.storage.buyer_repository import BuyerRepository
from app.storage.task_repository import TaskRepository


PLATFORM_LABELS = {"piaoniu": "票牛", "motianlun": "摩天轮"}


def ticket_choice_label(ticket: TicketOption) -> str:
    area = f" · {ticket.area}" if ticket.area else ""
    return (
        f"{ticket.ticket_name}{area} · ¥{ticket.unit_price} · "
        f"余量 {ticket.available_quantity} · {ticket.listing_id}"
    )


def preferred_ticket_label(
    tickets: dict[str, TicketOption], preferred_listing_id: str | None
) -> str:
    if preferred_listing_id:
        for label, ticket in tickets.items():
            if ticket.listing_id == preferred_listing_id:
                return label
    return next(iter(tickets), "")


def build_monitor_task(
    *,
    ticket: TicketOption,
    quantity: int,
    buyer_ids: list[str],
    ideal_price: str,
    query_interval_seconds: str,
    existing: MonitorTask | None = None,
) -> MonitorTask:
    values: dict[str, Any] = {
        "ticket": ticket,
        "quantity": quantity,
        "buyer_ids": buyer_ids,
        "ideal_price": Decimal(ideal_price),
        "query_interval_seconds": float(query_interval_seconds),
        "enabled": existing.enabled if existing else True,
        "status": existing.status if existing else "monitoring",
    }
    if existing:
        values.update(
            task_id=existing.task_id,
            created_at=existing.created_at,
            last_unit_price=existing.last_unit_price,
            last_estimated_total=existing.last_estimated_total,
            last_final_total=existing.last_final_total,
            last_checked_at=existing.last_checked_at,
            next_check_at=existing.next_check_at,
            last_error=existing.last_error,
        )
    return MonitorTask(**values)


class MvpTaskEditor(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        runner: AsyncRunner,
        platform_apis: dict[PlatformName, TicketPlatformApi],
        buyer_repository: BuyerRepository,
        task_repository: TaskRepository,
        audit_repository: AuditRepository,
        task: MonitorTask | None = None,
        saved_callback=None,
    ) -> None:
        super().__init__(parent)
        self.runner = runner
        self.apis = platform_apis
        self.buyers_repository = buyer_repository
        self.tasks = task_repository
        self.audit = audit_repository
        self.task = task
        self.saved_callback = saved_callback
        self.platform: PlatformName | None = task.ticket.platform if task else None
        self.sessions: dict[str, Any] = {}
        self.tickets: dict[str, TicketOption] = {}
        self.buyers: list[BuyerProfile] = []
        self.title("编辑监控任务" if task else "新建监控任务")
        self.geometry("820x680")
        self.minsize(760, 620)
        self.transient(parent)
        self.grab_set()
        self.url_var = tk.StringVar(value=task.ticket.event_url if task else "")
        self.platform_var = tk.StringVar(
            value=PLATFORM_LABELS[task.ticket.platform] if task else "等待识别"
        )
        self.event_var = tk.StringVar(value=task.ticket.event_name if task else "")
        self.session_var = tk.StringVar(value=task.ticket.session_name if task else "")
        self.ticket_var = tk.StringVar(
            value=ticket_choice_label(task.ticket) if task else ""
        )
        self.quantity_var = tk.IntVar(value=task.quantity if task else 1)
        self.ideal_var = tk.StringVar(value=str(task.ideal_price) if task else "280")
        self.interval_var = tk.StringVar(
            value=str(task.query_interval_seconds) if task else "10"
        )
        if task:
            self.sessions[self.session_var.get()] = SessionInfo(
                platform=task.ticket.platform,
                event_id=task.ticket.event_id,
                session_id=task.ticket.session_id,
                session_name=task.ticket.session_name,
            )
            self.tickets[self.ticket_var.get()] = task.ticket
        self._build()
        if task:
            self.session_combo.configure(values=list(self.sessions))
            self.ticket_combo.configure(values=list(self.tickets))
        self._load_buyers()
        if task:
            self.after(50, self._discover)

    def _build(self) -> None:
        shell = ttk.Frame(self, padding=20)
        shell.pack(fill="both", expand=True)
        ttk.Label(shell, text="监控任务", style="DialogTitle.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w"
        )
        ttk.Label(
            shell,
            text="平台由官方网址自动识别；保存后始终监控同一场次、同一 listing。",
            style="Muted.TLabel",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(3, 18))

        ttk.Label(shell, text="演出网址").grid(row=2, column=0, sticky="w", pady=7)
        ttk.Entry(shell, textvariable=self.url_var).grid(
            row=2, column=1, sticky="ew", padx=12, pady=7
        )
        ttk.Button(shell, text="解析演出", command=self._discover).grid(
            row=2, column=2, sticky="ew", pady=7
        )

        ttk.Label(shell, text="平台").grid(row=3, column=0, sticky="w", pady=7)
        ttk.Label(shell, textvariable=self.platform_var).grid(
            row=3, column=1, columnspan=2, sticky="w", padx=12, pady=7
        )
        ttk.Label(shell, text="演出").grid(row=4, column=0, sticky="w", pady=7)
        ttk.Label(shell, textvariable=self.event_var, wraplength=600).grid(
            row=4, column=1, columnspan=2, sticky="w", padx=12, pady=7
        )

        ttk.Label(shell, text="场次").grid(row=5, column=0, sticky="w", pady=7)
        self.session_combo = ttk.Combobox(
            shell, textvariable=self.session_var, state="readonly"
        )
        self.session_combo.grid(row=5, column=1, columnspan=2, sticky="ew", padx=12, pady=7)
        self.session_combo.bind("<<ComboboxSelected>>", lambda _event: self._load_tickets())

        ttk.Label(shell, text="购票数量").grid(row=6, column=0, sticky="w", pady=7)
        quantity = ttk.Spinbox(
            shell, from_=1, to=6, textvariable=self.quantity_var, width=10
        )
        quantity.grid(row=6, column=1, sticky="w", padx=12, pady=7)
        ttk.Button(shell, text="按数量刷新票品", command=self._load_tickets).grid(
            row=6, column=2, sticky="e", pady=7
        )

        ttk.Label(shell, text="精确票品").grid(row=7, column=0, sticky="w", pady=7)
        self.ticket_combo = ttk.Combobox(
            shell, textvariable=self.ticket_var, state="readonly"
        )
        self.ticket_combo.grid(row=7, column=1, columnspan=2, sticky="ew", padx=12, pady=7)

        ttk.Label(shell, text="购票人").grid(row=8, column=0, sticky="nw", pady=7)
        buyer_frame = ttk.Frame(shell)
        buyer_frame.grid(row=8, column=1, columnspan=2, sticky="nsew", padx=12, pady=7)
        self.buyer_list = tk.Listbox(
            buyer_frame,
            selectmode=tk.MULTIPLE,
            exportselection=False,
            height=7,
        )
        buyer_scroll = ttk.Scrollbar(buyer_frame, command=self.buyer_list.yview)
        self.buyer_list.configure(yscrollcommand=buyer_scroll.set)
        self.buyer_list.pack(side="left", fill="both", expand=True)
        buyer_scroll.pack(side="right", fill="y")

        ttk.Label(shell, text="理想订单总价").grid(row=9, column=0, sticky="w", pady=7)
        ttk.Entry(shell, textvariable=self.ideal_var, width=18).grid(
            row=9, column=1, sticky="w", padx=12, pady=7
        )
        ttk.Label(shell, text="只有预下单最终应付不超过此金额才创建订单").grid(
            row=9, column=2, sticky="w", pady=7
        )

        ttk.Label(shell, text="查询间隔（秒）").grid(row=10, column=0, sticky="w", pady=7)
        ttk.Entry(shell, textvariable=self.interval_var, width=18).grid(
            row=10, column=1, sticky="w", padx=12, pady=7
        )
        ttk.Label(shell, text="过短可能触发平台限流，请合理设置。").grid(
            row=10, column=2, sticky="w", pady=7
        )

        actions = ttk.Frame(shell)
        actions.grid(row=11, column=0, columnspan=3, sticky="e", pady=(24, 0))
        ttk.Button(actions, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(actions, text="保存任务", command=self._save).pack(
            side="right", padx=(0, 8)
        )
        shell.columnconfigure(1, weight=1)
        shell.rowconfigure(8, weight=1)

    def _discover(self) -> None:
        try:
            platform = detect_platform(self.url_var.get().strip())
        except Exception as exc:
            messagebox.showerror("网址无效", str(exc), parent=self)
            return
        self.platform = platform
        self.platform_var.set(PLATFORM_LABELS[platform])

        async def discover():
            api = self.apis[platform]
            event = await api.get_event(self.url_var.get().strip())
            sessions = await api.list_sessions(event.event_id)
            return event, sessions

        self._poll(self.runner.submit(discover()), self._render_event)

    def _render_event(self, result) -> None:
        event, sessions = result
        previous_session = self.session_var.get()
        self.event_var.set(event.event_name)
        self.sessions = {session.session_name: session for session in sessions}
        self.session_combo.configure(values=list(self.sessions))
        self.session_var.set(
            previous_session
            if previous_session in self.sessions
            else next(iter(self.sessions), "")
        )
        self._load_tickets()

    def _load_tickets(self) -> None:
        if self.platform is None or self.session_var.get() not in self.sessions:
            return
        try:
            quantity = int(self.quantity_var.get())
        except (TypeError, ValueError):
            messagebox.showerror("数量无效", "购票数量必须是正整数。", parent=self)
            return
        session = self.sessions[self.session_var.get()]

        async def load():
            return await self.apis[self.platform].list_tickets(
                session.event_id, session.session_id, quantity
            )

        self._poll(self.runner.submit(load()), self._render_tickets)

    def _render_tickets(self, tickets: list[TicketOption]) -> None:
        preferred_listing_id = self.task.ticket.listing_id if self.task else None
        self.tickets = {ticket_choice_label(ticket): ticket for ticket in tickets}
        values = list(self.tickets)
        self.ticket_combo.configure(values=values)
        self.ticket_var.set(
            preferred_ticket_label(self.tickets, preferred_listing_id)
        )
        if not values:
            messagebox.showinfo("暂无票品", "当前场次和数量没有可选票品。", parent=self)

    def _load_buyers(self) -> None:
        self._poll(self.runner.submit(self.buyers_repository.list()), self._render_buyers)

    def _render_buyers(self, buyers: list[BuyerProfile]) -> None:
        selected_ids = set(self.task.buyer_ids if self.task else [])
        self.buyers = buyers
        self.buyer_list.delete(0, "end")
        for index, buyer in enumerate(buyers):
            self.buyer_list.insert(
                "end", f"{buyer.name} · {buyer.certificate_number} · {buyer.phone or ''}"
            )
            if buyer.buyer_id in selected_ids:
                self.buyer_list.selection_set(index)

    def _save(self) -> None:
        try:
            ticket = self.tickets[self.ticket_var.get()]
            buyer_ids = [
                self.buyers[index].buyer_id for index in self.buyer_list.curselection()
            ]
            task = build_monitor_task(
                ticket=ticket,
                quantity=int(self.quantity_var.get()),
                buyer_ids=buyer_ids,
                ideal_price=self.ideal_var.get().strip(),
                query_interval_seconds=self.interval_var.get().strip(),
                existing=self.task,
            )
        except Exception as exc:
            messagebox.showerror("任务无效", str(exc), parent=self)
            return

        async def save() -> MonitorTask:
            saved = await self.tasks.save(task)
            await self.audit.append(
                AuditEntry(
                    level="INFO",
                    category="task",
                    action="task_saved",
                    platform=saved.ticket.platform,
                    task_id=saved.task_id,
                    message="监控任务已保存",
                    context={"task": saved.model_dump(mode="json")},
                )
            )
            return saved

        def completed(_saved: MonitorTask) -> None:
            if self.saved_callback:
                self.saved_callback()
            self.destroy()

        self._poll(self.runner.submit(save()), completed)

    def _poll(self, future: Future[Any], callback) -> None:
        if not future.done():
            self.after(80, lambda: self._poll(future, callback))
            return
        try:
            callback(future.result())
        except Exception as exc:
            messagebox.showerror("操作失败", str(exc), parent=self)
