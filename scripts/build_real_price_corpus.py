"""
Build the real-price corpus from Betfair historical stream files (S35).

Walks a directory of Betfair stream files (.json, .jsonl, .bz2, .gz), keeps each
MATCH_ODDS market that has a result and enough in-play ticks, and writes one
gzip JSON file per market under ``<out>/<family>/`` plus ``<out>/manifest.json``.
Excluded markets are listed in the manifest with a reason code.

Real tick (README contract "real tick"): ``ltp_*`` is the last traded price and
``back_*``/``lay_*`` are ladder prices. Both are ``None`` when the file does not
carry them; ltp is never copied into back or lay. The result lives only in
``_meta`` on the first tick of each market.

Re-running on the same input rewrites nothing: identical bytes are skipped and the
manifest contains no timestamps.

Usage:
    python scripts/build_real_price_corpus.py --src <dir> --out data/real/betfair
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import statistics
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import timezone
from pathlib import Path
from typing import Any, Optional

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scraper.match_filter import MatchClassifier  # noqa: E402
from scraper.parsers.betfair_historical import (  # noqa: E402
    BetfairHistoricalParser,
    ParsedHistoricalMatch,
)
from scripts.cricsheet_harmonizer import canonicalize_team  # noqa: E402
from shared.logging import setup_logging  # noqa: E402
from shared.schemas import OddsEvent  # noqa: E402
from strategy.fair_value import family_of  # noqa: E402

logger = setup_logging("build_real_price_corpus")

SOURCE = "betfair_historical"
MARKET_MODEL = "real"
DEFAULT_OUT = _REPO_ROOT / "data" / "real" / "betfair"
MANIFEST_NAME = "manifest.json"
MIN_IN_PLAY_TICKS = 10
RESAMPLE_SECONDS = 3.0
STREAM_SUFFIXES = (".json", ".jsonl", ".bz2", ".gz")
SIMULATED_RE = re.compile(r"\b(SRL|Simulated|Virtual)\b", re.IGNORECASE)


@dataclass
class CorpusBuild:
    """Result of one build run."""

    accepted: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    written: int = 0
    unchanged: int = 0
    in_play_intervals: dict[str, list[float]] = field(default_factory=dict)


# ================================================================
# Discovery and exclusion
# ================================================================


def iter_stream_files(src: Path) -> list[Path]:
    """Stream files under ``src`` sorted by relative path. CSV files and the manifest are skipped."""
    files: list[Path] = []
    for path in src.rglob("*"):
        if not path.is_file():
            continue
        name = path.name.lower()
        if ".csv" in name or name == MANIFEST_NAME or not name.endswith(STREAM_SUFFIXES):
            continue
        files.append(path)
    return sorted(files, key=lambda p: p.relative_to(src).as_posix())


def rejection_reason(match: ParsedHistoricalMatch) -> Optional[tuple[str, str]]:
    """Return ``(reason_code, detail)`` when the market must be excluded, else None."""
    text = " ".join([match.event_name, match.competition, match.team_home, match.team_away])
    if SIMULATED_RE.search(text):
        return "simulated_event", match.event_name or match.competition
    if match.market_type != "MATCH_ODDS":
        return "not_match_odds", match.market_type or "missing"
    if match.runner_count < 2:
        return "too_few_runners", str(match.runner_count)
    if match.result_type not in ("win", "draw"):
        return "no_winner", match.result_type
    if match.in_play_ticks < MIN_IN_PLAY_TICKS:
        return "too_few_in_play_ticks", str(match.in_play_ticks)
    return None


# ================================================================
# Conversion to real ticks
# ================================================================


def _canonical_names(match: ParsedHistoricalMatch) -> tuple[str, str]:
    home = canonicalize_team(match.team_home) or match.team_home
    away = canonicalize_team(match.team_away) or match.team_away
    return home, away


def convert_market(
    match: ParsedHistoricalMatch, classifier: MatchClassifier
) -> tuple[list[dict[str, Any]], dict[str, Any], list[float]]:
    """
    Convert one parsed market into real ticks, a manifest entry, and the in-play
    tick intervals in seconds.
    """
    team_home, team_away = _canonical_names(match)

    winner = ""
    loser = ""
    winner_is_home: Optional[bool] = None
    if match.result_type == "win":
        winner_is_home = match.winner == match.team_home
        winner = team_home if winner_is_home else team_away
        loser = team_away if winner_is_home else team_home

    event = OddsEvent(
        match_id=match.match_id,
        timestamp=match.start_time,
        team_home=team_home,
        team_away=team_away,
        competition=match.competition,
    )
    match_format = classifier.classify(event).format.value
    family = family_of(match_format)

    ticks: list[dict[str, Any]] = []
    for index, raw in enumerate(match.ticks):
        tick: dict[str, Any] = {
            "time": raw["timestamp"].astimezone(timezone.utc).isoformat(),
            "market_id": match.market_id,
            "match_id": match.match_id,
            "team_home": team_home,
            "team_away": team_away,
            "competition": match.competition,
            "match_format": match_format,
            "family": family,
            "in_play": bool(raw.get("in_play")),
            "ltp_home": raw.get("ltp_home"),
            "ltp_away": raw.get("ltp_away"),
            "ltp_draw": raw.get("ltp_draw"),
            "back_home": raw.get("back_home"),
            "lay_home": raw.get("lay_home"),
            "back_away": raw.get("back_away"),
            "lay_away": raw.get("lay_away"),
            "traded_volume_home": raw.get("traded_volume_home"),
            "traded_volume_away": raw.get("traded_volume_away"),
            "source": SOURCE,
            "data_tier": match.data_tier,
            "market_model": MARKET_MODEL,
        }
        if index == 0:
            tick["_meta"] = {
                "winner": winner,
                "loser": loser,
                "winner_is_home": winner_is_home,
                "result_type": match.result_type,
            }
        ticks.append(tick)

    in_play_times = [t["timestamp"] for t in match.ticks if t.get("in_play")]
    intervals = [(later - earlier).total_seconds() for earlier, later in zip(in_play_times, in_play_times[1:])]

    entry: dict[str, Any] = {
        "market_id": match.market_id,
        "match_id": match.match_id,
        "family": family,
        "match_format": match_format,
        "competition": match.competition,
        "team_home": team_home,
        "team_away": team_away,
        "start": ticks[0]["time"],
        "n_ticks": len(ticks),
        "n_in_play": match.in_play_ticks,
        "data_tier": match.data_tier,
        "winner": winner,
        "result_type": match.result_type,
        "is_major": bool(classifier.is_international_or_major(event)),
    }
    return ticks, entry, intervals


# ================================================================
# Writing
# ================================================================


def market_relpath(family: str, market_id: str) -> str:
    """Relative path of one market file inside the corpus root (always forward slashes)."""
    safe = re.sub(r"[^0-9A-Za-z._-]", "_", market_id)
    return f"{family}/{safe}.json.gz"


def _encode_ticks(ticks: list[dict[str, Any]]) -> bytes:
    payload = json.dumps(ticks, sort_keys=True, separators=(",", ":"))
    return gzip.compress(payload.encode("utf-8"), compresslevel=9, mtime=0)


def _write_if_changed(path: Path, payload: bytes, result: CorpusBuild) -> None:
    if path.exists() and path.read_bytes() == payload:
        result.unchanged += 1
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    result.written += 1


def build_corpus(src: Path, out: Path, *, resample_seconds: float = RESAMPLE_SECONDS) -> CorpusBuild:
    """Build the corpus from every stream file under ``src`` into ``out``."""
    parser = BetfairHistoricalParser(resample_seconds=resample_seconds, min_ticks=0)
    classifier = MatchClassifier()
    result = CorpusBuild()
    seen: set[str] = set()
    out.mkdir(parents=True, exist_ok=True)

    for path in iter_stream_files(src):
        source = path.relative_to(src).as_posix()
        try:
            matches = parser.parse_stream_market_file(path)
        except (OSError, EOFError, ValueError) as exc:
            result.rejected.append({
                "market_id": "",
                "source": source,
                "reason": "unreadable_file",
                "detail": type(exc).__name__,
            })
            logger.warning("real_corpus_unreadable_file", source=source, error=type(exc).__name__)
            continue

        for match in sorted(matches, key=lambda m: m.market_id):
            rejection = rejection_reason(match)
            if rejection is None and match.market_id in seen:
                rejection = ("duplicate_market", source)
            if rejection is not None:
                reason, detail = rejection
                result.rejected.append({
                    "market_id": match.market_id,
                    "source": source,
                    "reason": reason,
                    "detail": detail,
                })
                continue

            seen.add(match.market_id)
            ticks, entry, intervals = convert_market(match, classifier)
            entry["source"] = source
            entry["file"] = market_relpath(entry["family"], match.market_id)
            payload = _encode_ticks(ticks)
            entry["sha1"] = hashlib.sha1(payload).hexdigest()
            _write_if_changed(out / entry["file"], payload, result)
            result.accepted.append(entry)
            result.in_play_intervals.setdefault(entry["data_tier"], []).extend(intervals)

    result.accepted.sort(key=lambda e: (e["family"], e["market_id"]))
    result.rejected.sort(key=lambda r: (r["source"], r["market_id"], r["reason"]))
    manifest = {
        "schema_version": 1,
        "source": SOURCE,
        "market_model": MARKET_MODEL,
        "min_in_play_ticks": MIN_IN_PLAY_TICKS,
        "resample_seconds": resample_seconds,
        "tier_rule": "basic = no ladder in file; advanced = ladder levels 0-2 only; pro = deeper ladder",
        "counts": {
            "accepted": len(result.accepted),
            "rejected_by_reason": dict(sorted(Counter(r["reason"] for r in result.rejected).items())),
        },
        "accepted": result.accepted,
        "rejected": result.rejected,
    }
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _write_if_changed(out / MANIFEST_NAME, manifest_bytes, result)
    return result


# ================================================================
# Loader
# ================================================================


def load_real_corpus(
    family: Optional[str] = None,
    tier: Optional[str] = None,
    out: Optional[Path] = None,
) -> list[dict[str, Any]]:
    """
    Return one episode per accepted market, optionally filtered by family and data tier.

    Each episode holds the market metadata plus ``ticks`` (real tick dicts; ``time`` is an
    ISO-8601 UTC string). The result appears only in ``ticks[0]["_meta"]``. Each file's sha1
    is checked against the manifest.
    """
    root = Path(out) if out is not None else DEFAULT_OUT
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.exists():
        raise FileNotFoundError(f"no real-price corpus at {manifest_path}; run scripts/build_real_price_corpus.py")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    episodes: list[dict[str, Any]] = []
    for entry in manifest["accepted"]:
        if family is not None and entry["family"] != family:
            continue
        if tier is not None and entry["data_tier"] != tier:
            continue
        payload = (root / entry["file"]).read_bytes()
        if hashlib.sha1(payload).hexdigest() != entry["sha1"]:
            raise ValueError(f"sha1 mismatch for {entry['file']}; rebuild the corpus")
        ticks = json.loads(gzip.decompress(payload).decode("utf-8"))
        episodes.append({
            "match_id": entry["match_id"],
            "market_id": entry["market_id"],
            "family": entry["family"],
            "match_format": entry["match_format"],
            "competition": entry["competition"],
            "team_home": entry["team_home"],
            "team_away": entry["team_away"],
            "data_tier": entry["data_tier"],
            "start": entry["start"],
            "ticks": ticks,
        })
    return episodes


# ================================================================
# CLI
# ================================================================


def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * (len(ordered) - 1)))]


def print_summary(result: CorpusBuild) -> None:
    print(f"accepted markets: {len(result.accepted)}")
    print(f"rejected markets: {len(result.rejected)}")
    print(f"files written: {result.written}, unchanged: {result.unchanged}")
    print("matches per family and tier:")
    counts = Counter((e["family"], e["data_tier"]) for e in result.accepted)
    for (family, tier), n in sorted(counts.items()):
        print(f"  {family:<9} {tier:<9} {n}")
    print("in-play tick intervals (seconds between consecutive in-play ticks):")
    for tier, intervals in sorted(result.in_play_intervals.items()):
        if intervals:
            print(
                f"  {tier:<9} n={len(intervals)} median={statistics.median(intervals):.1f}"
                f" p90={_quantile(intervals, 0.9):.1f}"
            )
    print("rejections by reason:")
    for reason, n in sorted(Counter(r["reason"] for r in result.rejected).items()):
        print(f"  {reason:<22} {n}")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build the real-price corpus from Betfair stream files.")
    parser.add_argument("--src", type=Path, required=True, help="Directory of Betfair stream files")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Output directory (default data/real/betfair)")
    args = parser.parse_args(argv)
    if not args.src.is_dir():
        parser.error(f"--src is not a directory: {args.src}")

    result = build_corpus(args.src, args.out)
    logger.info(
        "real_corpus_built",
        accepted=len(result.accepted),
        rejected=len(result.rejected),
        written=result.written,
        unchanged=result.unchanged,
        out=str(args.out),
    )
    print_summary(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
