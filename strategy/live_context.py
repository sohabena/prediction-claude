"""Pre-match ratings for the v2 fair model, shared by the live loop and the real backtester (S43).

``load_ratings`` builds one ``CricketRatings`` over the t20 and odi Cricsheet families. It reuses a cached copy
when the source fingerprint (file count, newest mtime and data root) is unchanged, and rebuilds it otherwise.
``prematch_fields`` and ``attach_prematch`` return the ``pm_*`` tick fields from ``pre_match_live`` for one
match and date. Unknown teams, venues or XIs come back neutral, as in ``pre_match_live``.

Team names go through the caller's ``normalize_team`` (for example ``scripts.cricsheet_harmonizer
.canonicalize_team``). This module does not import the harmonizer.
"""
from __future__ import annotations

import os
import time
import weakref
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Optional

import joblib

from scraper.match_filter import MatchClassifier
from shared.logging import setup_logging
from strategy.fair_value import family_of
from strategy.ratings import CricketRatings

logger = setup_logging("live_context")

RATING_FAMILIES = ("t20", "odi")
DEFAULT_DATA_ROOT = Path("data")
DEFAULT_CACHE = Path("data/ratings/cricket_ratings.joblib")

# Per-ratings-object cache of ``prematch_fields`` results: one entry per (match, family, day, teams).
_FIELD_CACHES: "weakref.WeakKeyDictionary[Any, dict[tuple, dict[str, float]]]" = weakref.WeakKeyDictionary()


# ---------------------------------------------------------------- gender
def is_womens_name(name: Optional[str]) -> bool:
    """True for a women's side name (``... W``, ``... (W)``, ``... Women``), using the scraper's suffix rule."""
    normalized = str(name or "").lower().strip()
    return bool(normalized) and MatchClassifier._strip_womens_suffix(normalized) != normalized


def gender_of(team_home: Optional[str], team_away: Optional[str], competition: Optional[str] = "") -> str:
    """``"female"`` when either side or the competition is women's, else ``"male"`` (the ratings' gender keys)."""
    if "women" in str(competition or "").lower() or is_womens_name(team_home) or is_womens_name(team_away):
        return "female"
    return "male"


# ---------------------------------------------------------------- ratings: build, fingerprint, cache
def _source_dirs(data_root: Path) -> list[Path]:
    from scripts.train_fair_value import FAMILY_DIRS  # lazy: the training script imports the fair model

    dirs: list[Path] = []
    for family in RATING_FAMILIES:
        for sub in FAMILY_DIRS[family]:
            path = data_root / sub
            if path not in dirs:
                dirs.append(path)
    return dirs


def cricsheet_fingerprint(data_root: Path) -> dict[str, Any]:
    """Cheap change detector for the Cricsheet source dirs: JSON file count and newest mtime."""
    files = 0
    newest = 0.0
    for root in _source_dirs(Path(data_root)):
        if not root.exists():
            continue
        for path in root.rglob("*.json"):
            files += 1
            newest = max(newest, path.stat().st_mtime)
    return {"data_root": str(data_root), "files": files, "newest_mtime": newest}


def build_ratings(data_root: Path = DEFAULT_DATA_ROOT) -> CricketRatings:
    """Build ratings from every Cricsheet file of the t20 and odi families (streamed twice, see CricketRatings)."""
    from scripts.train_fair_value import FAMILY_DIRS, iter_cricsheet  # lazy, as above

    root = Path(data_root)
    stats: Counter = Counter()

    def source() -> Iterator[dict[str, Any]]:
        for family in RATING_FAMILIES:
            yield from iter_cricsheet([root / sub for sub in FAMILY_DIRS[family]], family, stats)

    ratings = CricketRatings(source)
    logger.info("ratings_built", matches=len(ratings.rows), rejected=dict(ratings.rejected), read=dict(stats))
    return ratings


def load_ratings(
    data_root: Path = DEFAULT_DATA_ROOT,
    cache: Optional[Path] = DEFAULT_CACHE,
) -> CricketRatings:
    """Ratings for t20 and odi, from ``cache`` when its fingerprint still matches the Cricsheet dirs.

    A missing, stale or unreadable cache triggers a rebuild, and the rebuilt copy is written back to ``cache``.
    Blocking (reads every Cricsheet file on a rebuild): call it through ``asyncio.to_thread`` in async code.
    """
    root = Path(data_root)
    fingerprint = cricsheet_fingerprint(root)
    if cache is not None and Path(cache).exists():
        try:
            payload = joblib.load(cache)
            if payload.get("fingerprint") == fingerprint:
                logger.info("ratings_cache_hit", path=str(cache), matches=len(payload["ratings"].rows))
                return payload["ratings"]
            logger.info("ratings_cache_stale", path=str(cache))
        except Exception as exc:  # corrupt or incompatible cache: rebuild below
            logger.warning("ratings_cache_unreadable", path=str(cache), error=type(exc).__name__)

    started = time.monotonic()
    ratings = build_ratings(root)
    logger.info("ratings_build_done", seconds=round(time.monotonic() - started, 1))
    if cache is not None:
        try:
            _write_cache(Path(cache), {"fingerprint": fingerprint, "ratings": ratings})
        except OSError as exc:
            logger.warning("ratings_cache_write_failed", path=str(cache), error=type(exc).__name__)
    return ratings


def _write_cache(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    joblib.dump(dict(payload), tmp)
    os.replace(tmp, path)


# ---------------------------------------------------------------- pre-match fields
def _day(value: Any) -> str:
    """ISO date (``YYYY-MM-DD``) of a tick time given as an ISO string or epoch seconds."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(float(value), tz=timezone.utc).date().isoformat()
    return str(value or "")[:10]


def prematch_fields(
    tick: Mapping[str, Any],
    ratings: Any,
    *,
    normalize_team: Optional[Callable[[str], Optional[str]]] = None,
    as_of: Optional[str] = None,
) -> dict[str, float]:
    """``pm_*`` fields for the match of ``tick``, using only information dated before ``as_of``.

    ``as_of`` defaults to the tick's own date (live: the decision day). Returns ``{}`` when there is no ratings
    object, the teams are missing, or the family is not rated (multi-day). Results are cached per
    (match, family, day, teams) on the ratings object.
    """
    home, away = tick.get("team_home"), tick.get("team_away")
    family = tick.get("family") or family_of(tick.get("match_format"))
    if ratings is None or not home or not away or family not in RATING_FAMILIES:
        return {}
    day = as_of or _day(tick.get("time"))
    if not day:
        return {}
    gender = gender_of(home, away, tick.get("competition", ""))
    key = (str(tick.get("match_id", "")), family, day, str(home), str(away))
    try:
        cache = _FIELD_CACHES.setdefault(ratings, {})
    except TypeError:  # not weak-referenceable: no cache, still correct
        cache = {}
    if key not in cache:
        try:
            cache[key] = ratings.pre_match_live(
                str(home), str(away), family, gender, day,
                venue=tick.get("venue"),
                toss_winner=tick.get("toss_winner"),
                toss_decision=tick.get("toss_decision"),
                normalize_team=normalize_team,
            )
        except (ValueError, KeyError) as exc:
            logger.warning("prematch_failed", match_id=tick.get("match_id"), error=type(exc).__name__)
            return {}
    return dict(cache[key])


def attach_prematch(
    tick: Mapping[str, Any],
    ratings: Any,
    *,
    normalize_team: Optional[Callable[[str], Optional[str]]] = None,
    as_of: Optional[str] = None,
) -> dict[str, Any]:
    """Copy of ``tick`` with the ``pm_*`` fields added (the input is not modified). Unchanged when no fields apply."""
    fields = prematch_fields(tick, ratings, normalize_team=normalize_team, as_of=as_of)
    return {**tick, **fields} if fields else dict(tick)
