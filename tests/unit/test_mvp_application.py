import tkinter as tk

from app.gui.mvp_application import MvpApplication
from app.settings import AppSettings


def test_mvp_application_has_only_required_pages(tmp_path) -> None:
    root = tk.Tk()
    root.withdraw()
    application = MvpApplication(
        root, AppSettings(database_path=tmp_path / "ticket.db")
    )
    try:
        labels = [application.notebook.tab(tab, "text") for tab in application.notebook.tabs()]
        assert labels == ["平台登录", "监控任务", "购票人", "审计日志"]
    finally:
        application.close()
