"""Glue between config, sources and storage."""

import logging
from collections.abc import Sequence
from datetime import date

from tr_banking.config import SeriesSpec, load_series_config
from tr_banking.db.repository import Repository
from tr_banking.settings import Settings
from tr_banking.sources.evds import EvdsClient

logger = logging.getLogger(__name__)


def load_evds(
    repo: Repository,
    client: EvdsClient,
    specs: Sequence[SeriesSpec],
    start: date,
    end: date,
) -> int:
    """Register the series, fetch their observations and upsert them; returns rows written."""
    if not specs:
        raise ValueError("no EVDS series configured")
    for spec in specs:
        repo.upsert_series(spec)
    observations = client.fetch_observations([spec.code for spec in specs], start, end)
    return repo.upsert_observations("evds", observations)


def run_evds_update(settings: Settings, start: date, end: date) -> int:
    """Open the real client and database from settings and load EVDS data for [start, end]."""
    specs = load_series_config(settings.series_config_path).for_source("evds")
    logger.info("updating %d EVDS series for %s..%s", len(specs), start, end)
    with (
        Repository(settings.db_path) as repo,
        EvdsClient(settings.evds_api_key, settings.evds_base_url, settings.raw_dir) as client,
    ):
        repo.init_schema()
        written = load_evds(repo, client, specs, start, end)
    logger.info("done: %d observations written to %s", written, settings.db_path)
    return written
