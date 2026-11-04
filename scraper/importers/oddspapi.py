"""S100: OddsPapi historical match-winner odds, read through the official API and stored as S98 records.

Endpoints used (documented at https://oddspapi.io/en/docs/, read 2026-11-04):

- ``GET /v4/sports``: ``sportId``, ``slug``, ``sportName``. Cricket is found by its name; its id is not documented.
- ``GET /v4/fixtures``: ``sportId``, ``from``, ``to`` (ISO 8601, UTC), ``statusId`` (2 = finished), ``language``.
  A plain JSON array of ``fixtureId``, ``tournamentName``, ``startTime``, ``participant1Name``, ``participant2Name``.
- ``GET /v4/historical-odds``: ``fixtureId`` and ``bookmakers`` (at most 3 slugs). Prices sit under
  ``bookmakers.<slug>.markets.<marketId>.outcomes.<outcomeId>.players."0"`` as lists of
  ``{createdAt, price, limit, active, exchangeMeta}``. Cooldown 5000 ms per endpoint; a 304 counts too.

Every request carries the key as the query parameter ``apiKey``. The key is read only from ``ODDSPAPI_API_KEY`` (by
the script) and is never printed, logged or written to a file name. Errors raised here never contain the key or a URL.

Golden Rulebook: Betfair is never requested and never stored (rule 3). Historical odds are bought or published, never
scraped (section 8). Nothing here touches the database. Tests use ``httpx.MockTransport``.
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Optional, Sequence

import httpx

from training import historical_odds as ho

BASE_URL = "https://api.oddspapi.io"
SOURCE = "oddspapi"
COOLDOWN_S = 5.0  # the operator's 5 s, enforced on every call; the docs give 5000 ms per endpoint for historical-odds
MAX_RETRIES = 3  # retries on 429 and 5xx: at most four attempts in all
BACKOFF_BASE_S = 5.0  # unverified: the docs advise exponential backoff for 500 and 503 but give no numbers
REQUEST_TIMEOUT_S = 30.0  # unverified: no timeout is documented
MAX_BOOKMAKERS_PER_CALL = 3  # documented
FINISHED_STATUS_ID = 2  # documented: 0 not started, 1 live, 2 finished, 3 cancelled
CRICKET_SPORT_NAME = "Cricket"  # documented as sportName; the cricket sportId is looked up at run time
PLAYER_KEY = "0"  # documented in the historical-odds example: match prices sit under players "0"
# Verified 2026-11-04 (/v4/bookmakers, 370 slugs): cricket history came from 1xbet (low-profile leagues included) and
# bet365; pinnacle had none on the fixtures tried.
DEFAULT_BOOKMAKERS = ("1xbet", "bet365", "pinnacle")
# Verified 2026-11-04 (/v4/markets?sportId=27): cricket market 271 is "Winner (incl. super over)", a moneyline with
# outcomes 271 = "1" (participant 1) and 272 = "2". 273 is the 1X2 market (274 = X, the tie); 101 is football.
MATCH_WINNER_MARKET_ID = "271"
DEFAULT_CACHE_ROOT = ho.DEFAULT_ROOT

_T20 = re.compile(r"\bT20|\bTwenty20", re.IGNORECASE)
_ODI = re.compile(r"\bODIs?\b", re.IGNORECASE)
_TEST = re.compile(r"\bTests?\b", re.IGNORECASE)
_WOMEN = re.compile(r"\bwomen", re.IGNORECASE)


class OddsPapiError(RuntimeError):
    """A request or response failed. Messages never contain the key or a URL."""


class QuotaExhausted(OddsPapiError):
    """REQUEST_LIMIT_EXCEEDED: the monthly quota is used up, so retrying does not help (documented)."""


@dataclass(frozen=True)
class Fixture:
    """One finished cricket fixture, as the match-winner records need it."""

    fixture_id: str
    tournament_name: str
    format: str
    gender: str
    start_time_utc: datetime
    home: str
    away: str


def format_from_tournament(name: Any) -> str:
    """t20, odi, test or other, from the tournament name. Unknown names are ``other``."""
    text = str(name or "")
    if _T20.search(text):
        return "t20"
    if _ODI.search(text):
        return "odi"
    if _TEST.search(text):
        return "test"
    return "other"


def gender_from_tournament(name: Any) -> str:
    """female when the tournament name says Women, otherwise male (the contract has no other gender for cricket)."""
    return "female" if _WOMEN.search(str(name or "")) else "male"


def parse_utc(value: Any) -> Optional[datetime]:
    """An ISO 8601 timestamp as an aware UTC datetime; None when missing or unreadable.

    A value without an offset is read as UTC (unverified: the docs say timestamps are UTC, not how they are written).
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _price(value: Any) -> Optional[float]:
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return price if math.isfinite(price) and price > 1.0 else None


def _z(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text)[:200]


def days_between(first: date, last: date) -> Iterator[date]:
    """Each day from ``first`` to ``last``, both included."""
    day = first
    while day <= last:
        yield day
        day += timedelta(days=1)


def fixture_from_item(item: Mapping[str, Any]) -> Optional[Fixture]:
    """A ``/v4/fixtures`` item as a Fixture; None when the id, start time or either participant is missing."""
    fixture_id = _text(item.get("fixtureId"))
    start = parse_utc(item.get("startTime"))
    # unverified: participant1 is taken as the home side (the docs list it first but do not say it is home)
    home = ho.normalise_team(_text(item.get("participant1Name")))
    away = ho.normalise_team(_text(item.get("participant2Name")))
    if not fixture_id or start is None or not home or not away or home.casefold() == away.casefold():
        return None
    name = _text(item.get("tournamentName"))
    return Fixture(
        fixture_id=fixture_id,
        tournament_name=name,
        format=format_from_tournament(name),
        gender=gender_from_tournament(name),
        start_time_utc=start,
        home=home,
        away=away,
    )


def requested_bookmakers(names: Iterable[str]) -> tuple[list[str], int]:
    """The slugs to request, lower-cased and de-duplicated in order, with Betfair removed.

    Returns ``(slugs, removed)``; ``removed`` counts the Betfair entries that were dropped before any request.
    """
    kept: list[str] = []
    removed = 0
    for name in names:
        slug = str(name or "").strip().lower()
        if not slug:
            continue
        if ho.is_betfair(slug):
            removed += 1
            continue
        if slug not in kept:
            kept.append(slug)
    return kept, removed


def bookmaker_groups(slugs: Sequence[str]) -> list[tuple[str, ...]]:
    """The slugs split into calls of at most three (documented limit)."""
    return [tuple(slugs[i:i + MAX_BOOKMAKERS_PER_CALL]) for i in range(0, len(slugs), MAX_BOOKMAKERS_PER_CALL)]


def historical_key(fixture_id: str, group: Sequence[str]) -> str:
    """The cache key of one historical-odds call: the fixture and its bookmaker group."""
    return f"{fixture_id}__{'-'.join(group)}"


def _error_body(response: httpx.Response) -> Mapping[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, Mapping) else {}


def _error_code(response: httpx.Response) -> str:
    return _text(_error_body(response).get("code"))


def _retry_after_s(response: httpx.Response) -> Optional[float]:
    """Seconds from ``retryMs`` (documented on RATE_LIMITED responses), or None."""
    try:
        return max(float(_error_body(response).get("retryMs")) / 1000.0, 0.0)
    except (TypeError, ValueError):
        return None


class OddsPapiClient:
    """Cooldown-enforcing, cache-first client for the OddsPapi REST API.

    ``offline=True`` never requests: a cache miss returns None and is counted, which is how the dry run works.
    ``stats`` counts requests, retries, cache hits and misses. It never holds the key.
    """

    def __init__(
        self,
        api_key: str,
        *,
        transport: Optional[httpx.BaseTransport] = None,
        clock: Optional[Callable[[], float]] = None,
        sleep: Optional[Callable[[float], None]] = None,
        root: Path = DEFAULT_CACHE_ROOT,
        offline: bool = False,
    ) -> None:
        if not api_key:
            raise ValueError("an OddsPapi key is required")
        self._api_key = api_key
        self._http = httpx.Client(base_url=BASE_URL, transport=transport, timeout=REQUEST_TIMEOUT_S)
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self.root = Path(root)
        self.offline = offline
        self.stats: Counter = Counter()
        self._last_call: Optional[float] = None
        # httpx logs each request URL at INFO and the URL carries the key: keep those records out of the logs.
        for name in ("httpx", "httpcore"):
            logging.getLogger(name).setLevel(logging.WARNING)

    def close(self) -> None:
        self._http.close()

    def cache_path(self, endpoint: str, key: str) -> Path:
        """``<root>/raw/oddspapi/<endpoint>/<key>.json``; the key is sanitised and never contains the API key."""
        return self.root / "raw" / SOURCE / endpoint / f"{_safe_name(key)}.json"

    def is_cached(self, endpoint: str, key: str) -> bool:
        return self.cache_path(endpoint, key).is_file()

    def _cooldown(self) -> None:
        if self._last_call is None:
            return
        wait = self._last_call + COOLDOWN_S - self._clock()
        if wait > 0:
            self._sleep(wait)

    def _get(self, path: str, params: Mapping[str, Any]) -> bytes:
        """The raw body of a 200 response. Retries 429 and 5xx at most MAX_RETRIES times; 4xx is not retried."""
        query = {**params, "apiKey": self._api_key}
        for attempt in range(MAX_RETRIES + 1):
            self._cooldown()
            self._last_call = self._clock()
            self.stats["requests"] += 1
            wait: Optional[float] = None
            try:
                response = self._http.get(path, params=query)
            except httpx.HTTPError as exc:
                problem = f"network error ({type(exc).__name__})"
            else:
                if response.status_code == 200:
                    return response.content
                code = _error_code(response)
                if code == "REQUEST_LIMIT_EXCEEDED":
                    raise QuotaExhausted(f"{path}: the monthly request quota is used up (HTTP {response.status_code})")
                if response.status_code != 429 and response.status_code < 500:
                    raise OddsPapiError(f"{path}: HTTP {response.status_code} {code or 'error'}")
                problem = f"HTTP {response.status_code} {code}".strip()
                wait = _retry_after_s(response)
            if attempt == MAX_RETRIES:
                raise OddsPapiError(f"{path}: {problem}; gave up after {MAX_RETRIES} retries")
            self.stats["retries"] += 1
            self._sleep(wait if wait is not None else BACKOFF_BASE_S * 2 ** attempt)
        raise OddsPapiError(f"{path}: no answer")  # not reached

    def fetch_json(self, endpoint: str, key: str, path: str, params: Mapping[str, Any]) -> Any:
        """The parsed JSON for one call. A cached response is returned without a request; a new one is saved raw
        before it is parsed. Returns None when offline and not cached."""
        target = self.cache_path(endpoint, key)
        if target.is_file():
            try:
                payload = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                target.unlink(missing_ok=True)  # a damaged cache file is fetched again
            else:
                self.stats["cache_hits"] += 1
                return payload
        if self.offline:
            self.stats["not_cached"] += 1
            return None
        body = self._get(path, params)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.parent / (target.name + ".tmp")
        temp.write_bytes(body)
        temp.replace(target)
        try:
            return json.loads(body.decode("utf-8"))
        except ValueError:
            target.unlink(missing_ok=True)
            raise OddsPapiError(f"{endpoint}: the response is not JSON") from None

    def historical_odds(self, fixture_id: str, group: Sequence[str]) -> Optional[Mapping[str, Any]]:
        """Price history of one fixture for up to three bookmakers (documented endpoint)."""
        return self.fetch_json(
            "historical-odds",
            historical_key(fixture_id, group),
            "/v4/historical-odds",
            {"fixtureId": fixture_id, "bookmakers": ",".join(group)},
        )


def cricket_sport_id(client: OddsPapiClient) -> Optional[int]:
    """The sportId whose sportName is Cricket, from the cached ``/v4/sports``; None when offline and not cached."""
    sports = client.fetch_json("sports", "all", "/v4/sports", {"language": "en"})
    if sports is None:
        return None
    for sport in sports:
        if _text(sport.get("sportName")).casefold() == CRICKET_SPORT_NAME.casefold():
            return int(sport["sportId"])
    raise OddsPapiError("no sport named Cricket in /v4/sports")


def is_srl(tournament_name: Any) -> bool:
    """True for a simulated (SRL) tournament, e.g. "Big Bash League SRL": never real cricket, never imported."""
    return "srl" in _text(tournament_name).casefold().split()


def discover_fixtures(
    client: OddsPapiClient,
    date_from: date,
    date_to: date,
    *,
    sport_id: Optional[int] = None,
    window_days: int = 1,
    include_srl: bool = False,
) -> list[Fixture]:
    """Finished cricket fixtures on UTC days ``date_from`` to ``date_to``, each once, sorted by start time.

    One ``/v4/fixtures`` call per window of ``window_days`` UTC days (the docs allow under 10 days with sportId; each
    call is billable, so the importer uses 9). Simulated (SRL) tournaments are skipped unless ``include_srl``
    (2026-11-04: about 60% of cricket fixtures are SRL). Offline, an uncached window is counted in
    ``client.stats["uncached_windows"]`` and skipped.
    """
    if not 1 <= window_days <= 9:
        raise ValueError("window_days must be 1 to 9 (the API allows under 10 days per call)")
    days = list(days_between(date_from, date_to))
    windows = [days[i:i + window_days] for i in range(0, len(days), window_days)]
    sport = sport_id if sport_id is not None else cricket_sport_id(client)
    if sport is None:
        client.stats["uncached_windows"] += 1 + len(windows)
        return []
    found: dict[str, Fixture] = {}
    for window in windows:
        first, last = window[0], window[-1]
        start = datetime(first.year, first.month, first.day, tzinfo=timezone.utc)
        end = datetime(last.year, last.month, last.day, tzinfo=timezone.utc) + timedelta(days=1)
        params = {
            "sportId": sport,
            "from": _z(start),
            "to": _z(end),
            "statusId": FINISHED_STATUS_ID,
            "language": "en",
        }
        key = f"sport{sport}_{first.isoformat()}" + ("" if len(window) == 1 else f"_{last.isoformat()}")
        items = client.fetch_json("fixtures", key, "/v4/fixtures", params)
        if items is None:
            client.stats["uncached_windows"] += 1
            continue
        for item in items:
            if _text(item.get("statusId")) != str(FINISHED_STATUS_ID):
                client.stats["not_finished"] += 1
                continue
            if not include_srl and is_srl(item.get("tournamentName")):
                client.stats["srl_skipped"] += 1
                continue
            fixture = fixture_from_item(item)
            if fixture is None:
                client.stats["unusable_fixture"] += 1
                continue
            found.setdefault(fixture.fixture_id, fixture)
    return sorted(found.values(), key=lambda fixture: fixture.start_time_utc)


def outcome_roles(outcome_ids: Sequence[str]) -> Optional[dict[str, str]]:
    """home, draw and away roles for a match-winner market's outcome ids, by numeric order.

    Three outcomes read as "1", "X", "2" (home, draw, away); two read as home, away. Any other shape gives None.
    unverified: the 1x2 example on the markets page is 101, 102, 103 = "1", "X", "2". The order of a two-way market
    and the ids of other markets are not confirmed by the docs.
    """
    try:
        ordered = sorted(outcome_ids, key=int)
    except ValueError:
        return None
    if len(ordered) == 3:
        return dict(zip(ordered, ("home", "draw", "away")))
    if len(ordered) == 2:
        return dict(zip(ordered, ("home", "away")))
    return None


def _prices_before_start(
    outcomes: Mapping[str, Any],
    outcome_id: str,
    start: datetime,
    counts: Counter,
) -> list[tuple[datetime, float]]:
    """(time, price) for one outcome in time order, at or before the start. Later prices are ignored.

    ``active`` is not used: the docs filter describes it as "currently active", which is false for superseded history.
    """
    entries = (((outcomes.get(outcome_id) or {}).get("players") or {}).get(PLAYER_KEY)) or []
    points: list[tuple[datetime, float]] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            counts["bad_entry"] += 1
            continue
        when = parse_utc(entry.get("createdAt"))
        price = _price(entry.get("price"))
        if when is None or price is None:
            counts["bad_entry"] += 1
            continue
        if when > start:
            continue
        points.append((when, price))
    points.sort(key=lambda point: point[0])
    return points


def records_from_history(
    fixture: Fixture,
    payload: Optional[Mapping[str, Any]],
    market_id: Any = MATCH_WINNER_MARKET_ID,
    *,
    stats: Optional[Counter] = None,
) -> list[ho.OddsRecord]:
    """Open and close match-winner records for each bookmaker in one historical-odds payload.

    ``open`` takes the first price per outcome, ``close`` the last price at or before the start; prices after the
    start are ignored. A bookmaker whose book is incomplete (a missing outcome before the start) gives no records.
    Betfair entries are dropped and counted in ``stats["dropped_betfair"]``, never turned into records.
    """
    counts = stats if stats is not None else Counter()
    records: list[ho.OddsRecord] = []
    for slug, book in (((payload or {}).get("bookmakers")) or {}).items():
        if ho.is_betfair(slug):
            counts["dropped_betfair"] += 1
            continue
        market = (((book or {}).get("markets")) or {}).get(str(market_id))
        if not market:
            counts["no_market"] += 1
            continue
        outcomes = market.get("outcomes") or {}
        roles = outcome_roles(list(outcomes))
        if roles is None:
            counts["unmapped_market"] += 1
            continue
        series = {
            role: _prices_before_start(outcomes, outcome_id, fixture.start_time_utc, counts)
            for outcome_id, role in roles.items()
        }
        if any(not points for points in series.values()):
            counts["incomplete"] += 1
            continue
        for kind, pick in (("open", lambda points: points[0]), ("close", lambda points: points[-1])):
            picked = {role: pick(points) for role, points in series.items()}
            prices = {role: price for role, (_, price) in picked.items()}
            records.append(
                ho.OddsRecord(
                    source=SOURCE,
                    source_event_id=fixture.fixture_id,
                    competition=fixture.tournament_name,
                    format=fixture.format,
                    gender=fixture.gender,
                    start_time_utc=fixture.start_time_utc,
                    home=fixture.home,
                    away=fixture.away,
                    bookmaker=str(slug),
                    kind=kind,
                    price_home=prices["home"],
                    price_away=prices["away"],
                    snapshot_time_utc=max(when for when, _ in picked.values()),
                    price_draw=prices.get("draw"),
                    winner=None,
                )
            )
    return records


def import_fixtures(
    client: OddsPapiClient,
    fixtures: Sequence[Fixture],
    bookmakers: Sequence[str],
    market_id: Any = MATCH_WINNER_MARKET_ID,
) -> tuple[list[ho.OddsRecord], Counter]:
    """Records for every fixture and bookmaker group. Offline, uncached calls are counted, not made."""
    stats: Counter = Counter()
    records: list[ho.OddsRecord] = []
    for fixture in fixtures:
        for group in bookmaker_groups(bookmakers):
            payload = client.historical_odds(fixture.fixture_id, group)
            if payload is None:
                stats["uncached_calls"] += 1
                continue
            records.extend(records_from_history(fixture, payload, market_id, stats=stats))
    return records, stats
