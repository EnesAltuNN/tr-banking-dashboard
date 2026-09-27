"""Glue between config, sources and storage."""

import logging
from collections.abc import Sequence
from datetime import date

import pandas as pd

from tr_banking.config import SeriesSpec, Source, load_series_config
from tr_banking.db import Repository, open_repository
from tr_banking.settings import Settings
from tr_banking.sources import ObservationClient
from tr_banking.sources.bddk import BddkClient
from tr_banking.sources.bkm import BkmClient
from tr_banking.sources.common import SourceApiError
from tr_banking.sources.evds import EvdsClient

logger = logging.getLogger(__name__)

# Sources with a client today; config.Source also lists the planned ones.
IMPLEMENTED_SOURCES: tuple[Source, ...] = ("evds", "bddk", "bkm")
# Monthly data is published with a lag and sometimes revised: re-read a few months.
MONTHLY_LOOKBACK_MONTHS = 3


class UpdateError(RuntimeError):
    """At least one source failed; the others were still loaded."""


def load_source(
    repo: Repository,
    source: Source,
    client: ObservationClient,
    specs: Sequence[SeriesSpec],
    start: date,
    end: date,
) -> int:
    """Register the series, fetch their observations and upsert them; returns rows written."""
    if not specs:
        raise ValueError(f"no {source} series configured")
    for spec in specs:
        repo.upsert_series(spec)
    # One request per frequency: EVDS silently converts every series in a request to the
    # lowest frequency among them, e.g. weekly loans to monthly values next to a monthly CPI.
    frames = []
    for frequency in sorted({spec.frequency for spec in specs}):
        codes = [spec.code for spec in specs if spec.frequency == frequency]
        window_start = fetch_window_start(frequency, start)
        frames.append(client.fetch_observations(codes, window_start, end))
    return repo.upsert_observations(source, pd.concat(frames, ignore_index=True))


def fetch_window_start(frequency: str, start: date) -> date:
    """Monthly requests start on the first of a month, a few months back.

    EVDS returns nothing for a monthly request starting mid-month, and BKM and TÜİK publish
    with a lag of one to two months, so the latest fetch re-reads recent months.
    """
    if frequency != "monthly":
        return start
    month_index = start.year * 12 + start.month - 1 - MONTHLY_LOOKBACK_MONTHS
    return date(month_index // 12, month_index % 12 + 1, 1)


def open_client(source: Source, settings: Settings) -> EvdsClient | BddkClient | BkmClient:
    if source == "evds":
        if settings.evds_api_key is None:
            raise ValueError("EVDS_API_KEY is not set (add it to .env or the environment)")
        return EvdsClient(settings.evds_api_key, settings.evds_base_url, settings.raw_dir)
    if source == "bddk":
        return BddkClient(settings.raw_dir)
    if source == "bkm":
        return BkmClient(settings.raw_dir)
    raise ValueError(f"source {source!r} has no client yet")


def run_update(
    settings: Settings,
    start: date,
    end: date,
    sources: Sequence[Source] = IMPLEMENTED_SOURCES,
) -> dict[str, int]:
    """Load [start, end] for each source into the database; returns rows written per source.

    A failing source is logged and skipped so it cannot block the others; UpdateError is
    raised at the end if any source failed.
    """
    config = load_series_config(settings.series_config_path)
    written: dict[str, int] = {}
    failed: list[str] = []
    with open_repository(settings) as repo:
        # Check (Postgres) or create (SQLite) the schema; the fetch never runs migrations.
        repo.ensure_ready()
        for source in sources:
            specs = config.for_source(source)
            logger.info("updating %d %s series for %s..%s", len(specs), source, start, end)
            try:
                with open_client(source, settings) as client:
                    written[source] = load_source(repo, source, client, specs, start, end)
            except (SourceApiError, ValueError) as exc:
                logger.error("%s update failed: %s", source, exc)
                failed.append(source)
    logger.info("done: %s written to %s", written or "nothing", repo.backend)
    if failed:
        raise UpdateError(f"update failed for: {', '.join(failed)}")
    return written
