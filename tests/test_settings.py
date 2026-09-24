from pathlib import Path

import pytest

from tr_banking.settings import PROJECT_ROOT, Settings

# _env_file=None everywhere: tests must never read the real .env file.


def test_api_key_read_from_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVDS_API_KEY", "fake-key-123")

    settings = Settings(_env_file=None)

    assert settings.evds_api_key is not None
    assert settings.evds_api_key.get_secret_value() == "fake-key-123"


def test_api_key_is_masked_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVDS_API_KEY", "fake-key-123")

    settings = Settings(_env_file=None)

    assert "fake-key-123" not in repr(settings)
    assert "fake-key-123" not in str(settings.evds_api_key)


def test_api_key_is_optional_for_read_only_use(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EVDS_API_KEY", raising=False)

    assert Settings(_env_file=None).evds_api_key is None


def test_db_path_can_be_overridden(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DB_PATH", str(tmp_path / "other.db"))

    assert Settings(_env_file=None).db_path == tmp_path / "other.db"


def test_default_paths_are_under_project_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVDS_API_KEY", "fake")

    settings = Settings(_env_file=None)

    assert settings.db_path == PROJECT_ROOT / "data" / "tr_banking.db"
    assert settings.raw_dir == PROJECT_ROOT / "data" / "raw"
    assert settings.series_config_path.is_file()
    assert settings.evds_base_url.startswith("https://evds3.tcmb.gov.tr/")
