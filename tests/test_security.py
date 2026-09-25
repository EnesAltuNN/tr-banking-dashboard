from pathlib import Path

from tr_banking.security import files_containing_secrets, mask, secret_values
from tr_banking.settings import Settings
from tr_banking.sources.common import REDACTED, save_raw_response

KEY = "fake-evds-key-123"
PASSWORD = "dbPassword42"
URL = f"postgresql://postgres.abcdefgh:{PASSWORD}@aws-0-eu-central-1.pooler.supabase.com:5432/postgres"


def test_secret_values_include_key_url_and_password() -> None:
    settings = Settings(_env_file=None, evds_api_key=KEY, database_url=URL)

    assert secret_values(settings) == [KEY, URL, PASSWORD]


def test_no_secrets_configured() -> None:
    assert secret_values(Settings(_env_file=None)) == []


def test_short_values_are_ignored_to_avoid_false_alarms() -> None:
    settings = Settings(_env_file=None, database_url="postgresql://u:x@localhost/db")

    assert "x" not in secret_values(settings)


def test_finds_secret_in_content_and_in_file_name(tmp_path: Path) -> None:
    (tmp_path / "clean.json").write_text('{"items": []}', encoding="utf-8")
    (tmp_path / "content.json").write_text(f'{{"echo": "{PASSWORD}"}}', encoding="utf-8")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / f"{KEY}.json").write_text("{}", encoding="utf-8")

    leaks = files_containing_secrets(tmp_path, [KEY, PASSWORD])

    assert [path.name for path in leaks] == ["content.json", f"{KEY}.json"]


def test_missing_directory_has_no_leaks(tmp_path: Path) -> None:
    assert files_containing_secrets(tmp_path / "absent", [KEY]) == []


def test_mask() -> None:
    assert mask(f"data/raw/{KEY}.json", [KEY]) == "data/raw/***.json"


def test_save_raw_response_masks_given_secrets(tmp_path: Path) -> None:
    body = f'{{"error": "bad key {KEY}"}}'.encode()

    path = save_raw_response(tmp_path, body, "x", redact=[KEY])

    saved = path.read_text(encoding="utf-8")
    assert KEY not in saved
    assert REDACTED in saved


def test_save_raw_response_keeps_body_unchanged_without_secrets(tmp_path: Path) -> None:
    body = b'{"items": [1, 2]}'

    path = save_raw_response(tmp_path, body, "x", redact=[KEY])

    assert path.read_bytes() == body
