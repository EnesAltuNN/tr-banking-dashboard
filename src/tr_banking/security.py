"""Keep secrets out of files we publish (the raw responses become a public CI artifact)."""

from collections.abc import Iterable, Sequence
from pathlib import Path

from psycopg.conninfo import conninfo_to_dict

from tr_banking.settings import Settings

# Shorter values are too likely to match by accident (and are not real credentials anyway).
MIN_SECRET_LENGTH = 6


def secret_values(settings: Settings) -> list[str]:
    """Every configured secret: the EVDS key, the full DATABASE_URL and its password alone."""
    values: list[str] = []
    if settings.evds_api_key is not None:
        values.append(settings.evds_api_key.get_secret_value())
    if settings.database_url is not None:
        url = settings.database_url.get_secret_value()
        values.append(url)
        values.append(str(conninfo_to_dict(url).get("password") or ""))
    return [value for value in values if len(value) >= MIN_SECRET_LENGTH]


def files_containing_secrets(directory: Path, secrets: Sequence[str]) -> list[Path]:
    """Files under `directory` whose name or content contains any of the secrets."""
    if not secrets or not directory.exists():
        return []
    encoded = [secret.encode() for secret in secrets]
    return [
        path
        for path in sorted(directory.rglob("*"))
        if path.is_file() and _leaks(path, secrets, encoded)
    ]


def mask(text: str, secrets: Iterable[str]) -> str:
    for secret in secrets:
        text = text.replace(secret, "***")
    return text


def _leaks(path: Path, secrets: Sequence[str], encoded: Sequence[bytes]) -> bool:
    if any(secret in str(path) for secret in secrets):
        return True
    content = path.read_bytes()
    return any(secret in content for secret in encoded)
