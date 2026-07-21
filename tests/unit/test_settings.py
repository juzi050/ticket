from pathlib import Path

from app.settings import AppSettings


def test_settings_loads_only_serverchan_environment(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "ignored.db"))
    monkeypatch.setenv("BROWSER_CHANNEL", "ignored")
    monkeypatch.setenv("SERVERCHAN_SENDKEY", "test-sendkey")
    settings = AppSettings.load(tmp_path / "missing.env")
    assert settings.database_path == Path("data/ticket.db")
    assert settings.browser_channel == "msedge"
    assert settings.serverchan_sendkey == "test-sendkey"
