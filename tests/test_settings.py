import pytest
from pydantic import ValidationError

from tr_banking.settings import PROJECT_ROOT, Settings

# _env_file=None everywhere: tests must never read the real .env file.


def test_api_key_read_from_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVDS_API_KEY", "fake-key-123")

    settings = Settings(_env_file=None)

    assert settings.evds_api_key.get_secret_value() == "fake-key-123"


def test_api_key_is_masked_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVDS_API_KEY", "fake-key-123")

    settings = Settings(_env_file=None)

    assert "fake-key-123" not in repr(settings)
    assert "fake-key-123" not in str(settings.evds_api_key)


def test_missing_api_key_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EVDS_API_KEY", raising=False)

    with pytest.raises(ValidationError, match="evds_api_key"):
        Settings(_env_file=None)


def test_default_paths_are_under_project_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVDS_API_KEY", "fake")

    settings = Settings(_env_file=None)

    assert settings.db_path == PROJECT_ROOT / "data" / "tr_banking.db"
    assert settings.raw_dir == PROJECT_ROOT / "data" / "raw"
    assert settings.series_config_path.is_file()
    assert settings.evds_base_url.startswith("https://evds3.tcmb.gov.tr/")
