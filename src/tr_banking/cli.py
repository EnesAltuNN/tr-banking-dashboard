"""Command line entry point: `tr-banking fetch` and `tr-banking backfill`."""

import argparse
import logging
from collections.abc import Sequence
from datetime import date, timedelta

from tr_banking.pipeline import run_evds_update
from tr_banking.settings import get_settings
from tr_banking.sources.evds import EvdsApiError, EvdsResponseError

logger = logging.getLogger("tr_banking.cli")

# A few weeks of overlap so revisions to recent weeks are picked up on every fetch.
DEFAULT_FETCH_WEEKS = 8


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(args.verbose)

    today = date.today()
    if args.command == "fetch":
        start, end = today - timedelta(weeks=args.weeks), today
    else:
        start, end = args.start, args.end or today
        if start > end:
            parser.error(f"--start {start} is after --end {end}")

    try:
        run_evds_update(get_settings(), start, end)
    except (EvdsApiError, EvdsResponseError, ValueError) as exc:
        # Expected failures: one clear line (full traceback with -v), non-zero exit code.
        logger.error("%s failed: %s", args.command, exc, exc_info=args.verbose)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tr-banking", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    commands = parser.add_subparsers(dest="command", required=True)

    fetch = commands.add_parser("fetch", help="fetch the latest weeks")
    fetch.add_argument(
        "--weeks",
        type=positive_int,
        default=DEFAULT_FETCH_WEEKS,
        help=f"how many weeks back to fetch (default: {DEFAULT_FETCH_WEEKS})",
    )

    backfill = commands.add_parser("backfill", help="load history from a start date")
    backfill.add_argument("--start", type=iso_date, required=True, help="YYYY-MM-DD")
    backfill.add_argument("--end", type=iso_date, help="YYYY-MM-DD (default: today)")
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
