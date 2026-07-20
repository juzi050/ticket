from app.settings import AppSettings


def test_settings_loads_minimal_environment(monkeypatch, tmp_path) -> None:
    database = tmp_path / "local.db"
    monkeypatch.setenv("DATABASE_PATH", str(database))
    monkeypatch.setenv("BROWSER_CHANNEL", "msedge")
    settings = AppSettings.load(tmp_path / "missing.env")
    assert settings.database_path == database
    assert settings.browser_channel == "msedge"
