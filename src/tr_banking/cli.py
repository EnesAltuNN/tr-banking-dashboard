"""Command line entry point for fetching, database setup and CI health checks."""

import argparse
import logging
import sqlite3
from collections.abc import Sequence
from datetime import date, timedelta

import pandas as pd
import psycopg

from tr_banking.config import load_series_config
from tr_banking.db import PostgresRepository, Repository, StorageError, open_repository
from tr_banking.db.postgres import describe_connection
from tr_banking.freshness import find_stale
from tr_banking.pipeline import IMPLEMENTED_SOURCES, UpdateError, run_update
from tr_banking.security import files_containing_secrets, mask, secret_values
from tr_banking.settings import get_settings
from tr_banking.sources.common import SourceApiError

logger = logging.getLogger("tr_banking.cli")

# A few weeks of overlap so revisions to recent weeks are picked up on every fetch.
DEFAULT_FETCH_WEEKS = 8


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(args.verbose)

    today = date.today()
    if args.command == "backfill" and args.start > (args.end or today):
        parser.error(f"--start {args.start} is after --end {args.end or today}")

    try:
        if args.command == "db":
            return run_db_command(args.db_command, getattr(args, "check_only", False))
        elif args.command == "check-freshness":
            return check_freshness(args.source)
        elif args.command == "scan-raw":
            return scan_raw()
        else:
            if args.command == "fetch":
                start, end = today - timedelta(weeks=args.weeks), today
            else:
                start, end = args.start, args.end or today
            sources = (args.source,) if args.source else IMPLEMENTED_SOURCES
            run_update(get_settings(), start, end, sources)
    except (UpdateError, SourceApiError, StorageError, ValueError, psycopg.Error) as exc:
        # Expected failures: one clear line (full traceback with -v), non-zero exit code.
        logger.error("%s failed: %s", args.command, exc, exc_info=args.verbose)
        return 1
    return 0


def run_db_command(command: str, check_only: bool = False) -> int:
    settings = get_settings()
    if command == "check" and settings.database_url is not None:
        report_connection(settings.database_url.get_secret_value())
    with open_repository(settings) as repo:
        if command == "check":
            report_status(repo)
            return 0
        if check_only:
            # Read-only: works for the least-privilege role the scheduled job uses.
            if pending := repo.pending_migrations():
                logger.error(
                    "pending migrations: %s (apply them as the table owner: "
                    "tr-banking db migrate with the postgres connection string)",
                    pending,
                )
                return 1
            logger.info("%s schema is up to date, no pending migrations", repo.backend)
            return 0
        pending = repo.pending_migrations()
        repo.init_schema()
        if pending:
            logger.info("applied %d migration(s): %s", len(pending), ", ".join(pending))
        logger.info("%s schema is up to date", repo.backend)
        return 0


def check_freshness(source: str | None) -> int:
    """Exit 1 if any configured series has no data or data older than its rhythm allows."""
    settings = get_settings()
    config = load_series_config(settings.series_config_path)
    sources = (source,) if source else IMPLEMENTED_SOURCES
    expected = pd.DataFrame(
        [(s.source, s.code, s.frequency) for src in sources for s in config.for_source(src)],
        columns=["source", "code", "frequency"],
    )
    with open_repository(settings) as repo:
        latest = repo.latest_dates()[["source", "code", "latest_date"]]
    # Left join: a configured series missing from the database counts as stale.
    merged = expected.merge(latest, on=["source", "code"], how="left")

    for name, newest in merged.groupby("source")["latest_date"].max().items():
        logger.info("%s: newest data %s", name, newest.date() if pd.notna(newest) else "none")
    stale = find_stale(merged, date.today())
    for row in stale.itertuples():
        age = "no data" if pd.isna(row.age_days) else f"{int(row.age_days)} days old"
        logger.error("stale: %s %s (%s)", row.source, row.code, age)
    if not stale.empty:
        logger.error("%d of %d series are stale", len(stale), len(merged))
        return 1
    logger.info("all %d series are fresh", len(merged))
    return 0


def scan_raw() -> int:
    """Exit 1 if a saved raw response contains a configured secret (checked before upload)."""
    settings = get_settings()
    secrets = secret_values(settings)
    if not secrets:
        logger.warning("no secrets configured, nothing to look for")
        return 0
    files = [path for path in settings.raw_dir.rglob("*") if path.is_file()]
    leaks = files_containing_secrets(settings.raw_dir, secrets)
    for path in leaks:
        logger.error("secret found in %s", mask(str(path), secrets))
    if leaks:
        return 1
    logger.info("scanned %d raw files: no secrets found", len(files))
    return 0


def report_connection(conninfo: str) -> None:
    """Log where DATABASE_URL points (host, port, role) without ever logging the string."""
    info = describe_connection(conninfo)
    logger.info(
        "connection: %s, host %s, port %d, role %s",
        info.kind,
        info.host,
        info.port,
        info.role or "-",
    )
    for warning in info.warnings:
        logger.warning("connection: %s", warning)


def report_status(repo: Repository) -> None:
    logger.info("backend: %s", repo.backend)
    if isinstance(repo, PostgresRepository):
        status = repo.server_status()
        logger.info(
            "server: PostgreSQL %s, connected as %s", status["server_version"], status["role"]
        )
        migrations = status["migrations"]
        if migrations is None:
            logger.info(
                "migrations: not visible to %s (expected for a read-only role)", status["role"]
            )
        else:
            logger.info("migrations: %s", ", ".join(migrations) or "none")
            if pending := repo.pending_migrations():
                logger.warning("pending migrations: %s (run `tr-banking db migrate`)", pending)
        for table, enabled in sorted(status["rls"].items()):
            logger.info("row level security on %s: %s", table, "on" if enabled else "OFF")
    try:
        counts = repo.counts()
    except (sqlite3.Error, psycopg.Error):
        logger.warning("tables not found: run `tr-banking db migrate`")
        return
    logger.info("rows: %d series, %d observations", counts["series"], counts["observations"])
    logger.info("last fetch: %s", repo.last_fetched_at() or "never")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tr-banking", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    commands = parser.add_subparsers(dest="command", required=True)

    # Options shared by both commands.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--source", choices=IMPLEMENTED_SOURCES, help="only this source (default: all)"
    )

    fetch = commands.add_parser("fetch", parents=[common], help="fetch the latest weeks")
    fetch.add_argument(
        "--weeks",
        type=positive_int,
        default=DEFAULT_FETCH_WEEKS,
        help=f"how many weeks back to fetch (default: {DEFAULT_FETCH_WEEKS})",
    )

    backfill = commands.add_parser(
        "backfill", parents=[common], help="load history from a start date"
    )
    backfill.add_argument("--start", type=iso_date, required=True, help="YYYY-MM-DD")
    backfill.add_argument("--end", type=iso_date, help="YYYY-MM-DD (default: today)")

    db = commands.add_parser("db", help="database setup and health check")
    db_commands = db.add_subparsers(dest="db_command", required=True)
    migrate = db_commands.add_parser(
        "migrate", help="create or upgrade the schema (safe to re-run; needs the owner role)"
    )
    migrate.add_argument(
        "--check",
        dest="check_only",
        action="store_true",
        help="apply nothing; exit 1 if a migration is pending (works for read-only roles)",
    )
    db_commands.add_parser("check", help="show connection, schema and row counts")

    commands.add_parser(
        "check-freshness",
        parents=[common],
        help="exit 1 if any series is older than its publishing rhythm allows",
    )
    commands.add_parser("scan-raw", help="exit 1 if a raw response file contains a secret")
    return parser


def configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {value!r}") from None


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


if __name__ == "__main__":
    raise SystemExit(main())
