"""Read-only market-page worker for LotusBook match pages: Winner (match odds), Bookmaker, session and
S52 list markets, plus the Sportradar scoreboard on the same page.

Rules enforced here: one page per live recorded match (S77: and per real pre-match match inside the lead window),
polled no faster than every 3 s,
read via ``page.content()`` only (no clicks, no form input, no login, no requests other than the match
page itself), reloaded every ``RELOAD_EVERY_S``. Simulated (SRL) matches are recorded like real ones.
Records are stored per market: a record is written only when its own content changes, and a market
that disappears from the page is written once with ``status='closed'``.

S56: the page's own market JSON (``sma-event`` responses, read passively through ``page.on("response")``) is
the primary source. The DOM parse runs only when no JSON has arrived for ``JSON_FRESH_S`` seconds. The JSON
also gives the competition and start time (``on_meta``) and the Winner settlement (``on_settled``).

S68: the page's own score feed (``match-center/score/v3`` responses, read the same passive way) gives the live
score, toss and result. Changed states are handed to ``on_score``; unchanged or out-of-order ones are dropped.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Mapping, Optional
from urllib.parse import urljoin

from scraper.parsers.lotusbook_json import EventMeta, event_id_from_url, event_meta, event_records, settlement_of
from scraper.parsers.lotusbook_markets import MarketRecord, parse_market_html
from scraper.parsers.lotusbook_score import (
    PIPELINE_LEGACY,
    ScoreState,
    parse_score_v3,
    score_event_id_from_url,
    score_pipeline_of,
)
from scraper.parsers.lotusbook_scoreboard import ScoreboardState, parse_scoreboard_html
from shared.logging import setup_logging

logger = setup_logging("market_pages")

MIN_POLL_INTERVAL_S = 3.0
MAX_CONSECUTIVE_ERRORS = 5
PAGE_LOAD_TIMEOUT_MS = 60_000
# Reload the match page this often so a stalled live connection cannot freeze the recorded prices.
RELOAD_EVERY_S = 300.0
# S56: the DOM parse runs only when no market JSON has arrived for this long.
JSON_FRESH_S = 30.0
# A match worker stops when its match has shown no activity for this long (innings breaks drop matches off the
# listing while their page keeps updating). Read by plan_market_workers and scraper/manager.py.
MARKET_ABSENT_STOP_S = 2700.0
# S77: a real match gets its page this many minutes before its start (env PREMATCH_PAGE_LEAD_MIN overrides).
PREMATCH_PAGE_LEAD_MIN_DEFAULT = 75.0  # 15 min margin so the T-60m dataset snapshot has a fresh page row
# S77: a running pre-match page stays open while its start is within this of the lead window, in either
# direction. It closes when the start moved later by more than this (out of the window), or when the match is
# still not live this long after its start.
PREMATCH_STAY_GRACE_S = 1800.0
LIVE_KIND = "live"
PREMATCH_KIND = "prematch"

StoreFn = Callable[[str, list[MarketRecord]], Awaitable[None]]
# (match_id, scoreboard state or None when the widget is absent from the page, observed_at)
ScoreboardFn = Callable[[str, Optional[ScoreboardState], datetime], Awaitable[None]]
# (match_id, competition and start time from the market JSON)
MetaFn = Callable[[str, EventMeta], Awaitable[None]]
# (match_id, winner runner name, observed_at) for a CLOSED Winner market with exactly one winner
SettledFn = Callable[[str, str, datetime], Awaitable[None]]
# S68: (match_id, changed score state or None when only the pipeline is new, pipeline or None, observed_at)
ScoreFeedFn = Callable[[str, Optional[ScoreState], Optional[str], datetime], Awaitable[None]]
MarketKey = tuple[str, str, str]  # (market_type, market_name, selection)


def clamp_poll_interval(interval: float) -> float:
    """Never poll faster than the 3 s politeness floor."""
    return max(MIN_POLL_INTERVAL_S, float(interval))


def eligibility(*, is_live: bool, team_home: str, team_away: str, competition: str = "") -> tuple[bool, str]:
    """Return ``(eligible, reason)``. Only live matches get a page by this rule.

    S77 adds the pre-match case (real matches inside the lead window); see ``plan_market_workers``.
    S50: simulated (SRL and similar) matches are recorded like real ones, so they are eligible too.
    They are kept apart downstream (never approved or published); see scraper/manager.py.
    """
    if not is_live:
        return False, "not_live"
    return True, "ok"


# ── S77: which matches get a page, and when ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MarketCandidate:
    """What the planner knows about one listed match. Times are UTC.

    ``listing_seen``: last accepted listing event. ``live_seen``: last live activity (listing or own page).
    ``startable``: not in a restart backoff and the page can be opened. ``completed``: result already detected.
    """

    match_id: str
    is_live: bool = False
    simulated: bool = False
    start: Optional[datetime] = None
    completed: bool = False
    startable: bool = True
    listing_seen: Optional[datetime] = None
    live_seen: Optional[datetime] = None


@dataclass(frozen=True)
class MarketPlan:
    """``starts``: match ids to open a page for, in order. ``stops``: match id -> reason
    (``completed``, ``absent``, ``window``, or ``preempted`` when a live match needs its slot)."""

    starts: tuple[str, ...] = ()
    stops: Mapping[str, str] = field(default_factory=dict)


def in_prematch_window(start: datetime, now: datetime, lead: timedelta) -> bool:
    """A page may be opened from ``lead`` before the start up to the start itself (both ends inclusive)."""
    return start - lead <= now <= start


def prematch_may_stay(start: datetime, now: datetime, lead: timedelta) -> bool:
    """A running pre-match page stays while the start lies within the lead window widened by the stay grace."""
    grace = timedelta(seconds=PREMATCH_STAY_GRACE_S)
    return start - lead - grace <= now <= start + grace


def _age_s(now: datetime, seen: datetime) -> float:
    return (now - seen).total_seconds()


def _running_stop_reason(
    match: Optional[MarketCandidate], kind: str, now: datetime, lead: timedelta, absent_s: float
) -> Optional[str]:
    if match is not None and match.completed:
        return "completed"
    if kind == LIVE_KIND:
        seen = match.live_seen if match is not None else None
        return "absent" if seen is not None and _age_s(now, seen) > absent_s else None
    # Pre-match page: absence is measured on the listing, not on live activity (it is not live yet).
    if match is None:
        return "absent"
    if match.start is None:
        return "window"
    if match.listing_seen is not None and _age_s(now, match.listing_seen) > absent_s:
        return "absent"
    if not prematch_may_stay(match.start, now, lead):
        return "window"
    return None


def _start_key(match: Optional[MarketCandidate], now: datetime) -> datetime:
    return match.start if match is not None and match.start is not None else now


def plan_market_workers(
    matches: list[MarketCandidate],
    running: Mapping[str, str],
    now: datetime,
    cap: int,
    lead: timedelta,
    absent_s: float = MARKET_ABSENT_STOP_S,
) -> MarketPlan:
    """Decide which match pages to open and which to close. Pure: no browser, no DB, no clock.

    Rules (S77):
    - Live matches (SRL included) are served first. Pre-match pages (real matches only) fill the free slots,
      earliest start first, and only while the start is inside the lead window.
    - A live match that finds every slot taken closes the pre-match page with the latest start, and takes its slot.
      Live pages are never preempted.
    - A running live page closes when its match has shown no live activity for ``absent_s`` (as before S77), or
      when it is completed. A running pre-match page closes when its match leaves the listing for ``absent_s``,
      when its start moved out of the window by more than the stay grace, or when it is completed.
    - A match that was running at the start of this plan (including one being closed) is not started again in it.

    ``running`` maps every open page's match id to its kind (``LIVE_KIND`` or ``PREMATCH_KIND``). A page whose match is
    live now counts as a live page. ``cap`` bounds the pages open after the plan.
    """
    by_id = {m.match_id: m for m in matches}
    kinds = dict(running)
    for mid in list(kinds):
        match = by_id.get(mid)
        if match is not None and match.is_live and kinds[mid] == PREMATCH_KIND:
            kinds[mid] = LIVE_KIND

    stops: dict[str, str] = {}
    for mid, kind in kinds.items():
        reason = _running_stop_reason(by_id.get(mid), kind, now, lead, absent_s)
        if reason is not None:
            stops[mid] = reason

    active = {mid: kind for mid, kind in kinds.items() if mid not in stops}
    free = cap - len(active)
    taken = set(kinds)
    starts: list[str] = []

    live_wanting = sorted(
        (m for m in matches if m.is_live and not m.completed and m.startable and m.match_id not in taken),
        key=lambda m: (m.start is None, m.start or now, m.match_id),
    )
    for match in live_wanting:
        if free > 0:
            starts.append(match.match_id)
            active[match.match_id] = LIVE_KIND
            free -= 1
            continue
        victims = [mid for mid, kind in active.items() if kind == PREMATCH_KIND]
        if not victims:
            break  # every slot holds a live page: nothing to preempt
        victim = max(victims, key=lambda mid: (_start_key(by_id.get(mid), now), mid))
        stops[victim] = "preempted"
        del active[victim]
        starts.append(match.match_id)
        active[match.match_id] = LIVE_KIND

    pre_wanting = sorted(
        (
            m for m in matches
            if not m.is_live and not m.simulated and m.start is not None and not m.completed and m.startable
            and m.match_id not in taken and in_prematch_window(m.start, now, lead)
        ),
        key=lambda m: (m.start, m.match_id),
    )
    for match in pre_wanting:
        if free <= 0:
            break
        starts.append(match.match_id)
        free -= 1

    return MarketPlan(starts=tuple(starts), stops=stops)


def build_match_url(site_url: str, href: str) -> str:
    """Resolve a listing href (``/cricket/...``) against the site origin."""
    return urljoin(site_url, href)


# JS for the listing page. Read-only: it only reads an anchor's href.
RESOLVE_HREF_JS = """
(lotusId) => {
  const links = Array.from(document.querySelectorAll('a[href*="/cricket/"][href*="vs"]'));
  const hit = links.find(a => (a.getAttribute('href') || '').split('/').pop() === lotusId);
  return hit ? hit.getAttribute('href') : null;
}
"""


async def resolve_match_href(listing_page: Any, lotus_id: str) -> Optional[str]:
    """Find the match href for a LotusBook id on the already-open listing page."""
    return await listing_page.evaluate(RESOLVE_HREF_JS, lotus_id)


async def open_match_page(context: Any, url: str) -> Any:
    """Open one extra page for a match. Caller owns it and must close it."""
    page = await context.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT_MS)
    except Exception:
        await page.close()
        raise
    return page


def _closed(prev: MarketRecord, now: datetime) -> MarketRecord:
    """A market that left the page: same key, no prices, no lines, status ``closed``."""
    return replace(
        prev,
        time=now,
        back=None,
        lay=None,
        line_no=None,
        rate_no=None,
        line_yes=None,
        rate_yes=None,
        back_size=None,
        lay_size=None,
        status="closed",
    )


class MarketPageWorker:
    """Polls one match page and hands changed market records and the scoreboard to the callbacks."""

    def __init__(
        self,
        match_id: str,
        page: Any,
        store: StoreFn,
        poll_interval: float = MIN_POLL_INTERVAL_S,
        reload_every: float = RELOAD_EVERY_S,
        clock: Callable[[], float] = time.monotonic,
        on_scoreboard: Optional[ScoreboardFn] = None,
        on_meta: Optional[MetaFn] = None,
        on_settled: Optional[SettledFn] = None,
        on_score: Optional[ScoreFeedFn] = None,
    ) -> None:
        self.match_id = match_id
        self.page = page
        self.store = store
        self.poll_interval = clamp_poll_interval(poll_interval)
        self.reload_every = reload_every
        self._clock = clock
        self._last_load = clock()
        self._on_scoreboard = on_scoreboard
        self._on_meta = on_meta
        self._on_settled = on_settled
        self._on_score = on_score
        # S68 score feed state: last handed state, pipeline last handed, and one-time flags
        self._score_last: Optional[ScoreState] = None
        self._score_pipeline_sent: Optional[str] = None
        self._score_json_snapshots = 0
        self._score_error_logged = False
        self._score_legacy_logged = False
        # Last stored record per market key. A key absent from it is either new or was closed.
        self._last_by_key: dict[MarketKey, MarketRecord] = {}
        self.stored_snapshots = 0
        # S56 passive JSON capture state
        self._attached_at: Optional[float] = None  # set by attach(); None means DOM-only (S47 behaviour)
        self._json_at: Optional[float] = None      # clock time of the last market JSON ingested
        self._json_snapshots = 0
        self._json_error_logged = False
        self._meta_sent: Optional[tuple] = None
        self._settled_sent = False
        self._closed_logged = False
        self._tasks: set[asyncio.Task] = set()

    # ── S56: passive capture of the page's own market JSON ─────────────────────────────

    @property
    def json_snapshots(self) -> int:
        """Market JSON bodies ingested for this match."""
        return self._json_snapshots

    def attach(self) -> None:
        """Listen to the page's own responses. Passive: it reads responses the page requested, nothing else.

        A page object without ``on`` (a test double, for example) is left alone and the worker stays DOM-only.
        """
        if self._attached_at is not None:
            return
        self._attached_at = self._clock()
        page_on = getattr(self.page, "on", None)
        if callable(page_on):
            page_on("response", self._on_response)

    def _on_response(self, response: Any) -> None:
        """Synchronous listener: ignores every URL except this match's ``sma-event`` and score feed JSON."""
        try:
            url = response.url
        except Exception:
            return
        event_id = event_id_from_url(url)
        if event_id is not None and self._is_own_event(event_id):
            self._spawn(self.handle_response(response))
            return
        score_id = score_event_id_from_url(url)
        if score_id is not None and self._is_own_event(score_id):
            self._spawn(self.handle_score_response(response))

    def _spawn(self, coro: Any) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _is_own_event(self, event_id: str) -> bool:
        if not self.match_id.startswith("lb_"):
            return True
        return self.match_id[len("lb_"):] == event_id

    async def handle_response(self, response: Any) -> None:
        """Read one ``sma-event`` response body and ingest it. A body that fails to parse is logged once."""
        try:
            payload = await response.json()
        except Exception as exc:
            if not self._json_error_logged:
                self._json_error_logged = True
                logger.warning("market_json_unreadable", match_id=self.match_id, error=str(exc))
            return
        await self.ingest_event_json(payload)

    # ── S68: passive capture of the page's own score feed ──────────────────────────────

    @property
    def score_json_snapshots(self) -> int:
        """Score feed bodies ingested for this match."""
        return self._score_json_snapshots

    async def handle_score_response(self, response: Any) -> None:
        """Read one ``match-center/score/v3`` response body and ingest it. An unreadable body is logged once."""
        try:
            payload = await response.json()
        except Exception as exc:
            if not self._score_error_logged:
                self._score_error_logged = True
                logger.warning("score_json_unreadable", match_id=self.match_id, error=str(exc))
            return
        await self.ingest_score_json(payload)

    def _score_is_new(self, state: ScoreState) -> bool:
        """A state is new when it is not older than the last handed one and its score content differs."""
        last = self._score_last
        if last is None:
            return True
        if state.updated_at is not None and last.updated_at is not None and state.updated_at < last.updated_at:
            return False  # out of order: the feed already moved past it
        return state.content_key() != last.content_key()

    async def ingest_score_json(self, payload: Any) -> bool:
        """Hand a changed score state (or a newly seen pipeline) to ``on_score``. Returns True when a state was handed.

        Bodies that cannot be parsed are logged once and skipped. A legacy body has no state; its pipeline is
        still handed over once so the registry records it.
        """
        now = datetime.now(timezone.utc)
        try:
            pipeline = score_pipeline_of(payload)
            state = parse_score_v3(payload)
        except Exception as exc:
            if not self._score_error_logged:
                self._score_error_logged = True
                logger.warning("score_json_parse_error", match_id=self.match_id, error=str(exc))
            return False

        self._score_json_snapshots += 1
        if pipeline == PIPELINE_LEGACY and not self._score_legacy_logged:
            self._score_legacy_logged = True
            logger.info("score_pipeline_legacy", match_id=self.match_id)

        pipeline_new = pipeline is not None and pipeline != self._score_pipeline_sent
        fresh = state is not None and self._score_is_new(state)
        if not fresh and not pipeline_new:
            return False
        if pipeline_new:
            self._score_pipeline_sent = pipeline
        if fresh:
            self._score_last = state
        await self._send_score(state if fresh else None, pipeline, now)
        return fresh

    async def _send_score(self, state: Optional[ScoreState], pipeline: Optional[str], observed_at: datetime) -> None:
        if self._on_score is None:
            return
        try:
            await self._on_score(self.match_id, state, pipeline, observed_at)
        except Exception as exc:  # the registry or the score row must never stop the recording
            logger.warning("score_callback_error", match_id=self.match_id, error=str(exc))

    def _json_is_fresh(self) -> bool:
        """True while the market JSON is the source: attached, and a body arrived within JSON_FRESH_S.
        Before the first body, the window counts from attach()."""
        if self._attached_at is None:
            return False
        last = self._json_at if self._json_at is not None else self._attached_at
        return self._clock() - last < JSON_FRESH_S

    async def ingest_event_json(self, payload: Any) -> int:
        """Turn one event JSON body into records, hand over meta and settlement, store the changed records.

        Returns the number of records stored. A body that cannot be parsed is logged once and skipped.
        """
        now = datetime.now(timezone.utc)
        try:
            records = event_records(payload, self.match_id, now)
            meta = event_meta(payload)
            settlement = settlement_of(payload)
        except Exception as exc:
            if not self._json_error_logged:
                self._json_error_logged = True
                logger.warning("market_json_parse_error", match_id=self.match_id, error=str(exc))
            return 0

        self._json_at = self._clock()
        self._json_snapshots += 1

        if meta is not None:
            await self._send_meta(meta)
        if settlement is not None:
            await self._send_settlement(settlement, now)

        if not records:
            return 0
        changed = self._changed_records(records, now)
        if not changed:
            return 0
        await self.store(self.match_id, changed)
        self.stored_snapshots += 1
        return len(changed)

    async def _send_meta(self, meta: EventMeta) -> None:
        if self._on_meta is None:
            return
        key = (meta.competition_id, meta.competition_name, meta.open_date.isoformat() if meta.open_date else None)
        if key == self._meta_sent:
            return
        self._meta_sent = key
        try:
            await self._on_meta(self.match_id, meta)
        except Exception as exc:  # the registry update must never stop the recording
            logger.warning("market_meta_callback_error", match_id=self.match_id, error=str(exc))

    async def _send_settlement(self, settlement: Any, observed_at: datetime) -> None:
        if not self._closed_logged:
            self._closed_logged = True
            logger.info(
                "winner_market_closed",
                match_id=self.match_id,
                market_id=settlement.market_id,
                winner=settlement.winner,
                runner_statuses=[list(item) for item in settlement.runner_statuses],
            )
        if self._settled_sent or settlement.winner is None or self._on_settled is None:
            return
        self._settled_sent = True
        try:
            await self._on_settled(self.match_id, settlement.winner, observed_at)
        except Exception as exc:
            logger.warning("market_settled_callback_error", match_id=self.match_id, error=str(exc))

    def seed(self, records: list[MarketRecord]) -> None:
        """Start from the last stored state of each market (read from the DB), so a restarted worker does not rewrite
        every unchanged market once."""
        for record in records:
            self._last_by_key[(record.market_type, record.market_name, record.selection)] = record

    def _changed_records(self, records: list[MarketRecord], now: datetime) -> list[MarketRecord]:
        """Records whose own content differs from the last stored record of the same key, plus one
        ``closed`` record for each key that disappeared from the page."""
        current: dict[MarketKey, MarketRecord] = {}
        for record in records:
            current[(record.market_type, record.market_name, record.selection)] = record

        changed: list[MarketRecord] = []
        for key, record in current.items():
            previous = self._last_by_key.get(key)
            if previous is None or previous.content_key() != record.content_key():
                changed.append(record)
                self._last_by_key[key] = record

        # A key that left the page is written once as closed and remembered as closed, so a closed market that
        # flickers in and out of the feed is not rewritten on every poll.
        for key in [k for k in self._last_by_key if k not in current]:
            previous = self._last_by_key[key]
            if previous.status == "closed" and previous.back is None and previous.lay is None:
                continue
            closed = _closed(previous, now)
            changed.append(closed)
            self._last_by_key[key] = closed
        return changed

    async def _scoreboard_step(self, html: str, now: datetime) -> None:
        if self._on_scoreboard is None:
            return
        state = parse_scoreboard_html(html)
        try:
            await self._on_scoreboard(self.match_id, state, now)
        except Exception as exc:  # the scoreboard must never stop the market recording
            logger.warning("scoreboard_callback_error", match_id=self.match_id, error=str(exc))

    async def poll_once(self) -> int:
        """Read the page once. Return the number of market records stored (0 when unchanged).

        A page with no market blocks at all is treated as a transient read: nothing is stored and no
        market is closed. Closing needs a page that still shows markets.
        """
        if self._clock() - self._last_load >= self.reload_every:
            await self.page.reload(wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT_MS)
            self._last_load = self._clock()
        html = await self.page.content()
        now = datetime.now(timezone.utc)
        await self._scoreboard_step(html, now)

        # S56: the market JSON is the primary source; the DOM parse runs only when it has been silent a while
        if self._json_is_fresh():
            return 0
        records = parse_market_html(html, self.match_id, now)
        if not records:
            return 0
        changed = self._changed_records(records, now)
        if not changed:
            return 0
        await self.store(self.match_id, changed)
        self.stored_snapshots += 1
        return len(changed)

    async def run(self, stop: asyncio.Event) -> None:
        """Poll until ``stop`` is set or too many consecutive errors occur."""
        self.attach()
        errors = 0
        while not stop.is_set():
            try:
                await self.poll_once()
                errors = 0
            except Exception as exc:  # keep polling through transient page errors
                errors += 1
                logger.warning("market_poll_error", match_id=self.match_id, error=str(exc), count=errors)
                if errors >= MAX_CONSECUTIVE_ERRORS:
                    logger.error("market_worker_giving_up", match_id=self.match_id)
                    break
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.poll_interval)
            except asyncio.TimeoutError:
                pass
