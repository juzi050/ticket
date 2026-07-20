from __future__ import annotations

import tkinter as tk
from concurrent.futures import Future
from tkinter import messagebox, ttk

from app.models import AudienceCreateRequest, PlatformAudienceOption


PLATFORM_LABELS = {"piaoniu": "票牛", "motianlun": "摩天轮"}


class AudienceCreateDialog(tk.Toplevel):
    """敏感字段只存在于本窗口与一次性请求对象中。"""

    def __init__(self, parent: tk.Misc, platform: str, submit_callback, success_callback) -> None:
        super().__init__(parent)
        self.platform = platform
        self.submit_callback = submit_callback
        self.success_callback = success_callback
        self.title(f"新增{PLATFORM_LABELS[platform]}购票人")
        self.geometry("500x390")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.vars = {
            "name": tk.StringVar(),
            "certificate_type": tk.StringVar(value="身份证"),
            "certificate_number": tk.StringVar(),
            "phone": tk.StringVar(),
        }
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _build(self) -> None:
        body = ttk.Frame(self, padding=22)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="新增平台购票人", style="DialogTitle.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 6)
        )
        ttk.Label(
            body,
            text="资料将直接填写到平台官方页面，本软件不保存表单草稿。",
            style="Muted.TLabel",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 16))
        labels = (
            ("name", "姓名"),
            ("certificate_type", "证件类型"),
            ("certificate_number", "证件号码"),
            ("phone", "手机号（平台要求时填写）"),
        )
        for row, (key, text) in enumerate(labels, 2):
            ttk.Label(body, text=text).grid(row=row, column=0, sticky="w", pady=7, padx=(0, 12))
            if key == "certificate_type":
                widget = ttk.Combobox(
                    body,
                    textvariable=self.vars[key],
                    values=["身份证", "护照", "港澳居民来往内地通行证", "台湾居民来往大陆通行证"],
                    state="readonly",
                )
            else:
                widget = ttk.Entry(body, textvariable=self.vars[key], show="•" if key == "certificate_number" else "")
            widget.grid(row=row, column=1, sticky="ew", pady=7)
        body.columnconfigure(1, weight=1)

        actions = ttk.Frame(body)
        actions.grid(row=6, column=0, columnspan=2, sticky="e", pady=(22, 0))
        ttk.Button(actions, text="取消", command=self._close).pack(side="right")
        self.submit_button = ttk.Button(
            actions,
            text=f"保存到{PLATFORM_LABELS[self.platform]}账号",
            style="Accent.TButton",
            command=self._submit,
        )
        self.submit_button.pack(side="right", padx=(0, 8))

    def _clear_form(self) -> None:
        for variable in self.vars.values():
            variable.set("")

    def _close(self) -> None:
        self._clear_form()
        self.destroy()

    def _submit(self) -> None:
        request: AudienceCreateRequest | None = None
        try:
            name = self.vars["name"].get().strip()
            certificate_type = self.vars["certificate_type"].get().strip()
            certificate_number = self.vars["certificate_number"].get().strip()
            phone = self.vars["phone"].get().strip()
            request = AudienceCreateRequest(
                name=name,
                certificate_type=certificate_type,
                certificate_number=certificate_number,
                phone=phone or None,
            )
            confirmation = (
                f"请核对将提交到{PLATFORM_LABELS[self.platform]}账号的完整资料：\n\n"
                f"姓名：{name}\n"
                f"证件类型：{certificate_type}\n"
                f"证件号码：{certificate_number}\n"
                f"手机号：{phone or '未填写'}\n\n"
                "确认后将打开并操作平台官方页面。"
            )
            if not messagebox.askyesno("确认购票人资料", confirmation, parent=self):
                request.clear_sensitive()
                return
            self.submit_button.configure(state="disabled")
            future: Future[PlatformAudienceOption] = self.submit_callback(
                self.platform, request
            )
            self._poll_submit(future, request)
        except Exception as exc:
            if request is not None:
                request.clear_sensitive()
            messagebox.showerror("无法提交", str(exc), parent=self)

    def _poll_submit(
        self, future: Future[PlatformAudienceOption], request: AudienceCreateRequest
    ) -> None:
        if not future.done():
            self.after(100, lambda: self._poll_submit(future, request))
            return
        try:
            option = future.result()
        except Exception as exc:
            request.clear_sensitive()
            self._clear_form()
            self.submit_button.configure(state="normal")
            messagebox.showerror("保存到平台失败", str(exc), parent=self)
            return
        request.clear_sensitive()
        self._clear_form()
        messagebox.showinfo(
            "保存成功",
            f"{option.display_name} 已保存到{PLATFORM_LABELS[self.platform]}账号。",
            parent=self,
        )
        self.success_callback()
        self.destroy()


class AudienceManagerFrame(ttk.Frame):
    def __init__(self, parent: tk.Misc, refresh_callback, create_callback, open_callback) -> None:
        super().__init__(parent, padding=16)
        self.refresh_callback = refresh_callback
        self.create_callback = create_callback
        self.open_callback = open_callback
        self.platform_var = tk.StringVar(value="piaoniu")
        self.status_var = tk.StringVar(value="刷新结果仅保存在当前运行内存中")
        self.options: list[PlatformAudienceOption] = []
        self._build()

    def _build(self) -> None:
        heading = ttk.Frame(self)
        heading.pack(fill="x", pady=(0, 12))
        ttk.Label(heading, text="购票人管理", style="PageTitle.TLabel").pack(side="left")
        ttk.Label(
            heading,
            text="真实资料保存在平台账号，本地只在任务中保存平台选项 ID",
            style="Muted.TLabel",
        ).pack(side="left", padx=14)

        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=(0, 10))
        platform = ttk.Combobox(
            actions,
            textvariable=self.platform_var,
            values=["piaoniu", "motianlun"],
            state="readonly",
            width=14,
        )
        platform.pack(side="left", padx=(0, 8))
        platform.bind("<<ComboboxSelected>>", lambda _event: self.refresh())
        ttk.Button(actions, text="刷新购票人", style="Accent.TButton", command=self.refresh).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(actions, text="新增购票人", command=self.add_audience).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(
            actions,
            text="打开官方购票人管理页面",
            command=lambda: self.open_callback(self.platform_var.get()),
        ).pack(side="left")
        ttk.Label(actions, textvariable=self.status_var, style="Muted.TLabel").pack(
            side="right"
        )

        wrapper = ttk.Frame(self, style="Card.TFrame", padding=1)
        wrapper.pack(fill="both", expand=True)
        columns = ("name", "identity", "status")
        self.tree = ttk.Treeview(wrapper, columns=columns, show="headings")
        for column, label, width in (
            ("name", "姓名", 180),
            ("identity", "平台脱敏证件", 430),
            ("status", "状态", 100),
        ):
            self.tree.heading(column, text=label)
            self.tree.column(column, width=width, anchor="w" if column != "status" else "center")
        scrollbar = ttk.Scrollbar(wrapper, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        wrapper.rowconfigure(0, weight=1)
        wrapper.columnconfigure(0, weight=1)

    def refresh(self) -> None:
        platform = self.platform_var.get()
        self.status_var.set("正在实时读取平台账号…")
        future: Future[list[PlatformAudienceOption]] = self.refresh_callback(platform)
        self._poll_refresh(future)

    def _poll_refresh(self, future: Future[list[PlatformAudienceOption]]) -> None:
        if not future.done():
            self.after(100, lambda: self._poll_refresh(future))
            return
        try:
            self.options = future.result()
        except Exception as exc:
            self.status_var.set("刷新失败")
            messagebox.showerror("刷新购票人失败", str(exc), parent=self)
            return
        self.tree.delete(*self.tree.get_children())
        for index, option in enumerate(self.options):
            self.tree.insert(
                "",
                "end",
                iid=option.option_id or f"{option.platform}:unavailable:{index}",
                values=(
                    option.display_name,
                    option.masked_identity or "平台未展示脱敏证件",
                    "可用" if option.enabled else "不可用",
                ),
            )
        self.status_var.set(f"已实时读取 {len(self.options)} 位购票人")

    def add_audience(self) -> None:
        AudienceCreateDialog(
            self,
            self.platform_var.get(),
            self.create_callback,
            self.refresh,
        )
