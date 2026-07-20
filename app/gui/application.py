from __future__ import annotations

import logging
import queue
import tkinter as tk
from concurrent.futures import Future
from pathlib import Path
from tkinter import messagebox, ttk

from app.config import MonitorTask
from app.gui.async_runner import AsyncRunner
from app.gui.controller import GuiController
from app.gui.log_panel import LogPanel, QueueLogHandler
from app.gui.platform_panel import PlatformPanel
from app.gui.task_editor import TaskEditor
from app.gui.task_list import TaskListFrame
from app.gui.ui_events import UiEvent


CLEAR_CONFIRMATION = """确定要清理全部缓存吗？

此操作将删除：
- 票牛和摩天轮的登录状态
- 所有平台的全部监控任务
- 所有演出、场次、票档和价格配置
- 所有票务缓存和运行记录

清理后需要重新登录并重新创建任务。

此操作不可恢复。"""


class TicketMonitorApplication:
    def __init__(
        self,
        root: tk.Tk | None,
        controller: GuiController | None,
        runner: AsyncRunner | None,
        *,
        build_ui: bool = True,
    ) -> None:
        self.root = root
        self.controller = controller
        self.runner = runner
        self.events = controller.events if controller else queue.Queue()
        self.log_messages: queue.Queue[str] = queue.Queue()
        self._refresh_pending = False
        self._closing = False
        self._log_handler: QueueLogHandler | None = None
        self._startup_future: Future[object] | None = None
        self.rows: list[dict[str, object]] = []
        if not build_ui:
            return
        if root is None or controller is None or runner is None:
            raise ValueError("构建 GUI 时必须提供 root、controller 和 runner")
        self._configure_window()
        self._configure_styles()
        self._build_ui()
        self._attach_logging()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(80, self._poll_queues)
        self.root.after(250, self.refresh)
        self._startup_future = self.runner.submit(self.controller.startup())

    def _configure_window(self) -> None:
        assert self.root is not None
        self.root.title("票务值守台 · Ticket Monitor")
        self.root.geometry("1280x790")
        self.root.minsize(1060, 680)
        self.root.configure(background="#17222b")

    def _configure_styles(self) -> None:
        assert self.root is not None
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        palette = {
            "ink": "#17222b",
            "paper": "#f4f1e8",
            "card": "#ffffff",
            "muted": "#67747c",
            "accent": "#d9812b",
            "cyan": "#69c2c8",
            "danger": "#b54d45",
        }
        style.configure("TFrame", background=palette["paper"])
        style.configure("Card.TFrame", background=palette["card"])
        style.configure("DarkCard.TFrame", background="#22313c")
        style.configure("Metric.TFrame", background="#2b3d49")
        style.configure("TLabel", background=palette["paper"], foreground=palette["ink"], font=("Microsoft YaHei UI", 9))
        style.configure("PageTitle.TLabel", font=("Microsoft YaHei UI", 18, "bold"), foreground=palette["ink"])
        style.configure("DialogTitle.TLabel", font=("Microsoft YaHei UI", 16, "bold"), foreground=palette["ink"])
        style.configure("Section.TLabel", font=("Microsoft YaHei UI", 10, "bold"), foreground="#a95e1f")
        style.configure("Muted.TLabel", foreground=palette["muted"])
        style.configure("DarkMuted.TLabel", background="#22313c", foreground="#9fb0ba")
        style.configure("Status.TLabel", background="#22313c", foreground=palette["cyan"], font=("Microsoft YaHei UI", 12, "bold"))
        style.configure("MetricValue.TLabel", background="#2b3d49", foreground="#ffffff", font=("Microsoft YaHei UI", 22, "bold"))
        style.configure("Header.TFrame", background=palette["ink"])
        style.configure("HeaderTitle.TLabel", background=palette["ink"], foreground="#f4f1e8", font=("Microsoft YaHei UI", 17, "bold"))
        style.configure("HeaderMuted.TLabel", background=palette["ink"], foreground="#9fb0ba")
        style.configure("Chip.TLabel", background="#263945", foreground=palette["cyan"], padding=(10, 6), font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("TButton", font=("Microsoft YaHei UI", 9), padding=(10, 6))
        style.configure("Accent.TButton", background=palette["accent"], foreground="#ffffff")
        style.map("Accent.TButton", background=[("active", "#bd6a20")])
        style.configure("Danger.TButton", foreground=palette["danger"])
        style.configure("Treeview", rowheight=29, background="#ffffff", fieldbackground="#ffffff", borderwidth=0)
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 9, "bold"), background="#e4e0d6", padding=(6, 8))
        style.configure("TNotebook", background=palette["paper"], borderwidth=0)
        style.configure("TNotebook.Tab", padding=(18, 9), font=("Microsoft YaHei UI", 9, "bold"))

    def _build_ui(self) -> None:
        assert self.root is not None
        header = ttk.Frame(self.root, style="Header.TFrame", padding=(20, 14))
        header.pack(fill="x")
        title_box = ttk.Frame(header, style="Header.TFrame")
        title_box.pack(side="left")
        ttk.Label(title_box, text="票务值守台", style="HeaderTitle.TLabel").pack(anchor="w")
        ttk.Label(title_box, text="本地 · 单用户 · 官方订单流程", style="HeaderMuted.TLabel").pack(anchor="w")

        self.running_var = tk.StringVar(value="运行任务 0")
        self.piaoniu_var = tk.StringVar(value="票牛 · 未检查")
        self.motianlun_var = tk.StringVar(value="摩天轮 · 未检查")
        ttk.Button(header, text="清理缓存", style="Danger.TButton", command=self.clear_cache).pack(side="right")
        for variable in (self.running_var, self.motianlun_var, self.piaoniu_var):
            ttk.Label(header, textvariable=variable, style="Chip.TLabel").pack(side="right", padx=(0, 8))

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True)
        callbacks = {
            "new": self.new_task,
            "edit": self.edit_task,
            "copy": self.copy_task,
            "delete": self.delete_task,
            "start": self.start_task,
            "pause": self.pause_task,
            "stop": self.stop_task,
            "query": self.query_task,
            "logs": self.show_logs,
        }
        self.task_list = TaskListFrame(self.notebook, callbacks)
        self.piaoniu_panel = PlatformPanel(
            self.notebook,
            "piaoniu",
            lambda: self._submit(self.controller.login_platform("piaoniu")),
            lambda: self._submit(self.controller.open_platform_home("piaoniu")),
        )
        self.motianlun_panel = PlatformPanel(
            self.notebook,
            "motianlun",
            lambda: self._submit(self.controller.login_platform("motianlun")),
            lambda: self._submit(self.controller.open_platform_home("motianlun")),
        )
        self.log_panel = LogPanel(self.notebook)
        self.notebook.add(self.task_list, text="任务管理")
        self.notebook.add(self.piaoniu_panel, text="票牛")
        self.notebook.add(self.motianlun_panel, text="摩天轮")
        self.notebook.add(self.log_panel, text="运行日志")

    def _attach_logging(self) -> None:
        self._log_handler = QueueLogHandler(self.log_messages)
        logging.getLogger().addHandler(self._log_handler)

    def _submit(self, coroutine) -> Future[object] | None:
        if self.runner is None:
            coroutine.close()
            return None
        future = self.runner.submit(coroutine)
        self._watch_future(future)
        return future

    def _watch_future(self, future: Future[object], success=None) -> None:
        if self.root is None:
            return
        if not future.done():
            self.root.after(100, lambda: self._watch_future(future, success))
            return
        try:
            result = future.result()
            if success:
                success(result)
        except Exception as exc:
            messagebox.showerror("操作失败", str(exc), parent=self.root)
        self.refresh()

    def _poll_queues(self) -> None:
        if self.root is None or self._closing:
            return
        while True:
            try:
                message = self.log_messages.get_nowait()
            except queue.Empty:
                break
            self.log_panel.append(message)
        while True:
            try:
                event: UiEvent = self.events.get_nowait()
            except queue.Empty:
                break
            self._handle_event(event)
        self.root.after(100, self._poll_queues)

    def _handle_event(self, event: UiEvent) -> None:
        if event.event_type == "platform_status":
            platform = str(event.payload.get("platform", ""))
            status = str(event.payload.get("status", "未知"))
            if platform == "piaoniu":
                self.piaoniu_var.set(f"票牛 · {status}")
                self.piaoniu_panel.update_status(status)
            elif platform == "motianlun":
                self.motianlun_var.set(f"摩天轮 · {status}")
                self.motianlun_panel.update_status(status)
        elif event.event_type == "manual":
            messagebox.showwarning("需要人工处理", event.message, parent=self.root)
        elif event.event_type == "cleared":
            self.log_panel.clear()
            self.piaoniu_var.set("票牛 · 未登录")
            self.motianlun_var.set("摩天轮 · 未登录")
            self.piaoniu_panel.update_status("未登录")
            self.motianlun_panel.update_status("未登录")
        self.refresh()

    def refresh(self) -> None:
        if self._refresh_pending or self.runner is None or self.controller is None:
            return
        self._refresh_pending = True
        future = self.runner.submit(self.controller.list_tasks())

        def apply() -> None:
            if self.root is None or self._closing:
                return
            if not future.done():
                self.root.after(120, apply)
                return
            self._refresh_pending = False
            try:
                self.rows = future.result()
            except Exception:
                logging.getLogger("app.gui").exception("刷新任务列表失败")
                return
            self.task_list.refresh(self.rows)
            self.piaoniu_panel.refresh(self.rows)
            self.motianlun_panel.refresh(self.rows)
            self.running_var.set(
                f"运行任务 {sum(bool(row.get('is_running')) for row in self.rows)}"
            )
            self.root.after(1000, self.refresh)

        apply()

    def _task_from_rows(self, task_id: str) -> MonitorTask | None:
        return next((row["task"] for row in self.rows if row["task"].task_id == task_id), None)

    def _open_editor(self, task: MonitorTask | None = None) -> None:
        assert self.root is not None and self.controller is not None and self.runner is not None
        TaskEditor(
            self.root,
            task=task,
            profile_ids=[profile.profile_id for profile in self.controller.settings.purchase_profiles],
            discover_callback=lambda platform, url, quantity: self.runner.submit(
                self.controller.discover(platform, url, quantity)
            ),
            save_callback=lambda value: self._submit(
                self.controller.save_task(
                    value, original_task_id=task.task_id if task else None
                )
            ),
        )

    def new_task(self) -> None:
        self._open_editor()

    def edit_task(self, task_id: str) -> None:
        task = self._task_from_rows(task_id)
        if task:
            self._open_editor(task)

    def copy_task(self, task_id: str) -> None:
        assert self.controller is not None
        self._submit(self.controller.duplicate_task(task_id))

    def delete_task(self, task_id: str) -> None:
        assert self.controller is not None and self.root is not None
        if messagebox.askyesno("删除任务", "确定删除所选任务及其历史记录吗？", parent=self.root):
            self._submit(self.controller.delete_task(task_id))

    def start_task(self, task_id: str) -> None:
        assert self.controller is not None
        self._submit(self.controller.start_task(task_id))

    def pause_task(self, task_id: str) -> None:
        assert self.controller is not None
        self._submit(self.controller.pause_task(task_id))

    def stop_task(self, task_id: str) -> None:
        assert self.controller is not None
        self._submit(self.controller.stop_task(task_id))

    def query_task(self, task_id: str) -> None:
        assert self.controller is not None
        self._submit(self.controller.query_now(task_id))

    def show_logs(self, _task_id: str) -> None:
        self.notebook.select(self.log_panel)

    @staticmethod
    def confirm_cache_clear(confirm) -> bool:
        return bool(confirm("确定要清理全部缓存吗？", CLEAR_CONFIRMATION))

    def clear_cache(self) -> None:
        assert self.root is not None and self.controller is not None
        confirmed = self.confirm_cache_clear(
            lambda title, message: messagebox.askyesno(title, message, parent=self.root)
        )
        if not confirmed:
            return
        self._submit(self.controller.clear_cache())

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        if self._log_handler:
            logging.getLogger().removeHandler(self._log_handler)
        if self.runner and self.controller:
            if self._startup_future and not self._startup_future.done():
                self._startup_future.cancel()
            future = self.runner.submit(self.controller.shutdown())
            try:
                future.result(timeout=15)
            except Exception:
                logging.getLogger("app.gui").exception("关闭后台服务失败")
            self.runner.stop()
        if self.root:
            # Tcl 解释器仍有效时释放 Python 变量，避免退出阶段再访问已销毁的 Tk。
            for owner, names in (
                (self, ("running_var", "piaoniu_var", "motianlun_var")),
                (self.task_list, ("detail_var",)),
                (
                    self.piaoniu_panel,
                    ("status_var", "running_var", "paused_var", "error_var"),
                ),
                (
                    self.motianlun_panel,
                    ("status_var", "running_var", "paused_var", "error_var"),
                ),
            ):
                for name in names:
                    if hasattr(owner, name):
                        setattr(owner, name, None)
            self.root.destroy()


def launch_gui(config: Path = Path("config.yaml"), *, mock_mode: bool = False) -> None:
    root = tk.Tk()
    runner = AsyncRunner()
    events: queue.Queue[UiEvent] = queue.Queue()
    try:
        controller = runner.submit(
            GuiController.create(config, events, mock_mode=mock_mode)
        ).result(timeout=20)
        TicketMonitorApplication(root, controller, runner)
        root.mainloop()
    except Exception:
        runner.stop()
        root.destroy()
        raise
