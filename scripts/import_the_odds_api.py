"""S101: import The Odds API historical cricket h2h odds into the S98 store (paid source, credit guard).

Usage::

    # Estimate only (the default, --max-credits 0): prints the planned calls and their credits. Spends nothing; event
    # lists and odds snapshots already in the cache are reused and listed as cached.
    python scripts/import_the_odds_api.py --sport cricket_big_bash --from 2025-12-01 --to 2025-12-31 --regions uk,eu,au

    # Fetch within a budget: every paid call is checked against --max-credits before it is sent.
    python scripts/import_the_odds_api.py --sport cricket_big_bash --from 2025-12-01 --to 2025-12-31 --max-credits 500

The key is read only from the environment variable THE_ODDS_API_KEY and is never printed. Without it the script exits
2: this source needs a paid plan, which is the operator's decision. Golden Rulebook sections 2 and 8: the operator
decides what is bought; this script never buys anything.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scraper.importers.the_odds_api import (  # noqa: E402
    DEFAULT_CACHE_ROOT,
    MARKET,
    SPORT_KEY_PATTERN,
    BudgetExceeded,
    OddsApiError,
    TheOddsApiClient,
    estimate_cost,
    events_in_window,
    latest_per_match_book,
    plan_closing_snapshots,
    records_from_snapshot,
)
from training.historical_odds import DEFAULT_ROOT, read_store, write_store  # noqa: E402

KEY_ENV = "THE_ODDS_API_KEY"
STORE_SOURCE = "the_odds_api"


def _day(text: str) -> date:
    return date.fromisoformat(text)


def _non_negative_int(text: str) -> int:
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError("--max-credits cannot be negative")
    return value


def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import The Odds API historical cricket h2h odds (paid).")
    parser.add_argument("--sport", required=True, help="sport key, e.g. cricket_big_bash")
    parser.add_argument("--from", dest="date_from", required=True, type=_day, help="first UTC day, YYYY-MM-DD")
    parser.add_argument("--to", dest="date_to", required=True, type=_day, help="last UTC day, YYYY-MM-DD (inclusive)")
    parser.add_argument("--regions", default="uk", help="comma-separated regions, e.g. uk,eu,au (default: uk)")
    parser.add_argument(
        "--max-credits",
        type=_non_negative_int,
        default=0,
        help="credit budget for paid calls; 0 (default) only estimates and spends nothing",
    )
    return parser.parse_args(argv)


def _region_list(text: str) -> list[str]:
    return sorted({part.strip() for part in text.split(",") if part.strip()})


def _estimate(client: TheOddsApiClient, sport: str, event_times, regions: list[str], events) -> int:
    """Prints the plan for a run without a budget. Returns 0."""
    uncached_days = [when for when in event_times if client.cached_events(sport, when) is None]
    snapshots = plan_closing_snapshots(events)
    uncached_snapshots = [when for when in snapshots if client.cached_odds(sport, when, regions, MARKET) is None]
    print(f" Event calls   : {len(event_times)} planned, {len(event_times) - len(uncached_days)} cached, "
          f"{len(uncached_days)} to buy")
    print(f" Odds calls    : {len(snapshots)} planned, {len(snapshots) - len(uncached_snapshots)} cached, "
          f"{len(uncached_snapshots)} to buy")
    for when in uncached_days:
        print(f"   events  {when:%Y-%m-%d %H:%M}Z  1 credit")
    for when in uncached_snapshots:
        print(f"   odds    {when:%Y-%m-%d %H:%M}Z  {estimate_cost(0, 1, regions, MARKET)} credits")
    total = estimate_cost(len(uncached_days), len(uncached_snapshots), regions, MARKET)
    print("-" * 60)
    print(f" Credits to buy this plan: {total}")
    if uncached_days:
        print(f" Odds calls for the {len(uncached_days)} day(s) without an event list are not planned yet; "
              "their event lists are counted above.")
    print(" Nothing was requested and nothing was written. Rerun with --max-credits N (N at least the total above)")
    print(" to fetch; cached responses cost nothing.")
    return 0


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    transport=None,
    cache_root: Path = DEFAULT_CACHE_ROOT,
    store_root: Path = DEFAULT_ROOT,
) -> int:
    args = _parse_args(argv)
    print("\n" + "=" * 60)
    print(" PHOENIX - The Odds API historical import (S101)")
    print("=" * 60)
    key = os.environ.get(KEY_ENV, "").strip()
    if not key:
        print(f"[ERROR] {KEY_ENV} is not set. This source needs a paid The Odds API plan, which is the operator's")
        print("        decision. Nothing was requested.")
        return 2
    if not SPORT_KEY_PATTERN.match(args.sport):
        print(f"[ERROR] invalid sport key {args.sport!r}")
        return 2
    if args.date_to < args.date_from:
        print("[ERROR] --to is before --from")
        return 2
    regions = _region_list(args.regions)
    if not regions:
        print("[ERROR] --regions is empty")
        return 2
    days = [args.date_from + timedelta(days=i) for i in range((args.date_to - args.date_from).days + 1)]
    event_times = [datetime(day.year, day.month, day.day, tzinfo=timezone.utc) for day in days]
    print(f" Sport         : {args.sport}")
    print(f" Window (UTC)  : {args.date_from} to {args.date_to} ({len(days)} day(s))")
    print(f" Regions       : {','.join(regions)}   market: {MARKET}")
    print(f" Credit budget : {args.max_credits}" + ("  (estimate only)" if args.max_credits == 0 else ""))
    print("-" * 60)

    client = TheOddsApiClient(key, transport=transport, cache_root=cache_root, max_credits=args.max_credits)
    try:
        if args.max_credits == 0:
            events = events_in_window(
                [p for p in (client.cached_events(args.sport, when) for when in event_times) if p],
                days[0],
                days[-1],
            )
            return _estimate(client, args.sport, event_times, regions, events)

        stop_reason: Optional[str] = None
        for when in event_times:
            if client.cached_events(args.sport, when) is not None:
                continue
            try:
                client.events(args.sport, when)
            except BudgetExceeded as exc:
                stop_reason = str(exc)
                break
        missing_days = [when for when in event_times if client.cached_events(args.sport, when) is None]
        events = events_in_window(
            [p for p in (client.cached_events(args.sport, when) for when in event_times) if p],
            days[0],
            days[-1],
        )
        snapshots = plan_closing_snapshots(events)
        print(f" Events in window: {len(events)} (event lists missing for {len(missing_days)} day(s))")
        print(f" Odds snapshots  : {len(snapshots)}")

        counts: dict[str, int] = {}
        records = []
        fetched = cached = 0
        for when in snapshots:
            payload = client.cached_odds(args.sport, when, regions, MARKET)
            if payload is None:
                try:
                    payload = client.odds(args.sport, when, regions, MARKET)
                except BudgetExceeded as exc:
                    stop_reason = stop_reason or str(exc)
                    break
                fetched += 1
            else:
                cached += 1
            records.extend(records_from_snapshot(payload, "close", counts=counts))

        complete = stop_reason is None and not missing_days
        existing, previous = read_store(STORE_SOURCE, root=store_root)
        merged = latest_per_match_book(list(existing) + records)
        run = {
            "sport": args.sport,
            "from": args.date_from.isoformat(),
            "to": args.date_to.isoformat(),
            "regions": regions,
            "market": MARKET,
            "credits_spent": client.spent,
            "requests_sent": client.requests_made,
            "odds_fetched": fetched,
            "odds_from_cache": cached,
            "records_added": len(records),
            "complete": complete,
            "stop_reason": stop_reason,
            "counts": dict(sorted(counts.items())),
        }
        runs = list(previous.get("runs", [])) + [run]
        folder = write_store(
            STORE_SOURCE,
            merged,
            provenance=(
                "The Odds API v4 historical h2h odds (paid plan), decimal prices, regions "
                f"{','.join(regions)}, one snapshot 5 minutes before each start"
            ),
            inputs=[],
            extra={"runs": runs, "last_run": run},
            root=store_root,
        )
        print("-" * 60)
        print(f" Credits spent this run : {client.spent}")
        if client.last_usage.get("remaining") is not None:
            print(f" Account credits left   : {client.last_usage['remaining']} (from the last response)")
        print(f" Odds fetched / cached  : {fetched} / {cached}")
        print(f" Records added          : {len(records)}; store now holds {len(merged)} records")
        print(f" Counts                 : {dict(sorted(counts.items()))}")
        print(f" Complete               : {complete}" + (f" (stopped: {stop_reason})" if stop_reason else ""))
        print(f" Store                  : {folder}")
        return 0
    except OddsApiError as exc:
        print(f"[ERROR] {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
