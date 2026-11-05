"""S101: The Odds API historical cricket h2h odds, with a credit guard.

The Odds API is a paid service (historical endpoints need a paid plan). This client never spends a credit unless the
caller set a credit budget, and it checks every paid call against that budget before it is sent. Cached responses are
free and are never requested twice.

Endpoints (The Odds API v4 documentation)::

    GET /v4/historical/sports/{sport}/events?apiKey=&date=<ISO>            cost 1 (0 when no events are found)
    GET /v4/historical/sports/{sport}/odds?apiKey=&regions=&markets=&oddsFormat=decimal&date=<ISO>
                                                                            cost 10 per region per market

The key comes only from the caller (the script reads the environment variable ``THE_ODDS_API_KEY``). It is never
printed, logged or written to a file: request URLs are logged by httpx, so the key is redacted from every httpx log
record, and error messages are scrubbed before they are raised.

Records use the S98 contract (:mod:`training.historical_odds`). Betfair bookmakers are dropped and counted, never
stored. Snapshots are taken five minutes before each start, so no record is in play.

Golden Rulebook: historical odds are bought, never scraped (section 2 and 8); Betfair data is never used (rule 3).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, MutableMapping, Optional, Sequence, Union

import httpx

from training.historical_odds import KINDS, OddsRecord, is_betfair, normalise_team

logger = logging.getLogger("scraper.importers.the_odds_api")

BASE_URL = "https://api.the-odds-api.com/v4"
SOURCE = "the_odds_api"
DEFAULT_CACHE_ROOT = Path("data/historical_odds/raw/the_odds_api")
SNAPSHOT_STEP = timedelta(minutes=5)
CLOSE_LEAD = timedelta(minutes=5)
EVENT_COST = 1
ODDS_COST_PER_REGION_MARKET = 10
MARKET = "h2h"
ODDS_FORMAT = "decimal"
SPORT_KEY_PATTERN = re.compile(r"^[a-z0-9_]+$")

# Sport key -> (format, gender). Unknown keys become ("other", "male"): the cricket keys listed here are the men's
# competitions; the gender of an unlisted key is a guess, so the verifier should be read with that in mind.
SPORT_FORMATS: dict[str, tuple[str, str]] = {
    "cricket_ipl": ("t20", "male"),
    "cricket_big_bash": ("t20", "male"),
    "cricket_psl": ("t20", "male"),
    "cricket_caribbean_premier_league": ("t20", "male"),
    "cricket_t20_blast": ("t20", "male"),
    "cricket_international_t20": ("t20", "male"),
    "cricket_odi": ("odi", "male"),
    "cricket_test_match": ("test", "male"),
}

Regions = Union[str, Sequence[str]]


class BudgetExceeded(RuntimeError):
    """A paid call would take the run past its credit budget. Nothing was sent."""


class OddsApiError(RuntimeError):
    """The API answered with an error, or the request failed. The message never contains the key."""


# Every key this process has used; any log line or error text that contains one is redacted.
_SECRETS: set[str] = set()


def _scrub(text: Any) -> str:
    out = str(text)
    for secret in _SECRETS:
        out = out.replace(secret, "***")
    return out


class _RedactSecrets(logging.Filter):
    """Removes API keys from httpx's request log lines, which carry the key in the query string."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - a broken log call must not break the import
            message = str(record.msg)
        record.msg = _scrub(message)
        record.args = ()
        return True


_REDACTOR = _RedactSecrets()


def _install_redaction() -> None:
    for name in ("httpx", "httpcore"):
        logger_obj = logging.getLogger(name)
        if _REDACTOR not in logger_obj.filters:
            logger_obj.addFilter(_REDACTOR)


def sport_format(sport_key: Any) -> tuple[str, str]:
    """(format, gender) for a sport key; unknown keys are ``other``."""
    return SPORT_FORMATS.get(str(sport_key or ""), ("other", "male"))


def _names(value: Regions) -> list[str]:
    """Region or market names as a sorted, de-duplicated list; accepts ``"uk,eu"`` or a sequence."""
    items = value.split(",") if isinstance(value, str) else list(value)
    return sorted({str(item).strip() for item in items if str(item).strip()})


def _aware_utc(when: datetime) -> datetime:
    if when.tzinfo is None:
        raise ValueError("times must be timezone-aware")
    return when.astimezone(timezone.utc)


def _iso(when: datetime) -> str:
    return _aware_utc(when).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp(when: datetime) -> str:
    return _aware_utc(when).strftime("%Y%m%dT%H%M%SZ")


def _parse_time(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _floor_to_step(when: datetime) -> datetime:
    when = _aware_utc(when)
    return when.replace(minute=when.minute - when.minute % 5, second=0, microsecond=0)


def _price(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_header(headers: Mapping[str, Any], name: str) -> Optional[int]:
    raw = headers.get(name)
    try:
        return int(float(raw)) if raw is not None else None
    except (TypeError, ValueError):
        return None


def estimate_cost(n_event_calls: int, n_odds_calls: int, regions: Regions, markets: Regions) -> int:
    """Credits for a plan: 1 per events call, 10 per region per market per odds call."""
    per_odds_call = ODDS_COST_PER_REGION_MARKET * len(_names(regions)) * len(_names(markets))
    return int(n_event_calls) * EVENT_COST + int(n_odds_calls) * per_odds_call


def plan_closing_snapshots(events: Iterable[Mapping[str, Any]]) -> list[datetime]:
    """Snapshot times that cover every event's closing price, sorted and distinct.

    Each distinct start gets one snapshot five minutes before it, rounded down to the API's five-minute grid, so
    starts that share a grid point share one odds call. Every snapshot is before the start it serves.
    """
    snapshots: set[datetime] = set()
    for event in events:
        start = _parse_time(event.get("commence_time"))
        if start is not None:
            snapshots.add(_floor_to_step(start - CLOSE_LEAD))
    return sorted(snapshots)


def events_in_window(payloads: Iterable[Mapping[str, Any]], first_day: date, last_day: date) -> list[dict[str, Any]]:
    """Distinct events (by id) whose start date (UTC) falls within ``first_day``..``last_day``."""
    seen: dict[str, dict[str, Any]] = {}
    for payload in payloads:
        for event in payload.get("data") or []:
            start = _parse_time(event.get("commence_time"))
            if start is None or not first_day <= start.astimezone(timezone.utc).date() <= last_day:
                continue
            seen.setdefault(str(event.get("id")), dict(event))
    return list(seen.values())


def _tally(counts: Optional[MutableMapping[str, int]], key: str) -> None:
    if counts is not None:
        counts[key] = counts.get(key, 0) + 1


def records_from_snapshot(
    payload: Mapping[str, Any],
    kind: str = "close",
    *,
    aliases: Optional[Mapping[str, str]] = None,
    counts: Optional[MutableMapping[str, int]] = None,
) -> list[OddsRecord]:
    """One record per event and bookmaker from one odds snapshot.

    Only events starting after the snapshot are kept (nothing in play). Outcomes are matched to the home and away
    team names; ``Draw`` becomes ``price_draw``. Betfair bookmakers are dropped. ``counts`` (optional) receives
    tallies such as ``betfair_dropped``, ``in_play_skipped``, ``unmatched_outcome`` and ``records``.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}")
    snapshot_time = _parse_time(payload.get("timestamp"))
    if snapshot_time is None:
        raise ValueError("the snapshot has no timestamp")
    records: list[OddsRecord] = []
    for event in payload.get("data") or []:
        _tally(counts, "events_seen")
        start = _parse_time(event.get("commence_time"))
        home = normalise_team(event.get("home_team"), aliases)
        away = normalise_team(event.get("away_team"), aliases)
        if start is None or not home or not away:
            _tally(counts, "no_teams_or_start")
            continue
        if start <= snapshot_time:
            _tally(counts, "in_play_skipped")
            continue
        event_format, gender = sport_format(event.get("sport_key"))
        competition = str(event.get("sport_title") or event.get("sport_key") or "")
        event_id = str(event.get("id") or "")
        for bookmaker in event.get("bookmakers") or []:
            key = str(bookmaker.get("key") or "")
            title = str(bookmaker.get("title") or "")
            if is_betfair(key) or is_betfair(title):
                _tally(counts, "betfair_dropped")
                continue
            market = next((m for m in bookmaker.get("markets") or [] if m.get("key") == MARKET), None)
            if market is None:
                _tally(counts, "no_h2h")
                continue
            prices: dict[str, float] = {}
            problem: Optional[str] = None
            for outcome in market.get("outcomes") or []:
                name = normalise_team(outcome.get("name"), aliases).casefold()
                price = _price(outcome.get("price"))
                if name == "draw":
                    slot = "draw"
                elif name == home.casefold():
                    slot = "home"
                elif name == away.casefold():
                    slot = "away"
                else:
                    slot = None
                if slot is None:
                    problem = "unmatched_outcome"
                    break
                if price is None:
                    problem = "bad_price"
                    break
                prices[slot] = price
            if problem is None and ("home" not in prices or "away" not in prices):
                problem = "incomplete_market"
            if problem is not None:
                _tally(counts, problem)
                continue
            records.append(
                OddsRecord(
                    source=SOURCE,
                    source_event_id=event_id,
                    competition=competition,
                    format=event_format,
                    gender=gender,
                    start_time_utc=start,
                    home=home,
                    away=away,
                    bookmaker=key,
                    kind=kind,
                    price_home=prices["home"],
                    price_away=prices["away"],
                    snapshot_time_utc=snapshot_time,
                    price_draw=prices.get("draw"),
                    winner=None,
                )
            )
            _tally(counts, "records")
    return records


def latest_per_match_book(records: Iterable[OddsRecord]) -> list[OddsRecord]:
    """Keeps one record per (event, bookmaker, kind): the one from the latest snapshot, i.e. the closest to the start."""
    floor = datetime.min.replace(tzinfo=timezone.utc)
    best: dict[tuple[str, str, str], OddsRecord] = {}
    for record in records:
        key = (record.source_event_id, record.bookmaker, record.kind)
        current = best.get(key)
        if current is None or (record.snapshot_time_utc or floor) > (current.snapshot_time_utc or floor):
            best[key] = record
    return sorted(best.values(), key=lambda r: (r.start_time_utc, r.source_event_id, r.bookmaker))


class TheOddsApiClient:
    """Read-only client for the historical endpoints, with a credit budget and a response cache.

    ``max_credits`` is the most credits this client may spend in its lifetime (0 = spend nothing). Cached responses
    cost nothing and are returned without a request. ``transport`` is an optional httpx transport (tests pass
    ``httpx.MockTransport``).
    """

    def __init__(
        self,
        api_key: str,
        *,
        transport: Optional[httpx.BaseTransport] = None,
        cache_root: Union[str, Path] = DEFAULT_CACHE_ROOT,
        max_credits: int = 0,
        timeout: float = 30.0,
    ) -> None:
        key = str(api_key or "").strip()
        if not key:
            raise ValueError("an API key is required")
        self._key = key
        _SECRETS.add(key)
        _install_redaction()
        self._transport = transport
        self.cache_root = Path(cache_root)
        self.max_credits = int(max_credits)
        self.timeout = float(timeout)
        self.spent = 0
        self.requests_made = 0
        self.last_usage: dict[str, Optional[int]] = {}

    def __repr__(self) -> str:  # never show the key
        return f"TheOddsApiClient(cache_root={str(self.cache_root)!r}, max_credits={self.max_credits})"

    # Cache

    def cache_file(self, sport: str, name: str) -> Path:
        if not SPORT_KEY_PATTERN.match(sport):
            raise ValueError(f"invalid sport key {sport!r}")
        return self.cache_root / sport / f"{name}.json"

    @staticmethod
    def _events_name(when: datetime) -> str:
        return f"events_{_stamp(when)}"

    @staticmethod
    def _odds_name(when: datetime, regions: Regions, markets: Regions) -> str:
        return f"odds_{_stamp(when)}_{'-'.join(_names(regions))}_{'-'.join(_names(markets))}"

    def _read_cache(self, sport: str, name: str) -> Optional[dict[str, Any]]:
        path = self.cache_file(sport, name)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def cached_events(self, sport: str, when: datetime) -> Optional[dict[str, Any]]:
        return self._read_cache(sport, self._events_name(when))

    def cached_odds(
        self, sport: str, when: datetime, regions: Regions = "uk", markets: Regions = MARKET
    ) -> Optional[dict[str, Any]]:
        return self._read_cache(sport, self._odds_name(when, regions, markets))

    # Paid calls

    def events(self, sport: str, when: datetime) -> dict[str, Any]:
        """Event list at the snapshot at or before ``when``. Costs 1 credit unless cached."""
        name = self._events_name(when)
        return self._get(sport, name, f"/historical/sports/{sport}/events", {"date": _iso(when)}, EVENT_COST)

    def odds(
        self, sport: str, when: datetime, regions: Regions = "uk", markets: Regions = MARKET
    ) -> dict[str, Any]:
        """All events' h2h odds at the snapshot at or before ``when``. Costs 10 per region per market unless cached."""
        region_list, market_list = _names(regions), _names(markets)
        if not region_list or not market_list:
            raise ValueError("at least one region and one market are needed")
        cost = estimate_cost(0, 1, region_list, market_list)
        params = {
            "regions": ",".join(region_list),
            "markets": ",".join(market_list),
            "oddsFormat": ODDS_FORMAT,
            "date": _iso(when),
        }
        return self._get(sport, self._odds_name(when, region_list, market_list),
                         f"/historical/sports/{sport}/odds", params, cost)

    def _get(self, sport: str, name: str, path: str, params: Mapping[str, str], expected: int) -> dict[str, Any]:
        cached = self._read_cache(sport, name)
        if cached is not None:
            return cached
        if self.spent + expected > self.max_credits:
            raise BudgetExceeded(
                f"{name} needs {expected} credits; {self.spent} spent of a {self.max_credits} budget: not sent"
            )
        headers, payload = self._send(path, params)
        last = _int_header(headers, "x-requests-last")
        self.spent += last if last is not None else expected
        self.last_usage = {
            "last": last,
            "used": _int_header(headers, "x-requests-used"),
            "remaining": _int_header(headers, "x-requests-remaining"),
        }
        logger.info(
            "the_odds_api %s: credits last=%s used=%s remaining=%s",
            name, self.last_usage["last"], self.last_usage["used"], self.last_usage["remaining"],
        )
        path_out = self.cache_file(sport, name)
        path_out.parent.mkdir(parents=True, exist_ok=True)
        path_out.write_text(json.dumps(payload), encoding="utf-8")
        return payload

    def _send(self, path: str, params: Mapping[str, str]) -> tuple[httpx.Headers, dict[str, Any]]:
        options: dict[str, Any] = {"timeout": self.timeout}
        if self._transport is not None:
            options["transport"] = self._transport
        query = {"apiKey": self._key, **params}
        try:
            with httpx.Client(**options) as http:
                self.requests_made += 1
                response = http.get(BASE_URL + path, params=query)
        except httpx.HTTPError as exc:
            # The exception text can carry the request URL, which holds the key: report the type only.
            raise OddsApiError(f"request to {path} failed ({type(exc).__name__})") from None
        if response.status_code != 200:
            body = _scrub(response.text[:200])
            raise OddsApiError(f"HTTP {response.status_code} from {path}: {body}")
        try:
            payload = response.json()
        except ValueError:
            raise OddsApiError(f"response from {path} is not JSON") from None
        if not isinstance(payload, dict):
            raise OddsApiError(f"response from {path} has an unexpected shape")
        return response.headers, payload
