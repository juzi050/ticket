from __future__ import annotations

import logging
import queue
import tkinter as tk
from tkinter import ttk

from app.logger import ContextAndSensitiveFilter


class QueueLogHandler(logging.Handler):
    def __init__(self, messages: queue.Queue[str]) -> None:
        super().__init__()
        self.messages = messages
        self.addFilter(ContextAndSensitiveFilter())
        self.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)s | %(platform)s | %(task_id)s | %(message)s")
        )

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.messages.put(self.format(record))
        except Exception:
            self.handleError(record)


class LogPanel(ttk.Frame):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent, padding=16)
        header = ttk.Frame(self)
        header.pack(fill="x", pady=(0, 10))
        ttk.Label(header, text="运行日志", style="PageTitle.TLabel").pack(side="left")
        ttk.Label(
            header, text="业务条件完整显示；Cookie、密码与 Token 始终过滤", style="Muted.TLabel"
        ).pack(side="left", padx=14)
        ttk.Button(header, text="清空界面日志", command=self.clear).pack(side="right")
        self.text = tk.Text(
            self,
            wrap="word",
            background="#111820",
            foreground="#d9e2e8",
            insertbackground="#d9e2e8",
            selectbackground="#34576a",
            relief="flat",
            padx=14,
            pady=12,
            font=("Cascadia Mono", 9),
        )
        scrollbar = ttk.Scrollbar(self, command=self.text.yview)
        self.text.configure(yscrollcommand=scrollbar.set)
        self.text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def append(self, message: str) -> None:
        self.text.insert("end", message + "\n")
        self.text.see("end")

    def clear(self) -> None:
        self.text.delete("1.0", "end")
