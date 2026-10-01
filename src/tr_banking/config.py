"""Load and validate config/series.yaml into typed models."""

from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

# Allowed values live here, not as SQL CHECK constraints, so a new source or module
# only needs a code change, never a schema migration.
Source = Literal["evds", "bddk", "bkm", "bank_site"]
Frequency = Literal["daily", "weekly", "monthly"]
Module = Literal["credit", "cards", "rates", "banking", "macro"]
# What a series measures, independent of its source. The dashboard gives each category one
# color in every chart and uses it to pick the KPI tiles.
Category = Literal[
    "total",
    "consumer",
    "housing",
    "auto",
    "personal",
    "credit_card",
    "debit_card",
    "commercial",
    "policy",
    "online",
    "foreign",
    # banking sector (deposits, non-performing loans, bank groups)
    "deposits",
    "fx_deposits",
    "household_deposits",
    "commercial_deposits",
    "npl",
    "npl_consumer",
    "npl_commercial",
    "state_banks",
    "private_banks",
    "foreign_banks",
    # capital, funding and securities (BDDK tables 297, 293, 291)
    "equity",
    "fx_position",
    "cbrt_funding",
    "repo_funding",
    "foreign_bank_funding",
    "issued_securities",
    "reserve_requirements",
    "securities",
    "government_bonds",
    # deposit rate and the USD/TRY rate (EVDS)
    "deposit_rate",
    "usd_try",
]


class SeriesSpec(BaseModel):
    """One series definition; mirrors a row of the `series` table."""

    # frozen: specs are read-only; extra="forbid": a typo like `frequncy` fails loudly.
    model_config = ConfigDict(frozen=True, extra="forbid")

    source: Source
    code: str = Field(min_length=1)
    name_tr: str = Field(min_length=1)
    name_en: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    frequency: Frequency
    module: Module
    # Optional overrides that live only in config (not in the database):
    # max_age_days replaces the per-frequency freshness limit, e.g. for publication lags;
    # deflator marks the price index used for real (inflation-adjusted) values;
    # policy_rate marks the central bank rate whose changes are marked on the rate charts;
    # category groups series across sources and modules (colors, KPI tiles);
    # alerts: false keeps a series out of the unusual-change alerts (too noisy, see CONVENTIONS).
    max_age_days: int | None = Field(default=None, ge=1)
    deflator: bool = False
    policy_rate: bool = False
    category: Category | None = None
    alerts: bool = True


class SeriesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    series: list[SeriesSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_unique_codes(self) -> "SeriesConfig":
        seen: set[tuple[str, str]] = set()
        for spec in self.series:
            key = (spec.source, spec.code)
            if key in seen:
                raise ValueError(f"duplicate series: source={spec.source} code={spec.code}")
            seen.add(key)
        for role in ("deflator", "policy_rate"):
            if len([spec for spec in self.series if getattr(spec, role)]) > 1:
                raise ValueError(f"at most one series may be the {role}")
        return self

    @property
    def deflator(self) -> SeriesSpec | None:
        return next((spec for spec in self.series if spec.deflator), None)

    @property
    def policy_rate(self) -> SeriesSpec | None:
        return next((spec for spec in self.series if spec.policy_rate), None)

    def for_source(self, source: Source) -> list[SeriesSpec]:
        return [spec for spec in self.series if spec.source == source]


def load_series_config(path: Path) -> SeriesConfig:
    # Explicit UTF-8: Windows would otherwise use the ANSI codepage and garble Turkish letters.
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return SeriesConfig.model_validate(data)


class MpcCalendar(BaseModel):
    """CBRT Monetary Policy Committee meeting dates (config/mpc_meetings.yaml)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    meetings: list[date] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_order(self) -> "MpcCalendar":
        # Strictly increasing: catches a duplicate or a typo'd year when a line is added.
        for earlier, later in zip(self.meetings, self.meetings[1:], strict=False):
            if later <= earlier:
                raise ValueError(f"meetings must be in increasing order: {earlier} then {later}")
        return self


def load_mpc_calendar(path: Path) -> MpcCalendar:
    with path.open(encoding="utf-8") as f:
        return MpcCalendar.model_validate(yaml.safe_load(f))
