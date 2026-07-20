from __future__ import annotations

import tkinter as tk
from concurrent.futures import Future
from tkinter import messagebox, ttk
from typing import Any

from app.domain import BuyerProfile
from app.gui.async_runner import AsyncRunner
from app.storage.audit_repository import AuditEntry, AuditRepository
from app.storage.buyer_repository import BuyerRepository


def build_buyer_profile(
    values: dict[str, str], existing: BuyerProfile | None = None
) -> BuyerProfile:
    payload: dict[str, Any] = {
        "name": values["name"],
        "certificate_type": values["certificate_type"],
        "certificate_number": values["certificate_number"],
        "phone": values["phone"] or None,
    }
    if existing:
        payload.update(
            buyer_id=existing.buyer_id,
            created_at=existing.created_at,
            updated_at=existing.updated_at,
        )
    return BuyerProfile(**payload)


class BuyerDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        save_callback,
        existing: BuyerProfile | None = None,
    ) -> None:
        super().__init__(parent)
        self.existing = existing
        self.save_callback = save_callback
        self.title("编辑购票人" if existing else "新增购票人")
        self.geometry("520x340")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.values = {
            "name": tk.StringVar(value=existing.name if existing else ""),
            "certificate_type": tk.StringVar(
                value=existing.certificate_type if existing else "身份证"
            ),
            "certificate_number": tk.StringVar(
                value=existing.certificate_number if existing else ""
            ),
            "phone": tk.StringVar(value=existing.phone or "" if existing else ""),
        }
        self._build()

    def _build(self) -> None:
        body = ttk.Frame(self, padding=20)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=self.title(), style="DialogTitle.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 16)
        )
        fields = (
            ("name", "姓名"),
            ("certificate_type", "证件类型"),
            ("certificate_number", "证件号码"),
            ("phone", "手机号"),
        )
        for row, (key, label) in enumerate(fields, 1):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", pady=7)
            if key == "certificate_type":
                widget = ttk.Combobox(
                    body,
                    textvariable=self.values[key],
                    values=("身份证", "护照", "港澳居民来往内地通行证", "台湾居民来往大陆通行证"),
                    state="readonly",
                )
            else:
                widget = ttk.Entry(body, textvariable=self.values[key])
            widget.grid(row=row, column=1, sticky="ew", padx=(16, 0), pady=7)
        body.columnconfigure(1, weight=1)
        actions = ttk.Frame(body)
        actions.grid(row=5, column=0, columnspan=2, sticky="e", pady=(20, 0))
        ttk.Button(actions, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(actions, text="保存", command=self._save).pack(
            side="right", padx=(0, 8)
        )

    def _save(self) -> None:
        try:
            profile = build_buyer_profile(
                {key: value.get().strip() for key, value in self.values.items()},
                self.existing,
            )
        except Exception as exc:
            messagebox.showerror("资料无效", str(exc), parent=self)
            return
        self.save_callback(profile)
        self.destroy()


class BuyerManagerFrame(ttk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        runner: AsyncRunner,
        repository: BuyerRepository,
        audit_repository: AuditRepository,
    ) -> None:
        super().__init__(parent, padding=16)
        self.runner = runner
        self.repository = repository
        self.audit = audit_repository
        self.buyers: dict[str, BuyerProfile] = {}
        self._build()
        self.refresh()

    def _build(self) -> None:
        header = ttk.Frame(self)
        header.pack(fill="x", pady=(0, 12))
        ttk.Label(header, text="购票人管理", style="PageTitle.TLabel").pack(side="left")
        ttk.Label(
            header,
            text="完整实名资料仅保存在本机 SQLite",
            style="Muted.TLabel",
        ).pack(side="left", padx=14)
        ttk.Button(header, text="新增", command=self.add).pack(side="right")
        ttk.Button(header, text="编辑", command=self.edit).pack(side="right", padx=6)
        ttk.Button(header, text="删除", command=self.delete).pack(side="right")

        columns = ("name", "certificate_type", "certificate_number", "phone")
        self.tree = ttk.Treeview(self, columns=columns, show="headings", selectmode="browse")
        for column, label, width in (
            ("name", "姓名", 140),
            ("certificate_type", "证件类型", 180),
            ("certificate_number", "证件号码", 260),
            ("phone", "手机号", 160),
        ):
            self.tree.heading(column, text=label)
            self.tree.column(column, width=width, anchor="w")
        scrollbar = ttk.Scrollbar(self, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda _event: self.edit())

    def refresh(self) -> None:
        self._poll(self.runner.submit(self.repository.list()), self._render)

    def _render(self, buyers: list[BuyerProfile]) -> None:
        self.buyers = {buyer.buyer_id: buyer for buyer in buyers}
        self.tree.delete(*self.tree.get_children())
        for buyer in buyers:
            self.tree.insert(
                "",
                "end",
                iid=buyer.buyer_id,
                values=(
                    buyer.name,
                    buyer.certificate_type,
                    buyer.certificate_number,
                    buyer.phone or "",
                ),
            )

    def selected(self) -> BuyerProfile | None:
        selection = self.tree.selection()
        return self.buyers.get(selection[0]) if selection else None

    def add(self) -> None:
        BuyerDialog(self, self._save)

    def edit(self) -> None:
        buyer = self.selected()
        if buyer is None:
            messagebox.showinfo("请选择购票人", "请先选择要编辑的购票人。", parent=self)
            return
        BuyerDialog(self, self._save, buyer)

    def _save(self, buyer: BuyerProfile) -> None:
        async def save() -> BuyerProfile:
            saved = await self.repository.save(buyer)
            await self.audit.append(
                AuditEntry(
                    level="INFO",
                    category="buyer",
                    action="buyer_saved",
                    buyer_id=saved.buyer_id,
                    message="本地购票人资料已保存",
                    context={"buyer": saved.model_dump(mode="json")},
                )
            )
            return saved

        self._poll(self.runner.submit(save()), lambda _buyer: self.refresh())

    def delete(self) -> None:
        buyer = self.selected()
        if buyer is None:
            messagebox.showinfo("请选择购票人", "请先选择要删除的购票人。", parent=self)
            return
        if not messagebox.askyesno(
            "删除购票人", f"确定删除购票人“{buyer.name}”吗？", parent=self
        ):
            return

        async def remove() -> None:
            await self.repository.delete(buyer.buyer_id)
            await self.audit.append(
                AuditEntry(
                    level="INFO",
                    category="buyer",
                    action="buyer_deleted",
                    buyer_id=buyer.buyer_id,
                    message="本地购票人资料已删除",
                    context={"buyer": buyer.model_dump(mode="json")},
                )
            )

        self._poll(self.runner.submit(remove()), lambda _result: self.refresh())

    def _poll(self, future: Future[Any], callback) -> None:
        if not future.done():
            self.after(80, lambda: self._poll(future, callback))
            return
        try:
            callback(future.result())
        except Exception as exc:
            messagebox.showerror("操作失败", str(exc), parent=self)
