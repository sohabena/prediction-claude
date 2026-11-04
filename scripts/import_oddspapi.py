"""S100: import OddsPapi historical match-winner odds for finished cricket fixtures into the S98 store.

Usage::

    python scripts/import_oddspapi.py --from 2026-01-01 --to 2026-01-31
        dry run (the default): makes no request; reads the cache and lists the calls --run would make, at 5 s a call
    python scripts/import_oddspapi.py --from 2026-01-01 --to 2026-01-31 --run
        makes the calls (a cached response is never requested again) and writes data/historical_odds/oddspapi/

Options: ``--bookmakers pinnacle,bet365,1xbet`` (Betfair is removed whatever is listed); ``--market-id 101`` (the
match-winner market; unverified, see scraper/importers/oddspapi.py).

The key comes from ``ODDSPAPI_API_KEY`` in the environment, or from the same name in ``.env`` in the repo root.
Without it the script exits 2 and asks the operator to create a free key. The key is never printed or written.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scraper.importers import oddspapi as op  # noqa: E402
from training import historical_odds as ho  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
KEY_ENV = "ODDSPAPI_API_KEY"
DISCOVERY_WINDOW_DAYS = 9  # one billable fixtures call per 9 days (the API allows under 10)


def _key_from_dotenv(path: Path) -> str:
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        name, sep, value = line.partition("=")
        if sep and name.strip() == KEY_ENV:
            return value.strip().strip("\"'")
    return ""


def api_key(environ: Mapping[str, str], dotenv_path: Path) -> str:
    """The key from the environment, else from ``.env``; empty when neither has it."""
    return (environ.get(KEY_ENV) or "").strip() or _key_from_dotenv(dotenv_path)


def _date(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not a date (YYYY-MM-DD): {text}") from exc


def _duration(seconds: float) -> str:
    minutes, secs = divmod(int(round(seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} h {minutes} min"
    return f"{minutes} min {secs} s"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import OddsPapi historical match-winner odds (S100).")
    parser.add_argument("--from", dest="date_from", type=_date, required=True, help="first UTC day (YYYY-MM-DD)")
    parser.add_argument("--to", dest="date_to", type=_date, required=True, help="last UTC day (YYYY-MM-DD)")
    parser.add_argument("--bookmakers", default=",".join(op.DEFAULT_BOOKMAKERS), help="comma-separated slugs")
    parser.add_argument("--market-id", default=op.MATCH_WINNER_MARKET_ID, help="match-winner market id")
    parser.add_argument("--run", action="store_true", help="make the calls (default: dry run, no request)")
    return parser


def main(
    argv: Optional[list[str]] = None,
    *,
    environ: Optional[Mapping[str, str]] = None,
    dotenv_path: Path = REPO_ROOT / ".env",
    transport: Any = None,
    clock: Any = None,
    sleep: Any = None,
    root: Optional[Path] = None,
) -> int:
    args = build_parser().parse_args(argv)
    key = api_key(os.environ if environ is None else environ, Path(dotenv_path))
    if not key:
        print(
            f"{KEY_ENV} is not set. Create a free OddsPapi account, copy the API key and put it in {dotenv_path} "
            f"as {KEY_ENV}=<key>, or export it in this shell. Nothing was requested.",
            file=sys.stderr,
        )
        return 2
    if args.date_to < args.date_from:
        print("--to is before --from", file=sys.stderr)
        return 2
    slugs, removed = op.requested_bookmakers(args.bookmakers.split(","))
    if removed:
        print(f"Betfair removed from the bookmaker list ({removed} entr{'y' if removed == 1 else 'ies'}); never requested.")
    if not slugs:
        print("no bookmakers left to request", file=sys.stderr)
        return 2

    store_root = Path(root) if root is not None else REPO_ROOT / ho.DEFAULT_ROOT
    client = op.OddsPapiClient(
        key, transport=transport, clock=clock, sleep=sleep, root=store_root, offline=not args.run
    )
    span = f"{args.date_from.isoformat()} to {args.date_to.isoformat()}"
    try:
        fixtures = op.discover_fixtures(client, args.date_from, args.date_to, window_days=DISCOVERY_WINDOW_DAYS)
        records, stats = op.import_fixtures(client, fixtures, slugs, args.market_id)
    except op.OddsPapiError as exc:
        print(f"OddsPapi: {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()

    if not args.run:
        odds_calls = stats["uncached_calls"]
        discovery = client.stats["uncached_windows"]
        total = odds_calls + discovery
        groups = len(op.bookmaker_groups(slugs))
        print(f"OddsPapi dry run, no request made: finished cricket fixtures {span}")
        print(f"  fixtures in the cache: {len(fixtures)}; discovery windows not cached: {discovery} (one call each)")
        print(f"  bookmakers: {', '.join(slugs)} ({groups} call(s) per fixture); market {args.market_id}")
        print(f"  odds calls not cached: {odds_calls}; records already cached: {len(records)}")
        note = " (at least: more fixtures appear once discovery is cached)" if discovery else ""
        print(f"  {total} call(s) at {op.COOLDOWN_S:.0f} s a call: about {_duration(total * op.COOLDOWN_S)}{note}")
        print("  add --run to make the calls")
        return 0

    provenance = (
        f"OddsPapi historical odds (GET /v4/historical-odds, api.oddspapi.io), finished cricket fixtures {span}, "
        f"bookmakers {', '.join(slugs)}, market {args.market_id}; prices as OddsPapi returns them"
    )
    folder = ho.write_store(
        op.SOURCE,
        records,
        provenance=provenance,
        root=store_root,
        extra={
            "date_range": {"from": args.date_from.isoformat(), "to": args.date_to.isoformat()},
            "requested_bookmakers": slugs,
            "market_id": str(args.market_id),
            "fixtures": len(fixtures),
            "stats": dict(stats),
            "client": dict(client.stats),
        },
    )
    print(f"wrote {len(records)} records for {len(fixtures)} fixtures to {folder}")
    print(f"  stats: {dict(stats)}; requests: {client.stats['requests']}; cache hits: {client.stats['cache_hits']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
