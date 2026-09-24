"""Runtime settings loaded from environment variables and the project's .env file."""

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# src/tr_banking/settings.py -> project root is two levels above the package directory.
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Values come from env vars first, then .env; field names map to UPPER_CASE env vars."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # SecretStr masks the value in repr/str, so it cannot leak through logs or tracebacks.
    evds_api_key: SecretStr
    evds_base_url: str = "https://evds3.tcmb.gov.tr/igmevdsms-dis/"
    db_path: Path = PROJECT_ROOT / "data" / "tr_banking.db"
    raw_dir: Path = PROJECT_ROOT / "data" / "raw"
    series_config_path: Path = PROJECT_ROOT / "config" / "series.yaml"


def get_settings() -> Settings:
    return Settings()
