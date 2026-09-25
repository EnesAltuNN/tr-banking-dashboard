"""Command line entry point: `tr-banking fetch | backfill | db migrate | db check`."""

import argparse
import logging
import sqlite3
from collections.abc import Sequence
from datetime import date, timedelta

import psycopg

from tr_banking.db import PostgresRepository, Repository, StorageError, open_repository
from tr_banking.db.postgres import describe_connection
from tr_banking.pipeline import IMPLEMENTED_SOURCES, UpdateError, run_update
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
            run_db_command(args.db_command)
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


def run_db_command(command: str) -> None:
    settings = get_settings()
    if command == "check" and settings.database_url is not None:
        report_connection(settings.database_url.get_secret_value())
    with open_repository(settings) as repo:
        if command == "migrate":
            repo.init_schema()
            logger.info("%s schema is up to date", repo.backend)
        else:
            report_status(repo)


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
    db_commands.add_parser("migrate", help="create or upgrade the schema (safe to re-run)")
    db_commands.add_parser("check", help="show connection, schema and row counts")
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
