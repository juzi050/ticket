from __future__ import annotations

import tkinter as tk
from concurrent.futures import Future
from decimal import Decimal
from tkinter import messagebox, ttk
from uuid import uuid4

from app.config import MonitorTask
from app.models import PlatformAudienceOption, TicketInfo


class TaskEditor(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        task: MonitorTask | None,
        discover_callback,
        save_callback,
        audience_callback=None,
        profile_ids: list[str] | None = None,
    ) -> None:
        super().__init__(parent)
        self.task = task
        # profile_ids 仅保留构造兼容，不再展示或保存本地购票档案。
        _ = profile_ids
        self.audience_callback = audience_callback
        self.discover_callback = discover_callback
        self.save_callback = save_callback
        self.tickets: list[TicketInfo] = []
        self.audiences: list[PlatformAudienceOption] = []
        self._pending_audience_ids = list(task.platform_audience_ids) if task else []
        self.listing_choices: dict[str, TicketInfo] = {}
        self.discovery_source: tuple[str, str, int] | None = None
        self.title("编辑监控任务" if task else "新建监控任务")
        self.geometry("820x760")
        self.minsize(720, 620)
        self.transient(parent)
        self.grab_set()
        self.vars: dict[str, tk.Variable] = {}
        self._build()
        self._load(task)
        self.after(120, self._refresh_audiences)

    def _build(self) -> None:
        shell = ttk.Frame(self, padding=18)
        shell.pack(fill="both", expand=True)
        ttk.Label(shell, text="任务配置", style="DialogTitle.TLabel").pack(anchor="w")
        ttk.Label(
            shell,
            text="先识别官方页面，再从已解析的场次、票档和区域中选择。内部 ID 不需要手填。",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(2, 12))

        canvas = tk.Canvas(shell, highlightthickness=0, background="#f4f1e8")
        scrollbar = ttk.Scrollbar(shell, orient="vertical", command=canvas.yview)
        form = ttk.Frame(canvas, padding=(2, 2, 14, 12))
        form.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        window = canvas.create_window((0, 0), window=form, anchor="nw")
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        for column in (1, 3):
            form.columnconfigure(column, weight=1)

        row = 0
        row = self._section(form, row, "基本信息")
        row = self._field(form, row, "task_name", "任务名称", pair=True)
        row = self._field(form, row, "task_id", "任务 ID", pair=True)
        row = self._field(form, row, "platform", "平台", kind="combo", values=["piaoniu", "motianlun"])
        row = self._field(form, row, "event_url", "演出详情页链接", wide=True)
        row = self._field(form, row, "event_name", "演出名称", wide=True)

        identify_bar = ttk.Frame(form)
        identify_bar.grid(row=row, column=0, columnspan=4, sticky="ew", pady=(4, 12))
        ttk.Button(identify_bar, text="识别演出信息", style="Accent.TButton", command=self._discover).pack(side="left")
        self.discovery_status = ttk.Label(identify_bar, text="尚未识别", style="Muted.TLabel")
        self.discovery_status.pack(side="left", padx=10)
        row += 1

        row = self._section(form, row, "场次与票品")
        row = self._field(form, row, "session", "目标场次", kind="combo", wide=True)
        row = self._field(form, row, "event_date", "演出日期", pair=True)
        row = self._field(form, row, "event_time", "演出时间", pair=True)
        row = self._field(form, row, "ticket_level", "目标票档", kind="combo", pair=True)
        row = self._field(form, row, "area", "目标区域", kind="combo", pair=True)
        row = self._field(form, row, "listing", "当前稳定票品", kind="combo", wide=True)
        row = self._field(form, row, "stand", "目标看台", pair=True)
        row = self._field(form, row, "excluded", "排除关键词（逗号分隔）", wide=True)
        row = self._field(form, row, "row_min", "最小排数", pair=True)
        row = self._field(form, row, "row_max", "最大排数", pair=True)
        row = self._field(form, row, "seat_min", "最小座位号", pair=True)
        row = self._field(form, row, "seat_max", "最大座位号", pair=True)

        row = self._section(form, row, "购买与运行")
        row = self._field(form, row, "quantity", "购买数量", pair=True)
        row = self._field(form, row, "max_unit_price", "最高单价", pair=True)
        row = self._field(form, row, "max_total_price", "最高总价", pair=True)
        row = self._field(form, row, "interval_seconds", "查询间隔（秒）", pair=True)
        ttk.Label(form, text="指定购票人").grid(
            row=row, column=0, sticky="nw", padx=(0, 8), pady=4
        )
        audience_box = ttk.Frame(form)
        audience_box.grid(row=row, column=1, columnspan=3, sticky="ew", padx=(0, 14), pady=4)
        self.audience_list = tk.Listbox(
            audience_box,
            height=6,
            selectmode=tk.MULTIPLE,
            exportselection=False,
            activestyle="none",
        )
        audience_scroll = ttk.Scrollbar(
            audience_box, orient="vertical", command=self.audience_list.yview
        )
        self.audience_list.configure(yscrollcommand=audience_scroll.set)
        self.audience_list.grid(row=0, column=0, sticky="nsew")
        audience_scroll.grid(row=0, column=1, sticky="ns")
        audience_box.columnconfigure(0, weight=1)
        audience_box.rowconfigure(0, weight=1)
        audience_actions = ttk.Frame(audience_box)
        audience_actions.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(5, 0))
        ttk.Button(
            audience_actions, text="实时刷新购票人", command=self._refresh_audiences
        ).pack(side="left")
        self.audience_status = ttk.Label(
            audience_actions, text="尚未读取平台购票人", style="Muted.TLabel"
        )
        self.audience_status.pack(side="left", padx=10)
        self.audience_list.bind("<<ListboxSelect>>", lambda _event: self._update_audience_status())
        row += 1

        flags = ttk.Frame(form)
        flags.grid(row=row, column=0, columnspan=4, sticky="w", pady=(8, 12))
        for key, text in (
            ("adjacent", "要求连座"),
            ("auto_lock", "自动锁单"),
            ("notify", "启用微信通知"),
            ("stop_after_lock", "进入待支付后停止"),
            ("enabled", "保存后启用"),
        ):
            variable = tk.BooleanVar(value=False)
            self.vars[key] = variable
            ttk.Checkbutton(flags, text=text, variable=variable).pack(side="left", padx=(0, 18))

        actions = ttk.Frame(self, padding=(18, 10, 18, 16))
        actions.pack(fill="x")
        ttk.Button(actions, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(actions, text="保存任务", style="Accent.TButton", command=self._save).pack(
            side="right", padx=(0, 8)
        )
        self.vars["session_widget"].bind(  # type: ignore[attr-defined]
            "<<ComboboxSelected>>", lambda _event: self._update_ticket_levels()
        )
        self.vars["ticket_level_widget"].bind(  # type: ignore[attr-defined]
            "<<ComboboxSelected>>", lambda _event: self._update_areas()
        )
        self.vars["area_widget"].bind(  # type: ignore[attr-defined]
            "<<ComboboxSelected>>", lambda _event: self._update_listings()
        )
        self.vars["platform_widget"].bind(  # type: ignore[attr-defined]
            "<<ComboboxSelected>>", lambda _event: self._platform_changed()
        )
        self.vars["quantity"].trace_add("write", lambda *_args: self._update_audience_status())

    def _section(self, parent: ttk.Frame, row: int, text: str) -> int:
        ttk.Label(parent, text=text, style="Section.TLabel").grid(
            row=row, column=0, columnspan=4, sticky="w", pady=(12, 7)
        )
        return row + 1

    def _field(
        self,
        parent: ttk.Frame,
        row: int,
        key: str,
        label: str,
        *,
        kind: str = "entry",
        values: list[str] | None = None,
        pair: bool = False,
        wide: bool = False,
    ) -> int:
        variable = tk.StringVar()
        self.vars[key] = variable
        column = 0
        ttk.Label(parent, text=label).grid(row=row, column=column, sticky="w", padx=(0, 8), pady=4)
        if kind == "combo":
            widget = ttk.Combobox(parent, textvariable=variable, values=values or [], state="readonly")
            self.vars[f"{key}_widget"] = widget  # type: ignore[assignment]
        else:
            widget = ttk.Entry(parent, textvariable=variable)
            if key == "task_id" and self.task is not None:
                widget.configure(state="readonly")
        span = 3 if wide else 1
        widget.grid(row=row, column=1, columnspan=span, sticky="ew", padx=(0, 14), pady=4)
        return row + 1

    def _load(self, task: MonitorTask | None) -> None:
        values = {
            "task_id": task.task_id if task else f"task_{uuid4().hex[:8]}",
            "task_name": task.task_name if task else "",
            "platform": task.platform if task else "piaoniu",
            "event_url": task.event_url if task else "",
            "event_name": task.event_name if task else "",
            "session": task.target_sessions[0] if task and task.target_sessions else "",
            "event_date": task.event_date if task and task.event_date else "",
            "event_time": task.event_time if task and task.event_time else "",
            "ticket_level": task.target_ticket_levels[0] if task and task.target_ticket_levels else "",
            "area": task.target_areas[0] if task and task.target_areas else "",
            "listing": task.target_listing_id if task else "",
            "stand": task.target_stands[0] if task and task.target_stands else "",
            "excluded": ", ".join(task.excluded_keywords) if task else "",
            "row_min": task.row_min if task and task.row_min is not None else "",
            "row_max": task.row_max if task and task.row_max is not None else "",
            "seat_min": task.seat_min if task and task.seat_min is not None else "",
            "seat_max": task.seat_max if task and task.seat_max is not None else "",
            "quantity": task.quantity if task else 1,
            "max_unit_price": task.max_unit_price if task else "",
            "max_total_price": task.max_total_price if task else "",
            "interval_seconds": task.interval_seconds if task and task.interval_seconds else 10,
        }
        for key, value in values.items():
            self.vars[key].set(str(value))
        self.vars["adjacent"].set(task.adjacent_seats_required if task else False)
        self.vars["auto_lock"].set(task.auto_lock if task else False)
        self.vars["notify"].set(task.notify if task else True)
        self.vars["stop_after_lock"].set(task.stop_after_lock_success if task else True)
        self.vars["enabled"].set(task.enabled if task else False)
        if task:
            for option_id, label in zip(
                task.platform_audience_ids,
                task.platform_audience_labels,
                strict=False,
            ):
                self.audience_list.insert("end", label or option_id)
                self.audience_list.selection_set("end")
        self._update_audience_status()

    def _platform_changed(self) -> None:
        self._pending_audience_ids = []
        self.audiences = []
        self.audience_list.delete(0, "end")
        self._refresh_audiences()

    def _refresh_audiences(self) -> None:
        if self.audience_callback is None:
            self.audience_status.configure(text="购票人实时读取不可用")
            return
        platform = str(self.vars["platform"].get())
        preserved = {
            self.audiences[index].option_id
            for index in self.audience_list.curselection()
            if index < len(self.audiences)
        }
        if not preserved:
            preserved = set(self._pending_audience_ids)
        self._pending_audience_ids = list(preserved)
        self.audience_status.configure(text="正在实时读取平台账号…")
        future: Future[list[PlatformAudienceOption]] = self.audience_callback(platform)
        self._poll_audiences(future)

    def _poll_audiences(self, future: Future[list[PlatformAudienceOption]]) -> None:
        if not future.done():
            self.after(100, lambda: self._poll_audiences(future))
            return
        try:
            options = future.result()
        except Exception as exc:
            self.audience_status.configure(text="读取失败")
            messagebox.showerror("刷新购票人失败", str(exc), parent=self)
            return
        platform = str(self.vars["platform"].get())
        self.audiences = [
            option for option in options if option.platform == platform and option.enabled
        ]
        unavailable_count = sum(
            1 for option in options if option.platform == platform and not option.enabled
        )
        selected_ids = set(self._pending_audience_ids)
        self.audience_list.delete(0, "end")
        for index, option in enumerate(self.audiences):
            label = option.display_name
            if option.masked_identity:
                label += f" · {option.masked_identity}"
            self.audience_list.insert("end", label)
            if option.option_id in selected_ids:
                self.audience_list.selection_set(index)
        self._pending_audience_ids = []
        self._update_audience_status()
        if unavailable_count and not self.audiences:
            self.audience_status.configure(
                text="平台页面未提供稳定购票人 ID，自动锁单需人工接管"
            )

    def _update_audience_status(self) -> None:
        if not hasattr(self, "audience_status"):
            return
        selected = len(self.audience_list.curselection())
        try:
            quantity = int(str(self.vars["quantity"].get()))
        except (TypeError, ValueError):
            quantity = 0
        if selected == quantity and quantity > 0:
            text = f"已准确选择 {selected} 位购票人"
        else:
            text = f"购买数量为 {quantity or '-'}，当前选择 {selected} 位"
        self.audience_status.configure(text=text)

    def _discover(self) -> None:
        try:
            quantity = int(self.vars["quantity"].get())
            platform = str(self.vars["platform"].get())
            url = str(self.vars["event_url"].get()).strip()
            if not url:
                raise ValueError("请先填写演出链接")
            self.discovery_status.configure(text="正在识别…")
            self.discovery_source = (platform, url, quantity)
            future: Future[list[TicketInfo]] = self.discover_callback(platform, url, quantity)
            self._poll_discovery(future)
        except ValueError as exc:
            messagebox.showerror("无法识别", str(exc), parent=self)

    def _poll_discovery(self, future: Future[list[TicketInfo]]) -> None:
        if not future.done():
            self.after(100, lambda: self._poll_discovery(future))
            return
        try:
            self.tickets = future.result()
        except Exception as exc:
            self.discovery_status.configure(text="识别失败")
            messagebox.showerror("识别失败", str(exc), parent=self)
            return
        if not self.tickets:
            self.discovery_status.configure(text="没有符合精确数量的当前票品")
            return
        first = self.tickets[0]
        self.vars["event_name"].set(first.event_name)
        sessions = list(dict.fromkeys(ticket.session_name for ticket in self.tickets))
        self._set_combo_values("session", sessions)
        self._update_ticket_levels()
        self.discovery_status.configure(text=f"已识别 {len(self.tickets)} 个稳定票品")

    def _set_combo_values(self, key: str, values: list[str]) -> None:
        unique = list(dict.fromkeys(values))
        widget = self.vars[f"{key}_widget"]
        widget.configure(values=unique)  # type: ignore[attr-defined]
        current = str(self.vars[key].get())
        if current not in unique:
            self.vars[key].set(unique[0] if unique else "")

    def _update_ticket_levels(self) -> None:
        session = str(self.vars["session"].get())
        candidates = [ticket for ticket in self.tickets if ticket.session_name == session]
        self._set_combo_values(
            "ticket_level", [ticket.ticket_level for ticket in candidates]
        )
        self._update_areas()

    def _update_areas(self) -> None:
        session = str(self.vars["session"].get())
        level = str(self.vars["ticket_level"].get())
        candidates = [
            ticket
            for ticket in self.tickets
            if ticket.session_name == session and ticket.ticket_level == level
        ]
        self._set_combo_values("area", [ticket.area or "" for ticket in candidates])
        self._update_listings()

    def _update_listings(self) -> None:
        session = str(self.vars["session"].get())
        level = str(self.vars["ticket_level"].get())
        area = str(self.vars["area"].get())
        candidates = [
            ticket
            for ticket in self.tickets
            if ticket.session_name == session
            and ticket.ticket_level == level
            and (ticket.area or "") == area
        ]
        self.listing_choices = {}
        for index, ticket in enumerate(candidates, 1):
            identity = ticket.listing_id or ticket.ticket_group_id or f"票品{index}"
            label = (
                f"{ticket.unit_price} 元 · 可购 {ticket.available_quantity} · "
                f"{identity} · #{index}"
            )
            self.listing_choices[label] = ticket
        self._set_combo_values("listing", list(self.listing_choices))

    @staticmethod
    def _optional_int(value: object) -> int | None:
        text = str(value).strip()
        return int(text) if text else None

    def _save(self) -> None:
        try:
            session = str(self.vars["session"].get())
            level = str(self.vars["ticket_level"].get())
            area = str(self.vars["area"].get())
            selected = self.listing_choices.get(str(self.vars["listing"].get()))
            if self.tickets and selected is None:
                raise ValueError("所选场次、票档和区域不是同一票品，请重新识别后选择")
            if self.tickets and self.discovery_source != (
                str(self.vars["platform"].get()),
                str(self.vars["event_url"].get()).strip(),
                int(self.vars["quantity"].get()),
            ):
                raise ValueError("平台、演出链接或数量已改变，请重新识别票品")
            original = self.task
            selected_options = [
                self.audiences[index]
                for index in self.audience_list.curselection()
                if index < len(self.audiences)
            ]
            audience_ids = [option.option_id for option in selected_options]
            audience_labels = [option.display_name for option in selected_options]
            quantity = int(self.vars["quantity"].get())
            auto_lock = bool(self.vars["auto_lock"].get())
            if audience_ids and len(audience_ids) != quantity:
                raise ValueError(f"购买数量为 {quantity}，请准确选择 {quantity} 位购票人。")
            if auto_lock and not audience_ids:
                raise ValueError("自动锁单任务必须选择购票人")
            task = MonitorTask(
                task_id=str(self.vars["task_id"].get()).strip(),
                task_name=str(self.vars["task_name"].get()).strip(),
                enabled=bool(self.vars["enabled"].get()),
                platform=str(self.vars["platform"].get()),
                event_name=str(self.vars["event_name"].get()).strip(),
                event_url=str(self.vars["event_url"].get()).strip(),
                event_id=selected.event_id if selected else (original.event_id if original else ""),
                target_session_id=selected.session_id if selected else (original.target_session_id if original else ""),
                target_listing_id=selected.listing_id if selected else (original.target_listing_id if original else ""),
                target_ticket_group_id=selected.ticket_group_id if selected else (original.target_ticket_group_id if original else ""),
                target_ticket_level_id=(
                    str(selected.raw.get("category_id", ""))
                    if selected
                    else original.target_ticket_level_id if original else ""
                ),
                target_sessions=[session] if session else [],
                event_date=str(self.vars["event_date"].get()).strip() or None,
                event_time=str(self.vars["event_time"].get()).strip() or None,
                target_ticket_levels=[level] if level else [],
                target_areas=[area] if area else [],
                target_stands=[str(self.vars["stand"].get()).strip()] if str(self.vars["stand"].get()).strip() else [],
                excluded_keywords=[
                    item.strip()
                    for item in str(self.vars["excluded"].get()).replace("，", ",").split(",")
                    if item.strip()
                ],
                row_min=self._optional_int(self.vars["row_min"].get()),
                row_max=self._optional_int(self.vars["row_max"].get()),
                seat_min=self._optional_int(self.vars["seat_min"].get()),
                seat_max=self._optional_int(self.vars["seat_max"].get()),
                quantity=quantity,
                adjacent_seats_required=bool(self.vars["adjacent"].get()),
                max_unit_price=Decimal(str(self.vars["max_unit_price"].get())),
                max_total_price=Decimal(str(self.vars["max_total_price"].get())),
                interval_seconds=float(str(self.vars["interval_seconds"].get())),
                auto_lock=auto_lock,
                notify=bool(self.vars["notify"].get()),
                stop_after_lock_success=bool(self.vars["stop_after_lock"].get()),
                platform_audience_ids=audience_ids,
                platform_audience_labels=audience_labels,
                purchase_profile_id="",
            )
            if task.auto_lock and not selected and not task.target_listing_id:
                raise ValueError("自动锁单任务必须先识别并选择稳定票品")
            self.save_callback(task)
            self.destroy()
        except Exception as exc:
            messagebox.showerror("任务配置无效", str(exc), parent=self)
