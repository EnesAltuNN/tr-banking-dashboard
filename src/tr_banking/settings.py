"""Runtime settings loaded from environment variables and the project's .env file."""

from pathlib import Path

from pydantic import SecretStr, field_validator
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
    # Optional here because only fetching needs it; the dashboard only reads the database.
    evds_api_key: SecretStr | None = None
    evds_base_url: str = "https://evds3.tcmb.gov.tr/igmevdsms-dis/"
    # Postgres connection string (it contains a password, hence SecretStr). When set, data
    # lives in Postgres/Supabase; when missing, in the local SQLite file at db_path.
    database_url: SecretStr | None = None
    db_path: Path = PROJECT_ROOT / "data" / "tr_banking.db"
    raw_dir: Path = PROJECT_ROOT / "data" / "raw"
    series_config_path: Path = PROJECT_ROOT / "config" / "series.yaml"

    @field_validator("evds_api_key", "database_url", mode="before")
    @classmethod
    def _blank_means_unset(cls, value: object) -> object:
        # `DATABASE_URL=` left empty in .env (or an empty CI secret) means "not configured".
        return None if isinstance(value, str) and not value.strip() else value


def get_settings() -> Settings:
    return Settings()
