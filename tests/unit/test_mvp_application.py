import tkinter as tk
from unittest.mock import Mock

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
        assert application._task_refresh_job is not None
        root.after_cancel(application._task_refresh_job)
        application._task_refresh_job = None
        application.task_panel.refresh = Mock()

        application._refresh_task_panel()

        application.task_panel.refresh.assert_called_once_with()
        assert application._task_refresh_job is not None
    finally:
        application.close()
