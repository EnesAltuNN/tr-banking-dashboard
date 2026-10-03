"""Weekly AI summary: facts from Python, text from the model (the API is always mocked)."""

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

import anthropic
import httpx2
import pytest

from tr_banking import cli
from tr_banking.config import load_series_config
from tr_banking.db import SqliteRepository
from tr_banking.settings import PROJECT_ROOT, Settings
from tr_banking.summary import FALLBACK_BETA, SummaryError, build_brief, write_summary

from .test_dashboard import populated_db

CONFIG = load_series_config(PROJECT_ROOT / "config" / "series.yaml")
TEXTS = {"tr": "Tüketici kredileri yıllık %37,7 arttı.", "en": "Consumer loans rose 37.7%."}


def reply(body: dict[str, Any] | None = None, **overrides: Any) -> dict[str, Any]:
    """A Messages API response whose text block is the structured JSON output."""
    message = {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5",
        "content": [{"type": "text", "text": json.dumps(body or TEXTS, ensure_ascii=False)}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 2000, "output_tokens": 400},
    }
    return message | overrides


class FakeApi:
    """Records requests and answers with a fixed response."""

    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return httpx2.Response(200, json=self.response)

    def client(self) -> anthropic.Anthropic:
        return anthropic.Anthropic(
            api_key="test-key-not-real",
            max_retries=0,
            http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(self)),
        )


def brief_from(tmp_path: Path) -> dict[str, Any]:
    with SqliteRepository(populated_db(tmp_path / "test.db")) as repo:
        return build_brief(repo.list_series(), repo.get_observations(), CONFIG)


# --- facts (pure Python) ---


def test_brief_holds_the_numbers_of_every_module(tmp_path: Path) -> None:
    brief = brief_from(tmp_path)

    assert brief["data_week"] == "2024-07-12"
    loans = {item["series"]: item for item in brief["loans"]["series"]}
    assert loans["Consumer loans (total)"]["value"] == pytest.approx(3223.3)
    assert loans["Consumer loans (total)"]["unit"] == "billion TRY"
    assert "real_yearly_change_pct" in loans["Consumer loans (total)"]
    rates = {item["series"]: item for item in brief["interest_rates"]}
    assert rates["General purpose loan rate"]["rate_pct"] == pytest.approx(77.9)
    assert "real_rate_pp" not in rates["CBRT policy rate (one-week repo)"]
    # Spread: commercial loan rate minus the TRY deposit rate (56.16% on 2024-07-12).
    spread = brief["loan_deposit_spread"]
    assert spread["week"] == "2024-07-12"
    commercial = rates["Commercial loan rate"]["rate_pct"]
    assert spread["value_pp"] == pytest.approx(commercial - 56.16, abs=0.01)
    # The USD fixture (Dec 2024) shares no week with the BDDK fixture: nothing to convert.
    assert brief["banking_sector"]["fx_deposits_in_usd"] is None
    assert brief["inflation"]["month"] == "2024-12"
    assert brief["inflation"]["value_pct"] == pytest.approx(44.38, abs=0.01)
    ratios = {item["ratio"]: item for item in brief["banking_sector"]["ratios"]}
    assert ratios["Non-performing loan ratio"]["value_pct"] == pytest.approx(50.0)
    assert brief["card_spending"][0]["month"] == "2024-07"
    assert brief["unusual_changes"] == []


def test_brief_is_plain_json(tmp_path: Path) -> None:
    brief = brief_from(tmp_path)

    text = json.dumps(brief, ensure_ascii=False, allow_nan=False)  # no NaN sneaks through
    assert json.loads(text) == brief


# --- the model call ---


def test_request_asks_for_json_with_a_server_side_fallback(tmp_path: Path) -> None:
    api = FakeApi(reply())

    model, text_tr, text_en = write_summary(api.client(), brief_from(tmp_path), "claude-opus-5")

    assert (model, text_tr, text_en) == ("claude-opus-5", TEXTS["tr"], TEXTS["en"])
    [request] = api.requests
    body = json.loads(request.content)
    assert body["model"] == "claude-opus-5"
    assert body["fallbacks"] == "default"
    assert FALLBACK_BETA in request.headers["anthropic-beta"]
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert set(body["output_config"]["format"]["schema"]["required"]) == {"tr", "en"}
    assert "Every number you mention must appear in the JSON" in body["system"]
    assert json.loads(body["messages"][0]["content"])["data_week"] == "2024-07-12"


def test_the_model_that_actually_wrote_it_is_reported(tmp_path: Path) -> None:
    # After a server-side fallback the response names the fallback model.
    api = FakeApi(reply(model="claude-opus-4-8"))

    model, _, _ = write_summary(api.client(), brief_from(tmp_path), "claude-opus-5")

    assert model == "claude-opus-4-8"


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (
            reply(
                content=[],
                stop_reason="refusal",
                stop_details={"type": "refusal", "category": "cyber", "explanation": None},
            ),
            "declined the request",
        ),
        (reply(stop_reason="max_tokens"), "cut off"),
        (reply(content=[{"type": "text", "text": "not json"}]), "did not return JSON"),
        (reply({"tr": "Metin", "en": " "}), "missing its Turkish or English"),
    ],
)
def test_unusable_answers_fail_loudly(
    tmp_path: Path, response: dict[str, Any], message: str
) -> None:
    with pytest.raises(SummaryError, match=message):
        write_summary(FakeApi(response).client(), brief_from(tmp_path), "claude-opus-5")


# --- tr-banking summarize ---


def use_settings(monkeypatch: pytest.MonkeyPatch, **values: Any) -> Settings:
    settings = Settings(_env_file=None, **values)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    return settings


def test_dry_run_prints_the_facts_and_sends_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    use_settings(monkeypatch, db_path=populated_db(tmp_path / "t.db"))
    monkeypatch.setattr(cli, "write_summary", lambda *args: pytest.fail("no API call expected"))

    assert cli.main(["summarize", "--dry-run"]) == 0

    assert json.loads(capsys.readouterr().out)["data_week"] == "2024-07-12"


def test_summarize_needs_a_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    use_settings(monkeypatch, db_path=populated_db(tmp_path / "t.db"))

    assert cli.main(["summarize"]) == 1

    assert "ANTHROPIC_API_KEY is not set" in caplog.text


def test_summarize_stores_once_per_data_week(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    db_path = populated_db(tmp_path / "t.db")
    use_settings(monkeypatch, db_path=db_path, anthropic_api_key="test-key-not-real")
    calls: list[str] = []

    def fake_write(client: anthropic.Anthropic, brief: dict, model: str) -> tuple[str, str, str]:
        calls.append(model)
        return model, "Metin", "Text"

    monkeypatch.setattr(cli, "write_summary", fake_write)

    assert cli.main(["summarize"]) == 0
    assert cli.main(["summarize"]) == 0  # same data week: nothing to do
    assert cli.main(["summarize", "--force"]) == 0

    assert calls == ["claude-opus-5", "claude-opus-5"]
    assert "already summarized" in caplog.text
    with SqliteRepository(db_path) as repo:
        latest = repo.latest_summary()
    assert (latest.data_date, latest.text_tr) == (date(2024, 7, 12), "Metin")
    assert json.loads(latest.input)["data_week"] == "2024-07-12"


def test_claude_key_is_a_secret_for_the_raw_scan(tmp_path: Path) -> None:
    from tr_banking.security import secret_values

    settings = Settings(_env_file=None, anthropic_api_key="sk-ant-test-key-not-real-123")

    assert "sk-ant-test-key-not-real-123" in secret_values(settings)
