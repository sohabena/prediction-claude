"""S102: verify the historical-odds stores against themselves, against Cricsheet and against each other.

Pure functions. The input is what each store holds (:class:`training.historical_odds.OddsRecord` rows, as
:func:`training.historical_odds.read_store` returns them) and the Cricsheet matches read by
:func:`load_cricsheet_matches`. The output is one result dict: one entry per check, an item per source (or per pair of
sources), numbers, up to 50 examples and an overall status. No database, no network, no file writes: the script
``scripts/verify_historical_odds.py`` reads the stores and writes the report.

A check with no records to test is WARN, never OK: nothing has been verified yet. A FAIL means no model may be judged
on that store until the fault is fixed.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import Counter
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import numpy as np

from training import historical_odds as ho
from training.dataset import CricsheetIndex, team_key
from training.market import fit_fav_longshot

# The Cricsheet folders scripts/build_training_dataset.py reads.
CRICSHEET_DIRS = ("ipl", "odis", "t20s", "tests")

BAD_PRICE_FAIL_SHARE = 0.01
OVERROUND_LOW, OVERROUND_HIGH = 1.00, 1.15
OVERROUND_WARN_SHARE = 0.05
LINK_RATE_MIN = 0.80
RESULT_AGREEMENT_MIN = 0.98
CALIBRATION_SLOPE_LOW, CALIBRATION_SLOPE_HIGH = 0.8, 1.2
CALIBRATION_MIN_MATCHES = 100
CROSS_SOURCE_MAX_MEDIAN = 0.03
MIN_START_YEAR = 2000
EXAMPLE_LIMIT = 50
ROW_LIMIT = 200

THRESHOLDS: dict[str, Any] = {
    "bad_price_fail_share": BAD_PRICE_FAIL_SHARE,
    "overround_range": [OVERROUND_LOW, OVERROUND_HIGH],
    "overround_warn_share": OVERROUND_WARN_SHARE,
    "link_rate_min": LINK_RATE_MIN,
    "result_agreement_min": RESULT_AGREEMENT_MIN,
    "calibration_slope_range": [CALIBRATION_SLOPE_LOW, CALIBRATION_SLOPE_HIGH],
    "calibration_min_matches": CALIBRATION_MIN_MATCHES,
    "cross_source_max_median": CROSS_SOURCE_MAX_MEDIAN,
    "min_start_year": MIN_START_YEAR,
}

_RANK = {"OK": 0, "WARN": 1, "FAIL": 2}
_CS_FAMILY = {"T20": "t20", "IT20": "t20", "ODI": "odi", "ODM": "odi", "TEST": "test", "MDM": "test"}
_EPOCH = datetime(1, 1, 1, tzinfo=timezone.utc)

CHECK_ORDER = (
    "readable",
    "schema",
    "duplicates",
    "range_consistency",
    "overround",
    "times",
    "cricsheet_link",
    "results",
    "calibration",
    "cross_source",
    "coverage",
)

CHECK_TITLES = {
    "readable": "Stores can be read",
    "schema": "Record contract",
    "duplicates": "Duplicate records",
    "range_consistency": "Open, min, max and close",
    "overround": "Overround (booksum)",
    "times": "Start and snapshot times",
    "cricsheet_link": "Link to Cricsheet",
    "results": "Winners agree with Cricsheet",
    "calibration": "Closing probability against results",
    "cross_source": "Agreement between sources",
    "coverage": "Coverage",
}

CHECK_MEANING = {
    "readable": "Each store opens and holds records. A store that cannot be read is FAIL; a store with no records "
                "yet is WARN.",
    "schema": "Every record follows the S98 contract. FAIL on any Betfair bookmaker, or when more than 1% of records "
              "have a price of 1.0 or less. WARN on any other contract problem.",
    "duplicates": "The same event, bookmaker, kind and snapshot time appears twice. FAIL on any duplicate.",
    "range_consistency": "Within one event, bookmaker and side: the minimum is not above the opening price, and the "
                         "closing price is not above the maximum. WARN on any break.",
    "overround": "The booksum of each kind (open, close, min, snapshot, average) should sit between 1.00 and 1.15. "
                 "The best-of kind max is skipped because it can sit below 1. WARN when more than 5% of records fall "
                 "outside.",
    "times": "Starts should not be in the future or before 2000 (WARN). A snapshot taken after the match start is "
             "look-ahead: FAIL.",
    "cricsheet_link": "Share of events linked to a Cricsheet match by team names and date (within one day). WARN when "
                      "a competition and season links under 80%, or when the format or gender disagrees with "
                      "Cricsheet.",
    "results": "For linked events with a stated winner, the winner agrees with the Cricsheet result (oriented by the "
               "link). FAIL under 98%.",
    "calibration": "Closing fair P(home) (one close per event, from the bookmaker with the most events) against the "
                   "Cricsheet result: log loss, Brier score, a 10-bin table and the calibration slope. WARN when the "
                   "slope is outside 0.8 to 1.2 or there are fewer than 100 matches.",
    "cross_source": "Events in two stores (same Cricsheet match): median absolute difference of closing fair P(home). "
                    "WARN over 0.03.",
    "coverage": "Events and records per source, competition, season, format and gender. Information only; WARN when "
                "a store is empty.",
}


# --------------------------------------------------------------------------- Cricsheet loading
def load_cricsheet_matches(
    raw_dir: Path, folders: Sequence[str] = CRICSHEET_DIRS
) -> tuple[list[dict[str, Any]], list[str]]:
    """Cricsheet matches from ``<raw_dir>/<folder>/*.json``, with what the verifier needs.

    Same folders and file reading as ``scripts/build_training_dataset._load_cricsheet_entries``, which keeps only
    teams and dates. This loader also keeps match type, gender, event name and the stated winner
    (``info.outcome.winner``; None when the match has no decided winner). Returns ``(matches, unreadable_paths)``.
    """
    matches: list[dict[str, Any]] = []
    unreadable: list[str] = []
    for folder in folders:
        for path in sorted((Path(raw_dir) / folder).glob("*.json")):
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
            except (OSError, ValueError):
                unreadable.append(str(path))
                continue
            info = data.get("info", {}) if isinstance(data, dict) else {}
            if not isinstance(info, dict):
                info = {}
            outcome = info.get("outcome") if isinstance(info.get("outcome"), dict) else {}
            event = info.get("event")
            event_name = event.get("name") if isinstance(event, dict) else event
            matches.append(
                {
                    "match_id": f"{folder}/{path.stem}",
                    "teams": info.get("teams"),
                    "dates": info.get("dates") or [],
                    "match_type": info.get("match_type"),
                    "gender": info.get("gender"),
                    "event": event_name,
                    "winner": outcome.get("winner") or None,
                }
            )
    return matches, unreadable


# --------------------------------------------------------------------------- small helpers
def _worst(statuses: Any) -> str:
    return max(statuses, key=lambda status: _RANK[status], default="OK")


def _aware(value: Any) -> Optional[datetime]:
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _key_time(value: Any) -> Optional[str]:
    dt = _aware(value)
    return dt.isoformat() if dt else None


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _round(value: Any, digits: int = 4) -> Optional[float]:
    number = _finite(value)
    return None if number is None else round(number, digits)


def _item(
    label: str,
    status: str,
    note: str,
    numbers: Optional[Mapping[str, Any]] = None,
    rows: Optional[Sequence[Mapping[str, Any]]] = None,
    examples: Optional[Sequence[Mapping[str, Any]]] = None,
) -> dict[str, Any]:
    return {
        "label": label,
        "status": status,
        "note": note,
        "numbers": dict(numbers or {}),
        "rows": [dict(row) for row in (rows or [])][:ROW_LIMIT],
        "examples": [dict(example) for example in (examples or [])][:EXAMPLE_LIMIT],
    }


def _empty_item(label: str) -> dict[str, Any]:
    return _item(label, "WARN", "no records in this store yet, so nothing is verified", {"records": 0})


def _usable(record: ho.OddsRecord) -> bool:
    """True when the prices are fit for arithmetic (no bad_price or bad_draw_price)."""
    problems = ho.validate(record)
    return "bad_price" not in problems and "bad_draw_price" not in problems


def _season(start: Any) -> str:
    dt = _aware(start)
    return str(dt.year) if dt else "unknown"


def _cs_family(match_type: Any) -> str:
    return _CS_FAMILY.get(str(match_type or "").strip().upper(), "other")


def _ordered_sources(records_by_source: Mapping[str, Any]) -> list[str]:
    """Every known source, always (an empty one is WARN, not absent), then any unknown keys."""
    extra = sorted(name for name in records_by_source if name not in ho.SOURCES)
    return list(ho.SOURCES) + extra


# --------------------------------------------------------------------------- events and links
def _cs_home_won(detail: Optional[Mapping[str, Any]], cs: Optional[Mapping[str, Any]], aliases: Any) -> Optional[int]:
    """1 when the home side won, 0 when the away side won, None when the Cricsheet result is not decided.

    ``reversed`` from the link says Cricsheet lists our away side first, so the first-listed side's result is flipped.
    """
    if not detail or not cs:
        return None
    teams = cs.get("teams") or []
    winner = cs.get("winner")
    if not winner or len(teams) != 2:
        return None
    won = team_key(winner, aliases)
    if won == team_key(teams[0], aliases):
        first_won = True
    elif won == team_key(teams[1], aliases):
        first_won = False
    else:
        return None
    home_won = (not first_won) if detail.get("reversed") else first_won
    return 1 if home_won else 0


def _closing_prices(records: Sequence[ho.OddsRecord]) -> tuple[Optional[str], dict[str, float]]:
    """One closing fair P(home) per event, from the bookmaker that closes the most events (ties: alphabetical).

    Where a bookmaker closes an event more than once, the latest close (by snapshot time) is used.
    """
    closes = [r for r in records if r.kind == "close" and _usable(r)]
    events_by_book: dict[str, set[str]] = {}
    for record in closes:
        events_by_book.setdefault(record.bookmaker, set()).add(str(record.source_event_id))
    if not events_by_book:
        return None, {}
    book = max(sorted(events_by_book), key=lambda name: len(events_by_book[name]))
    latest: dict[str, tuple[tuple[bool, datetime], ho.OddsRecord]] = {}
    for record in closes:
        if record.bookmaker != book:
            continue
        snap = _aware(record.snapshot_time_utc)
        rank = (snap is not None, snap or _EPOCH)
        event_id = str(record.source_event_id)
        if event_id not in latest or rank > latest[event_id][0]:
            latest[event_id] = (rank, record)
    closing: dict[str, float] = {}
    for event_id, (_, record) in latest.items():
        try:
            closing[event_id] = float(ho.p_home_fair(record))
        except (ValueError, ZeroDivisionError):
            continue
    return book, closing


def _events_for(
    records: Sequence[ho.OddsRecord],
    index: CricsheetIndex,
    cs_by_id: Mapping[str, Mapping[str, Any]],
    aliases: Any,
) -> tuple[list[dict[str, Any]], Optional[str]]:
    """One entry per event: its representative record, Cricsheet link and result, stated winner and closing P."""
    grouped: dict[str, list[ho.OddsRecord]] = {}
    for record in records:
        grouped.setdefault(str(record.source_event_id), []).append(record)
    book, closing = _closing_prices(records)
    events: list[dict[str, Any]] = []
    for event_id, rows in grouped.items():
        rep = rows[0]
        detail = ho.link_detail(rep, index)
        cs = cs_by_id.get(detail["cricsheet_match_id"]) if detail else None
        stated = next((r.winner for r in rows if r.winner in ("home", "away")), None)
        events.append(
            {
                "event_id": event_id,
                "competition": rep.competition or "(none)",
                "season": _season(rep.start_time_utc),
                "format": rep.format,
                "gender": rep.gender,
                "home": ho.normalise_team(rep.home),
                "away": ho.normalise_team(rep.away),
                "detail": detail,
                "cs": cs,
                "cs_home_won": _cs_home_won(detail, cs, aliases),
                "stated_winner": stated,
                "close_p": closing.get(event_id),
            }
        )
    return events, book


# --------------------------------------------------------------------------- per-source checks
def _readable_items(sources: Sequence[str], records: Mapping[str, list], errors: Mapping[str, str]) -> list[dict]:
    items = []
    for source in sources:
        if source in errors:
            items.append(_item(source, "FAIL", f"store could not be read: {errors[source]}"))
        elif not records[source]:
            items.append(_item(source, "WARN", "no store yet, or it holds no records", {"records": 0}))
        else:
            items.append(_item(source, "OK", f"{len(records[source])} records read", {"records": len(records[source])}))
    return items


def _schema_item(source: str, records: Sequence[ho.OddsRecord]) -> dict[str, Any]:
    counts: Counter = Counter()
    bad_price_records = 0
    records_with_problems = 0
    examples: list[dict[str, Any]] = []
    for record in records:
        problems = ho.validate(record)
        counts.update(problems)
        if problems:
            records_with_problems += 1
        if "bad_price" in problems:
            bad_price_records += 1
        if problems and len(examples) < EXAMPLE_LIMIT:
            examples.append({"event": record.source_event_id, "bookmaker": record.bookmaker,
                             "kind": record.kind, "problems": ", ".join(problems)})
    total = len(records)
    bad_share = bad_price_records / total
    forbidden = counts.get("forbidden_bookmaker", 0)
    other = sum(count for code, count in counts.items() if code != "forbidden_bookmaker")
    parts = []
    if forbidden:
        parts.append(f"{forbidden} record(s) from Betfair, which is never used")
    if bad_share > BAD_PRICE_FAIL_SHARE:
        parts.append(f"{bad_share:.1%} of records have a price of 1.0 or less (limit 1%)")
    if other:
        parts.append(f"{other} other contract problem(s)")
    if forbidden or bad_share > BAD_PRICE_FAIL_SHARE:
        status = "FAIL"
    elif other:
        status = "WARN"
    else:
        status = "OK"
    rows = [{"problem": code, "records": count} for code, count in sorted(counts.items())]
    return _item(
        source,
        status,
        "; ".join(parts) or "every record passes the contract",
        {"records": total, "records_with_problems": records_with_problems,
         "bad_price_share": _round(bad_share), "forbidden_bookmaker": forbidden},
        rows=rows,
        examples=examples,
    )


def _duplicates_item(source: str, records: Sequence[ho.OddsRecord]) -> dict[str, Any]:
    seen: Counter = Counter()
    for record in records:
        seen[(str(record.source_event_id), record.bookmaker, record.kind, _key_time(record.snapshot_time_utc))] += 1
    duplicated = [(key, count) for key, count in seen.items() if count > 1]
    extra = sum(count - 1 for _, count in duplicated)
    examples = [{"event": key[0], "bookmaker": key[1], "kind": key[2], "snapshot": key[3], "copies": count}
                for key, count in duplicated]
    note = f"{extra} duplicate record(s)" if extra else "no duplicate (event, bookmaker, kind, snapshot)"
    return _item(source, "FAIL" if extra else "OK", note,
                 {"records": len(records), "duplicate_records": extra, "duplicated_keys": len(duplicated)},
                 examples=examples)


def _range_item(source: str, records: Sequence[ho.OddsRecord]) -> dict[str, Any]:
    prices: dict[tuple, dict[str, float]] = {}
    for record in records:
        if not _usable(record):
            continue
        for side, price in (("home", record.price_home), ("away", record.price_away)):
            prices.setdefault((str(record.source_event_id), record.bookmaker, side), {}).setdefault(
                record.kind, float(price))
    checked = 0
    violations: list[dict[str, Any]] = []
    for (event_id, book, side), by_kind in prices.items():
        if "min" in by_kind and "open" in by_kind:
            checked += 1
            if by_kind["min"] > by_kind["open"] + 1e-9:
                violations.append({"event": event_id, "bookmaker": book, "side": side,
                                   "rule": "min <= open", "detail": f"min {by_kind['min']} above open {by_kind['open']}"})
        if "close" in by_kind and "max" in by_kind:
            checked += 1
            if by_kind["close"] > by_kind["max"] + 1e-9:
                violations.append({"event": event_id, "bookmaker": book, "side": side,
                                   "rule": "close <= max", "detail": f"close {by_kind['close']} above max {by_kind['max']}"})
    if not checked:
        note = "no event has open, min, max or close prices to compare (not applicable)"
    elif violations:
        note = f"{len(violations)} of {checked} comparisons break min <= open or close <= max"
    else:
        note = f"all {checked} comparisons hold"
    return _item(source, "WARN" if violations else "OK", note,
                 {"records": len(records), "comparisons": checked, "violations": len(violations)},
                 examples=violations)


def _overround_item(source: str, records: Sequence[ho.OddsRecord]) -> dict[str, Any]:
    by_kind: dict[str, list[tuple[float, ho.OddsRecord]]] = {}
    for record in records:
        if record.kind == "max" or not _usable(record):
            continue
        by_kind.setdefault(record.kind, []).append((ho.overround(record), record))
    rows: list[dict[str, Any]] = []
    examples: list[dict[str, Any]] = []
    worst = False
    for kind in sorted(by_kind):
        values = [value for value, _ in by_kind[kind]]
        outside = [(value, record) for value, record in by_kind[kind]
                   if value < OVERROUND_LOW or value > OVERROUND_HIGH]
        share = len(outside) / len(values)
        worst = worst or share > OVERROUND_WARN_SHARE
        rows.append({"kind": kind, "records": len(values), "median_overround": _round(statistics.median(values)),
                     "outside_share": _round(share)})
        for value, record in outside:
            if len(examples) < EXAMPLE_LIMIT:
                examples.append({"event": record.source_event_id, "bookmaker": record.bookmaker,
                                 "kind": kind, "overround": _round(value)})
    if not rows:
        return _item(source, "OK", "no usable prices to measure", {"records": len(records)})
    note = "; ".join(f"{row['kind']}: median {row['median_overround']}, {row['outside_share']:.1%} outside 1.00 to 1.15"
                     for row in rows)
    return _item(source, "WARN" if worst else "OK", note, {"records": len(records)}, rows=rows, examples=examples)


def _times_item(source: str, records: Sequence[ho.OddsRecord], now: datetime) -> dict[str, Any]:
    future = early = after_start = 0
    examples: list[dict[str, Any]] = []
    for record in records:
        start = _aware(record.start_time_utc)
        snap = _aware(record.snapshot_time_utc)
        if start is None:
            continue  # the schema check reports it
        if start > now:
            future += 1
            _note_example(examples, record, "start in the future", start, snap)
        if start.year < MIN_START_YEAR:
            early += 1
            _note_example(examples, record, "start before 2000", start, snap)
        if snap is not None and snap > start:
            after_start += 1
            _note_example(examples, record, "snapshot after the start (look-ahead)", start, snap)
    parts = []
    if after_start:
        parts.append(f"{after_start} snapshot(s) taken after the match start: look-ahead")
    if future:
        parts.append(f"{future} start(s) in the future")
    if early:
        parts.append(f"{early} start(s) before 2000")
    status = "FAIL" if after_start else ("WARN" if future or early else "OK")
    return _item(source, status, "; ".join(parts) or "no start in the future and no snapshot after its start",
                 {"records": len(records), "snapshots_after_start": after_start,
                  "starts_in_future": future, "starts_before_2000": early},
                 examples=examples)


def _note_example(examples: list, record: ho.OddsRecord, problem: str, start: datetime, snap: Optional[datetime]) -> None:
    if len(examples) < EXAMPLE_LIMIT:
        examples.append({"event": record.source_event_id, "bookmaker": record.bookmaker, "kind": record.kind,
                         "start": start.isoformat(), "snapshot": _key_time(snap), "problem": problem})


def _link_item(source: str, events: Sequence[dict[str, Any]]) -> dict[str, Any]:
    linked = [event for event in events if event["detail"]]
    groups: dict[tuple[str, str], list[int]] = {}
    unlinked: Counter = Counter()
    format_mismatch: list[str] = []
    gender_mismatch: list[str] = []
    for event in events:
        group = groups.setdefault((event["competition"], event["season"]), [0, 0])
        group[0] += 1
        if event["detail"]:
            group[1] += 1
            cs = event["cs"] or {}
            cs_format = _cs_family(cs.get("match_type"))
            if event["format"] != "other" and cs_format != "other" and cs_format != event["format"]:
                format_mismatch.append(event["event_id"])
            cs_gender = str(cs.get("gender") or "").lower()
            if cs_gender in ho.GENDERS and cs_gender != event["gender"]:
                gender_mismatch.append(event["event_id"])
        else:
            unlinked[event["home"]] += 1
            unlinked[event["away"]] += 1
    rows = [{"competition": comp, "season": season, "events": total, "linked": hit,
             "link_rate": _round(hit / total)} for (comp, season), (total, hit) in sorted(groups.items())]
    low = [row for row in rows if row["link_rate"] < LINK_RATE_MIN]
    examples = [{"team": name, "unlinked_events": count}
                for name, count in sorted(unlinked.items(), key=lambda kv: (-kv[1], kv[0]))]
    total = len(events)
    if not total:
        return _item(source, "WARN", "no events to link", {"events": 0})
    mismatches = len(format_mismatch) + len(gender_mismatch)
    parts = [f"{len(linked)} of {total} events linked ({len(linked) / total:.0%})"]
    if low:
        parts.append(f"{len(low)} competition and season group(s) under 80%")
    if mismatches:
        parts.append(f"format or gender disagrees with Cricsheet for {mismatches} event(s), e.g. "
                     f"{', '.join((format_mismatch + gender_mismatch)[:3])}")
    status = "WARN" if low or mismatches else "OK"
    return _item(source, status, "; ".join(parts),
                 {"events": total, "linked": len(linked), "link_rate": _round(len(linked) / total),
                  "format_mismatches": len(format_mismatch), "gender_mismatches": len(gender_mismatch)},
                 rows=rows, examples=examples)


def _results_item(source: str, events: Sequence[dict[str, Any]]) -> dict[str, Any]:
    with_stated = sum(1 for event in events if event["stated_winner"] is not None)
    compared = 0
    disagreements: list[dict[str, Any]] = []
    for event in events:
        if event["stated_winner"] is None or event["cs_home_won"] is None:
            continue
        compared += 1
        stated_home = 1 if event["stated_winner"] == "home" else 0
        if stated_home != event["cs_home_won"]:
            stated_team = event["home"] if stated_home else event["away"]
            disagreements.append({
                "event": event["event_id"], "home": event["home"], "away": event["away"],
                "stated_winner": stated_team, "cricsheet_winner": event["cs"]["winner"],
                "cricsheet_match_id": event["detail"]["cricsheet_match_id"],
            })
    agree = compared - len(disagreements)
    rate = agree / compared if compared else None
    numbers = {"events_with_stated_winner": with_stated, "compared": compared, "agree": agree,
               "agreement_rate": _round(rate)}
    if not compared:
        return _item(source, "WARN", "no event has both a stated winner and a decided Cricsheet result", numbers)
    status = "FAIL" if rate < RESULT_AGREEMENT_MIN else "OK"
    return _item(source, status, f"{agree} of {compared} agree ({rate:.1%}; limit 98%)", numbers,
                 examples=disagreements)


def _calibration_item(source: str, events: Sequence[dict[str, Any]], book: Optional[str]) -> dict[str, Any]:
    pairs = [(event["close_p"], event["cs_home_won"]) for event in events
             if event["close_p"] is not None and event["cs_home_won"] is not None
             and 0.0 < event["close_p"] < 1.0]
    numbers: dict[str, Any] = {"matches": len(pairs), "bookmaker": book}
    if not pairs:
        return _item(source, "WARN", "no event has both a closing price and a decided Cricsheet result", numbers)
    p = np.array([pair[0] for pair in pairs], dtype=float)
    y = np.array([float(pair[1]) for pair in pairs], dtype=float)
    clipped = np.clip(p, 1e-12, 1.0 - 1e-12)
    numbers["log_loss"] = _round(-np.mean(y * np.log(clipped) + (1.0 - y) * np.log(1.0 - clipped)))
    numbers["brier"] = _round(np.mean((p - y) ** 2))
    try:
        fit = fit_fav_longshot(p, y, n_bins=10)
    except (ValueError, RuntimeError) as exc:
        return _item(source, "WARN", f"calibration not fitted: {exc}", numbers)
    numbers.update({"slope": _round(fit.b), "slope_se": _round(fit.se_b), "intercept": _round(fit.a)})
    rows = [{"bin": f"{b.lo:.1f} to {b.hi:.1f}", "matches": b.n, "mean_p_market": _round(b.mean_p_market),
             "observed_win_rate": _round(b.observed_rate), "ci_low": _round(b.ci_low), "ci_high": _round(b.ci_high)}
            for b in fit.bins]
    problems = []
    if len(pairs) < CALIBRATION_MIN_MATCHES:
        problems.append(f"fewer than {CALIBRATION_MIN_MATCHES} matches, so the slope is not yet reliable")
    if not CALIBRATION_SLOPE_LOW <= fit.b <= CALIBRATION_SLOPE_HIGH:
        problems.append(f"slope {fit.b:.2f} is outside 0.8 to 1.2")
    note = (f"{len(pairs)} matches; log loss {numbers['log_loss']}, Brier {numbers['brier']}, "
            f"calibration slope {fit.b:.2f} (95% about {fit.b - 1.96 * fit.se_b:.2f} to {fit.b + 1.96 * fit.se_b:.2f})")
    if problems:
        note += "; " + "; ".join(problems)
    return _item(source, "WARN" if problems else "OK", note, numbers, rows=rows)


def _coverage_item(source: str, records: Sequence[ho.OddsRecord]) -> dict[str, Any]:
    groups: dict[tuple, dict[str, Any]] = {}
    for record in records:
        key = (record.competition or "(none)", _season(record.start_time_utc), record.format, record.gender)
        group = groups.setdefault(key, {"events": set(), "records": 0})
        group["events"].add(str(record.source_event_id))
        group["records"] += 1
    rows = [{"competition": c, "season": s, "format": f, "gender": g, "events": len(v["events"]),
             "records": v["records"]} for (c, s, f, g), v in sorted(groups.items())]
    events = sum(row["events"] for row in rows)
    return _item(source, "OK", f"{len(rows)} competition, season, format and gender combination(s); {events} events",
                 {"records": len(records), "events": events}, rows=rows)


def _cross_source_items(sources: Sequence[str], events: Mapping[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Per pair of sources: median absolute difference of closing P(home) for the same Cricsheet match.

    Every probability is oriented to the first-listed Cricsheet side before comparing.
    """
    oriented: dict[str, dict[str, float]] = {}
    for source in sources:
        for event in events[source]:
            if event["close_p"] is None or event["detail"] is None:
                continue
            p_first = event["close_p"] if not event["detail"]["reversed"] else 1.0 - event["close_p"]
            oriented.setdefault(event["detail"]["cricsheet_match_id"], {}).setdefault(source, p_first)
    items = []
    for first, second in combinations(sources, 2):
        label = f"{first} vs {second}"
        shared = [(cs_id, by_source[first], by_source[second])
                  for cs_id, by_source in oriented.items() if first in by_source and second in by_source]
        if not shared:
            items.append(_item(label, "WARN", "no event is in both stores (same Cricsheet match, closing price)",
                               {"shared_events": 0}))
            continue
        diffs = [abs(a - b) for _, a, b in shared]
        median = statistics.median(diffs)
        examples = sorted(
            ({"cricsheet_match_id": cs_id, first: _round(a), second: _round(b), "difference": _round(abs(a - b))}
             for cs_id, a, b in shared),
            key=lambda row: -row["difference"],
        )
        items.append(_item(
            label,
            "WARN" if median > CROSS_SOURCE_MAX_MEDIAN else "OK",
            f"median absolute difference {median:.3f} over {len(shared)} shared event(s) (limit 0.03)",
            {"shared_events": len(shared), "median_abs_diff": _round(median), "max_abs_diff": _round(max(diffs))},
            examples=examples,
        ))
    return items


# --------------------------------------------------------------------------- assembly
def _check(name: str, items: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return {
        "title": CHECK_TITLES[name],
        "meaning": CHECK_MEANING[name],
        "status": _worst(item["status"] for item in items),
        "items": list(items),
    }


def verify(
    records_by_source: Mapping[str, Sequence[ho.OddsRecord]],
    cricsheet_matches: Sequence[Mapping[str, Any]],
    *,
    aliases: Optional[Mapping[str, str]] = None,
    now: Optional[datetime] = None,
    read_errors: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    """Run every S102 check. ``aliases`` maps team names the way ``scripts/build_training_dataset._team_alias`` does.

    ``now`` fixes the clock for the future-start test (default: the current time, UTC). ``read_errors`` maps a source
    whose store could not be read to the reason; that source fails the ``readable`` check.
    Returns ``{"status", "generated_at_utc", "thresholds", "sources", "cricsheet_matches", "checks"}``; each check
    holds ``status``, ``title``, ``meaning`` and ``items`` (one per source or pair of sources).
    """
    clock = _aware(now) or datetime.now(timezone.utc)
    errors = dict(read_errors or {})
    alias_map = dict(aliases) if aliases else None
    sources = _ordered_sources(records_by_source)
    records = {source: list(records_by_source.get(source) or []) for source in sources}
    index = CricsheetIndex(cricsheet_matches, team_alias=alias_map)
    cs_by_id = {str(match.get("match_id")): match for match in cricsheet_matches}
    events: dict[str, list[dict[str, Any]]] = {}
    books: dict[str, Optional[str]] = {}
    for source in sources:
        events[source], books[source] = _events_for(records[source], index, cs_by_id, alias_map)

    def per_source(fn: Any) -> list[dict[str, Any]]:
        return [_empty_item(source) if not records[source] else fn(source) for source in sources]

    items = {
        "readable": _readable_items(sources, records, errors),
        "schema": per_source(lambda s: _schema_item(s, records[s])),
        "duplicates": per_source(lambda s: _duplicates_item(s, records[s])),
        "range_consistency": per_source(lambda s: _range_item(s, records[s])),
        "overround": per_source(lambda s: _overround_item(s, records[s])),
        "times": per_source(lambda s: _times_item(s, records[s], clock)),
        "cricsheet_link": per_source(lambda s: _link_item(s, events[s])),
        "results": per_source(lambda s: _results_item(s, events[s])),
        "calibration": per_source(lambda s: _calibration_item(s, events[s], books[s])),
        "cross_source": _cross_source_items(sources, events),
        "coverage": per_source(lambda s: _coverage_item(s, records[s])),
    }
    checks = {name: _check(name, items[name]) for name in CHECK_ORDER}
    return {
        "status": _worst(check["status"] for check in checks.values()),
        "generated_at_utc": clock.isoformat(),
        "thresholds": THRESHOLDS,
        "sources": {source: {"records": len(records[source]), "events": len(events[source])} for source in sources},
        "cricsheet_matches": len(cricsheet_matches),
        "checks": checks,
    }


# --------------------------------------------------------------------------- report text
def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.4g}"
    if isinstance(value, (list, dict)):
        return json.dumps(value, default=str)
    return str(value).replace("|", "/")


def _table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(_fmt(cell) for cell in row) + " |")
    return lines


def _example_text(example: Mapping[str, Any]) -> str:
    return "; ".join(f"{key} {_fmt(value)}" for key, value in example.items())


def render_markdown(result: Mapping[str, Any]) -> str:
    """The plain-English report: a summary table, then one section per check, FAIL first."""
    checks = result["checks"]
    ordered = sorted(CHECK_ORDER, key=lambda name: (-_RANK[checks[name]["status"]], CHECK_ORDER.index(name)))
    lines = [
        "# Historical odds verification",
        "",
        f"Generated {result['generated_at_utc']} (UTC). **Overall: {result['status']}.**",
        "",
        f"Cricsheet matches read: {result.get('cricsheet_matches', 0)}.",
    ]
    cricsheet_files = result.get("cricsheet_files")
    if cricsheet_files:
        lines.append(f"Cricsheet files unreadable: {len(cricsheet_files.get('unreadable', []))}.")
    lines += ["", "## Stores", ""]
    manifests = result.get("manifests") or {}
    lines += _table(
        ["Source", "Records", "Events", "Provenance"],
        [[source, info["records"], info["events"], (manifests.get(source) or {}).get("provenance", "")]
         for source, info in result["sources"].items()],
    )
    lines += ["", "## Summary", ""]
    lines += _table(["Check", "Status", "What it tests"],
                    [[checks[name]["title"], checks[name]["status"], checks[name]["meaning"]] for name in ordered])
    for name in ordered:
        check = checks[name]
        lines += ["", f"## {check['title']}: {check['status']}", "", check["meaning"], ""]
        lines += _table(["Item", "Status", "Detail"],
                        [[item["label"], item["status"], _item_detail(item)] for item in check["items"]])
        for item in check["items"]:
            if item["rows"]:
                lines += ["", f"Table for {item['label']}:", ""]
                headers = list(item["rows"][0].keys())
                lines += _table(headers, [[row.get(h) for h in headers] for row in item["rows"][:50]])
            if item["examples"]:
                lines += ["", f"Examples for {item['label']}:", ""]
                for example in item["examples"][:10]:
                    lines.append(f"- {_example_text(example)}")
                if len(item["examples"]) > 10:
                    lines.append(f"- ... and {len(item['examples']) - 10} more in report.json")
    return "\n".join(lines) + "\n"


def _item_detail(item: Mapping[str, Any]) -> str:
    numbers = ", ".join(f"{key} {_fmt(value)}" for key, value in item["numbers"].items())
    return f"{item['note']}" + (f" ({numbers})" if numbers else "")


__all__ = [
    "CHECK_ORDER",
    "CHECK_TITLES",
    "CRICSHEET_DIRS",
    "THRESHOLDS",
    "load_cricsheet_matches",
    "render_markdown",
    "verify",
]
