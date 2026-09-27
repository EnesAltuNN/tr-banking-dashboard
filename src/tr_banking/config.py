"""Load and validate config/series.yaml into typed models."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

# Allowed values live here, not as SQL CHECK constraints, so a new source or module
# only needs a code change, never a schema migration.
Source = Literal["evds", "bddk", "bkm", "bank_site"]
Frequency = Literal["daily", "weekly", "monthly"]
Module = Literal["credit", "cards", "rates", "macro"]


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
    # deflator marks the price index used for real (inflation-adjusted) values.
    max_age_days: int | None = Field(default=None, ge=1)
    deflator: bool = False


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
        if len([spec for spec in self.series if spec.deflator]) > 1:
            raise ValueError("at most one series may be the deflator")
        return self

    @property
    def deflator(self) -> SeriesSpec | None:
        return next((spec for spec in self.series if spec.deflator), None)

    def for_source(self, source: Source) -> list[SeriesSpec]:
        return [spec for spec in self.series if spec.source == source]


def load_series_config(path: Path) -> SeriesConfig:
    # Explicit UTF-8: Windows would otherwise use the ANSI codepage and garble Turkish letters.
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return SeriesConfig.model_validate(data)
