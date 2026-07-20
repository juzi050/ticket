from pathlib import Path

import pytest

from app.config import ConfigurationError, load_settings


def test_load_valid_config(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
tasks:
  - task_id: one
    platform: mock
    event_name: 测试
    event_url: https://example.com
    quantity: 1
    max_unit_price: 100
    max_total_price: 100
""",
        encoding="utf-8",
    )
    settings = load_settings(path)
    assert settings.tasks[0].task_id == "one"
    assert str(settings.tasks[0].max_unit_price) == "100"


def test_duplicate_task_id_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(
        """
tasks:
  - &task
    task_id: duplicate
    platform: mock
    event_name: 测试
    event_url: https://example.com
    max_unit_price: 100
    max_total_price: 100
  - <<: *task
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="task_id 重复"):
        load_settings(path)


def test_invalid_range_has_clear_error(tmp_path: Path) -> None:
    path = tmp_path / "bad-range.yaml"
    path.write_text(
        """
tasks:
  - task_id: invalid
    platform: mock
    event_name: 测试
    event_url: https://example.com
    max_unit_price: 100
    max_total_price: 100
    row_min: 10
    row_max: 1
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="row_max"):
        load_settings(path)
