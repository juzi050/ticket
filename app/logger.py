from __future__ import annotations

import logging
import re
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


class ContextAndSensitiveFilter(logging.Filter):
    _patterns = (
        (
            re.compile(
                r"(?i)(token|sendkey|cookie|password|authorization|api[_-]?key|secret|"
                r"payment[_-]?(?:token|password)|card[_-]?(?:number|no)|验证码|银行卡号|支付密码)"
                r"(\s*[=:]\s*)[^\s,;]+"
            ),
            r"\1\2***",
        ),
        (re.compile(r"https://[^\s]+(?:webhook|send)[^\s]+", re.I), "https://***"),
        (re.compile(r"(?<!\d)(1\d{2})\d{4}(\d{4})(?!\d)"), r"\1****\2"),
        (re.compile(r"(?<!\d)(\d{6})\d{8}(\d{4}[0-9Xx])(?!\d)"), r"\1********\2"),
    )

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "task_id"):
            record.task_id = "-"
        if not hasattr(record, "platform"):
            record.platform = "-"
        message = record.getMessage()
        for pattern, replacement in self._patterns:
            message = pattern.sub(replacement, message)
        record.msg = message
        record.args = ()
        return True


def setup_logging(level: str = "INFO", log_dir: str | Path = "logs") -> None:
    directory = Path(log_dir)
    directory.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(platform)s | %(task_id)s | %(name)s | %(message)s"
    )
    sensitive_filter = ContextAndSensitiveFilter()

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.addFilter(sensitive_filter)
    root.addHandler(console)

    file_handler = TimedRotatingFileHandler(
        directory / "ticket_monitor.log", when="midnight", backupCount=14, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(sensitive_filter)
    root.addHandler(file_handler)


def task_logger(name: str, task_id: str = "-", platform: str = "-") -> logging.LoggerAdapter:
    return logging.LoggerAdapter(logging.getLogger(name), {"task_id": task_id, "platform": platform})
