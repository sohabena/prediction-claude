"""
Scraper manager: orchestrates LotusBook scraping across all live matches.
Manages browser lifecycle, coordinates workers, publishes to Redis, stores to DB.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.async_api import Browser, BrowserContext, Playwright, async_playwright

from features.data_quality import TickValidator
from features.extractors.category_features import MatchCategoryClassifier, MatchTier
from scraper.live_match_tracker import LiveMatchTracker
from scraper.match_filter import MatchClassifier
from scraper.lotus_result_detector import LotusResultDetector
from scraper.market_pages import MarketPageWorker, build_match_url, eligibility, open_match_page, resolve_match_href
from scraper.parsers.lotusbook_markets import MarketRecord
from scraper.worker import ScraperWorker
from shared.config import get_settings
from shared.constants import CHANNEL_MATCH_CONTEXT, CHANNEL_MATCH_EVENTS, KEY_ACTIVE_MATCHES, KEY_MATCH_CONTEXT
from shared.db import get_session
from shared.logging import setup_logging
from shared.redis_client import get_redis
from shared.schemas import OddsEvent

logger = setup_logging("scraper_manager")

ROOT_DIR = Path(__file__).resolve().parent.parent
FEED_STATE_PATH = ROOT_DIR / "logs" / "scraper_feed.json"
MARKET_WORKER_CAP = 4  # S47: max extra match pages open at once (politeness)


def _env_flag(name: str, default: bool) -> bool:
    """Read a boolean environment flag ("1", "true", "yes", "on" are true)."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def write_feed_state(
    path: Path, *, updated_at: str, last_live_event_at: str | None, live_matches: int
) -> None:
    """Write the feed heartbeat that the watchdog (scripts/run_scraper_forever.py) reads.

    Written once per scrape batch. ``last_live_event_at`` only moves when a live event was
    processed, so a stalled scraper shows an old value while ``live_matches`` is still > 0.
    """
    payload = {
        "updated_at": updated_at,
        "last_live_event_at": last_live_event_at,
        "live_matches": live_matches,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        logger.warning("feed_state_write_error", path=str(path), error=str(e))


class ScraperManager:
    """
    Orchestrates LotusBook scraping.

    Responsibilities:
    - Manage Playwright browser instances
    - Coordinate scraper workers per page
    - Publish events to Redis
    - Write to TimescaleDB
    - Handle graceful shutdown
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._worker: ScraperWorker | None = None
        self._running = False
        self._active_matches: dict[str, dict[str, Any]] = {}
        self._match_filter = MatchClassifier(
            enabled=self.settings.scraper.international_only,
        )
        self._category_classifier = MatchCategoryClassifier()
        self._tick_validator = TickValidator()
        self._live_match_tracker = LiveMatchTracker()
        self._seen_match_ids: set[str] = set()
        self._scrape_approved_ids: set[str] = set()  # cached from DB
        self._completed_match_ids: set[str] = set()
        self._last_stored_fingerprint: dict[str, str] = {}  # matches with results
        self._quality_stats = {"stored": 0, "warnings": 0, "rejected": 0, "recorded_unapproved": 0,
                               "recorded_prematch": 0}
        self._result_detector = LotusResultDetector()
        self._last_approved_refresh: datetime | None = None
        # S37: record every qualifying live match. Approval only controls live publishing.
        self._record_all = _env_flag("SCRAPER_RECORD_ALL", default=True)
        # Pre-match prices of upcoming matches (stored only on change; see _prematch_due).
        self._record_prematch = _env_flag("SCRAPER_RECORD_PREMATCH", default=True)
        self._prematch_last: dict[str, tuple[tuple, datetime]] = {}
        self._last_live_event_at: str | None = None
        # S47: one read-only market-page worker per eligible live match.
        self._listing_page: Any = None
        self._market_attempted: set[str] = set()
        self._market_tasks: dict[str, asyncio.Task] = {}
        self._market_stops: dict[str, asyncio.Event] = {}

    async def start(self) -> None:
        """Start the scraper manager: launch browser, begin scraping."""
        logger.info("scraper_starting", url=self.settings.scraper.betting_site_url)

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self.settings.scraper.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        self._context = await self._browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )

        page = await self._context.new_page()
        self._listing_page = page
        await page.goto(
            self.settings.scraper.betting_site_url,
            wait_until="domcontentloaded",
            timeout=self.settings.scraper.session_timeout,
        )
        # LotusBook is SPA/async: anchors may not exist yet; without this the first poll
        # often returns 0 matches (see a[href*="/cricket/"][href*="vs"] — no #inPlayTeamName).
        try:
            await page.wait_for_selector(
                'a[href*="/cricket/"][href*="vs"]',
                timeout=min(self.settings.scraper.session_timeout, 60000),
            )
        except Exception as e:
            logger.warning("match_links_selector_timeout", error=str(e))

        logger.info("page_loaded", url=self.settings.scraper.betting_site_url)

        self._worker = ScraperWorker(
            page=page,
            poll_interval=self.settings.scraper.poll_interval,
        )

        # Try to set up WebSocket interception
        try:
            await self._worker.setup_ws_interception()
        except Exception as e:
            logger.warning("ws_interception_failed", error=str(e))

        self._running = True

        # Load scrape-approved match IDs from DB
        await self._load_scrape_approved()

        # Load already-completed match IDs so we don't scrape finished matches
        await self._load_completed_matches()

        # Pre-load already-resolved match IDs into detector
        self._result_detector.load_already_resolved(self._completed_match_ids)

        # Listen for new match results to mark matches as completed
        self._result_listener_task = asyncio.create_task(
            self._listen_for_results()
        )

        await self._worker.poll_loop(callback=self._on_events)

    async def _load_scrape_approved(self) -> None:
        """Load scrape-approved match IDs and times from DB into memory cache."""
        try:
            from sqlalchemy import select
            from backend.models.match_status import MatchTrainingStatus
            async with get_session() as session:
                result = await session.execute(
                    select(MatchTrainingStatus.match_id, MatchTrainingStatus.match_start_time).where(
                        MatchTrainingStatus.scrape_status == "scrape_approved"
                    )
                )
                
                approved_ids = set()
                approved_times = {}
                for row in result.fetchall():
                    approved_ids.add(row[0])
                    if row[1]:
                        approved_times[row[0]] = row[1]
                        
                self._scrape_approved_ids = approved_ids
                self._scrape_approved_times = approved_times
                logger.info("scrape_approved_loaded", count=len(approved_ids))
        except Exception as e:
            logger.error("load_scrape_approved_error", error=str(e))

    async def _load_completed_matches(self) -> None:
        """Load match IDs that already have results (completed matches)."""
        try:
            from sqlalchemy import select
            from backend.models.results import MatchResultRecord
            async with get_session() as session:
                result = await session.execute(
                    select(MatchResultRecord.match_id)
                )
                ids = {row[0] for row in result.fetchall()}
                self._completed_match_ids = ids
                logger.info("completed_matches_loaded", count=len(ids))
        except Exception as e:
            logger.error("load_completed_matches_error", error=str(e))

    async def _listen_for_results(self) -> None:
        """Subscribe to match result events so we stop scraping finished matches."""
        try:
            from shared.constants import CHANNEL_MATCH_RESULTS
            redis = await get_redis()
            from shared.redis_client import default_consumer_name

            async for msg in redis.consume_bus(
                "scraper", default_consumer_name("scraper-"), [CHANNEL_MATCH_RESULTS]
            ):
                try:
                    match_id = msg.data.get("match_id", "")
                    if match_id:
                        self._completed_match_ids.add(match_id)
                        logger.info("match_completed_via_result",
                                    match_id=match_id)
                except Exception as e:
                    logger.warning("result_listener_parse_error", error=str(e))
                await redis.ack_bus(msg)
        except Exception as e:
            logger.error("result_listener_error", error=str(e))

    async def _on_events(self, events: list[OddsEvent]) -> None:
        """Handle new odds events.

        Two-phase flow:
        1. DISCOVER: Register every qualifying match in DB (scrape_status=discovered).
        2. SCRAPE: Only store tick data for scrape_approved matches.
        """
        if not events:
            return

        redis = await get_redis()
        accepted_count = 0
        now = datetime.now(timezone.utc)
        current_batch_ids: set[str] = set()
        live_batch_ids: set[str] = set()

        # Check runtime dynamic scraper settings from Redis
        try:
            auto_raw = await redis.client.get("config:scraper:auto_approve")
            if auto_raw is not None:
                auto_approve_enabled = str(auto_raw).lower() in ("true", "1")
            else:
                auto_approve_enabled = bool(self.settings.scraper.auto_approve_matches)

            intl_raw = await redis.client.get("config:scraper:international_only")
            if intl_raw is not None:
                intl_enabled = str(intl_raw).lower() in ("true", "1")
            else:
                intl_enabled = bool(self.settings.scraper.international_only)

            self._match_filter.enabled = intl_enabled
            if intl_enabled:
                # Evict any active matches that fail blacklist immediately
                to_evict = [
                    mid for mid, m in self._active_matches.items()
                    if (
                        self._match_filter._is_blacklisted(m.get("competition", ""))
                        or self._match_filter._is_blacklisted(m.get("team_home", ""))
                        or self._match_filter._is_blacklisted(m.get("team_away", ""))
                    )
                ]
                for mid in to_evict:
                    del self._active_matches[mid]
        except Exception as e:
            auto_approve_enabled = bool(self.settings.scraper.auto_approve_matches)

        # Periodically refresh scrape-approved matches (every 5s) so UI approvals take effect immediately
        if self._last_approved_refresh is None or (now - self._last_approved_refresh).total_seconds() > 5:
            await self._load_scrape_approved()
            self._last_approved_refresh = now

        for event in events:
            # Match filter: skip non-qualifying matches
            if not self._match_filter.qualifies(event):
                continue

            accepted_count += 1
            current_batch_ids.add(event.match_id)
            if event.is_live:
                live_batch_ids.add(event.match_id)

            # Track active matches (always, for the frontend listing)
            self._active_matches[event.match_id] = {
                "match_id": event.match_id,
                "team_home": event.team_home,
                "team_away": event.team_away,
                "competition": event.competition,
                "is_live": event.is_live,
                "back_home": event.back_home,
                "lay_home": event.lay_home,
                "back_away": event.back_away,
                "lay_away": event.lay_away,
                "score_text": getattr(event, "score_text", None) or (f"{event.score}/{event.wickets} ({event.overs} ov)" if getattr(event, "overs", None) is not None else None),
                "scheduled_time": event.scheduled_time.isoformat() if event.scheduled_time else None,
                "last_update": now.isoformat(),
            }

            # Feed odds into result detector (for all qualifying matches)
            # Score data will be added below after LiveMatchTracker updates
            self._result_detector.update_tick(
                match_id=event.match_id,
                team_home=event.team_home,
                team_away=event.team_away,
                competition=event.competition,
                is_live=event.is_live,
                back_home=event.back_home,
                back_away=event.back_away,
                lay_home=event.lay_home,
                lay_away=event.lay_away,
            )

            # ── SIMULATED GATE: never register or record SRL / simulated events, whatever the filter setting ──
            if self._match_filter.reject_if_simulated(event):
                continue

            # Register first-seen matches as "discovered" (no tick storage yet)
            if event.match_id not in self._seen_match_ids:
                self._seen_match_ids.add(event.match_id)
                await self._upsert_training_status(event, auto_approve_enabled=auto_approve_enabled)

            # ── RECORD GATE (S37): approval no longer decides recording ──
            # With SCRAPER_RECORD_ALL on (default) every qualifying match is recorded while live.
            # Approved matches additionally go to live consumers (publish + MatchContext) below.
            approved = event.match_id in self._scrape_approved_ids
            if not approved and not self._record_all:
                continue

            # ── LIVE GATE: completed matches stop. Upcoming (not-live) matches are recorded pre-match
            # when SCRAPER_RECORD_PREMATCH is on, but only on a price change (see _prematch_due).
            if event.match_id in self._completed_match_ids:
                continue
            prematch = not event.is_live
            if prematch and not (self._record_prematch and self._prematch_due(event, now)):
                continue

            # ── SCHEDULE GATE: approved matches wait for their scheduled time ──
            if approved and hasattr(self, "_scrape_approved_times"):
                start_time = self._scrape_approved_times.get(event.match_id)
                if start_time is not None:
                    from datetime import timedelta
                    # Allow scraping starting 10 minutes before the scheduled time
                    if now < start_time - timedelta(minutes=10):
                        continue

            # ── Ingestion Quality Gate ──────────────────────────────

            # Gate 1: Skip extreme odds (>500 = no real market)
            bh = event.back_home or 0
            ba = event.back_away or 0
            if (bh > 500 or ba > 500):
                self._quality_stats["rejected"] += 1
                logger.debug("tick_rejected_extreme_odds", match_id=event.match_id,
                             back_home=bh, back_away=ba)
                continue

            # Gate 2: Skip near-certain outcomes (both teams < 1.05 is impossible)
            if bh > 0 and bh < 1.02 and ba > 0 and ba < 1.02:
                self._quality_stats["rejected"] += 1
                continue

            # Gate 3: Tick-level quality validation
            tick_data = {
                "back_home": event.back_home,
                "lay_home": event.lay_home,
                "back_away": event.back_away,
                "lay_away": event.lay_away,
                "back_draw": event.back_draw,
                "lay_draw": event.lay_draw,
                "timestamp": event.timestamp,
            }
            tick_ok, tick_issues = self._tick_validator.validate(
                event.match_id, tick_data,
            )
            if tick_issues:
                warn_issues = [i for i in tick_issues if i.severity == "warn"]
                err_issues = [i for i in tick_issues if i.severity == "error"]
                if warn_issues:
                    self._quality_stats["warnings"] += len(warn_issues)
                    logger.debug(
                        "tick_quality_warnings",
                        match_id=event.match_id,
                        warnings=[i.message for i in warn_issues],
                    )
                if err_issues:
                    self._quality_stats["rejected"] += 1
                    logger.warning(
                        "tick_rejected",
                        match_id=event.match_id,
                        errors=[i.message for i in err_issues],
                    )
                    continue  # Skip storing this tick

            # Derive MatchContext for every recorded live match (score state is part of the recording);
            # publish to Redis for live consumers only for approved matches.
            context = None
            if not prematch:
                context = self._live_match_tracker.update(event)
            if approved and not prematch:
                event_data = event.model_dump(mode="json")
                if context is not None:
                    event_data["context"] = context.model_dump(mode="json")
                await redis.publish_event(CHANNEL_MATCH_EVENTS, event_data)
            elif not approved:
                self._quality_stats["recorded_unapproved"] += 1
            if prematch:
                self._quality_stats["recorded_prematch"] += 1

            # Store to DB (recording, independent of approval)
            self._quality_stats["stored"] += 1
            await self._store_odds_tick(event)
            if prematch:
                # Marked only once stored, so a tick rejected by the quality gates does not reset the throttle
                self._prematch_last[event.match_id] = (self._prices(event), now)
            else:
                await self._ensure_market_worker(event)

            # Store MatchContext for every recorded match; publish it only for approved matches.
            # LotusBook often shows no score at all; a row is stored only when a score was actually read,
            # so the table never holds placeholder 0/0 rows.
            if context is not None:
                has_score = bool((event.score_text or "").strip())
                await self._publish_match_context(redis, context, publish=approved, store=has_score)
                # Feed score data into result detector for cross-referencing
                tracker_state = self._live_match_tracker._matches.get(event.match_id)
                fi_total = tracker_state.first_innings_total() if tracker_state else None
                self._result_detector.update_tick(
                    match_id=event.match_id,
                    team_home=event.team_home,
                    team_away=event.team_away,
                    competition=event.competition,
                    is_live=event.is_live,
                    back_home=None, back_away=None,  # odds already set above
                    innings=context.innings,
                    score=context.score,
                    first_innings_total=fi_total,
                )

        # --- Feed heartbeat for the watchdog (S37) ---
        if live_batch_ids:
            self._last_live_event_at = now.isoformat()
        write_feed_state(
            FEED_STATE_PATH,
            updated_at=now.isoformat(),
            last_live_event_at=self._last_live_event_at,
            live_matches=len(live_batch_ids),
        )

        # --- Result detection: check if any tracked matches have completed ---
        try:
            new_results = await self._result_detector.check_completed(current_batch_ids)
            for r in new_results:
                mid = r["match_id"]
                self._completed_match_ids.add(mid)
                logger.info(
                    "match_result_auto_detected",
                    match_id=mid,
                    winner=r.get("winner", ""),
                    result_type=r.get("result_type", ""),
                )
        except Exception as e:
            logger.error("result_detection_error", error=str(e))

        # Mark matches NOT in current batch as not-live (they may have finished)
        # and remove matches not seen for >120 seconds (completed/removed from exchange)
        stale_ids = []
        for mid, mdata in list(self._active_matches.items()):
            if mid not in current_batch_ids:
                # Match was not in this scrape — mark as not live
                if mdata.get("is_live"):
                    mdata["is_live"] = False
                # Check if stale (not seen for >120 seconds) — remove entirely
                try:
                    last = datetime.fromisoformat(mdata["last_update"])
                    if (now - last).total_seconds() > 120:
                        stale_ids.append(mid)
                except (ValueError, KeyError):
                    pass
        for mid in stale_ids:
            del self._active_matches[mid]

        # Update active matches list in Redis
        await redis.set_json(
            KEY_ACTIVE_MATCHES,
            {
                "matches": list(self._active_matches.values()),
                "updated_at": now.isoformat(),
            },
            ttl=60,
        )

        filter_stats = self._match_filter.stats
        logger.info(
            "events_processed",
            total=len(events),
            accepted=accepted_count,
            rejected=len(events) - accepted_count,
            filter_total_accepted=filter_stats["accepted"],
            filter_total_rejected=filter_stats["rejected"],
            active_matches=len(self._active_matches),
            quality_stored=self._quality_stats["stored"],
            quality_warnings=self._quality_stats["warnings"],
            quality_rejected=self._quality_stats["rejected"],
        )

    PREMATCH_MIN_INTERVAL_S = 30.0     # at most one stored pre-match tick per match per 30 s
    PREMATCH_HEARTBEAT_S = 900.0       # store an unchanged pre-match price at least every 15 min

    def _prematch_due(self, event: OddsEvent, now: datetime) -> bool:
        """True when a not-yet-live match's prices should be stored now.

        Pre-match markets on the listing page move slowly, and the page is polled every few seconds,
        so storing every poll would multiply volume for nothing. Store when any price changed (and
        at least ``PREMATCH_MIN_INTERVAL_S`` passed), or as a heartbeat every ``PREMATCH_HEARTBEAT_S``.
        """
        prices = self._prices(event)
        last = self._prematch_last.get(event.match_id)
        if last is not None:
            last_prices, last_at = last
            elapsed = (now - last_at).total_seconds()
            if elapsed < self.PREMATCH_MIN_INTERVAL_S:
                return False
            if prices == last_prices and elapsed < self.PREMATCH_HEARTBEAT_S:
                return False
        return True

    @staticmethod
    def _prices(event: OddsEvent) -> tuple:
        return (event.back_home, event.lay_home, event.back_draw, event.lay_draw, event.back_away, event.lay_away)

    async def _publish_match_context(
        self, redis: Any, context: Any, publish: bool = True, store: bool = True,
    ) -> None:
        """Store derived MatchContext to DB when ``store``, and publish it to Redis when ``publish``.

        Context is derived inline from LotusBook score_text with zero
        latency gap vs odds data. Every recorded match stores its context (it is part of
        the recording); only approved matches are published to live consumers.
        """
        from backend.models.match import MatchContextRecord

        try:
            if publish:
                ctx_data = context.model_dump(mode="json")

                # Publish to Redis pub/sub (consumed by LiveTradingLoop, FeaturePipeline)
                await redis.publish_event(CHANNEL_MATCH_CONTEXT, ctx_data)

                # Cache in Redis (consumed by context_resolver.get_match_context)
                key = KEY_MATCH_CONTEXT.format(match_id=context.match_id)
                await redis.set_json(key, ctx_data, ttl=60)

            if not store:
                return

            # Store to TimescaleDB
            async with get_session() as session:
                record = MatchContextRecord(
                    time=context.timestamp,
                    match_id=context.match_id,
                    is_live=context.is_live,
                    score=context.score,
                    wickets=context.wickets,
                    overs=context.overs,
                    run_rate=context.run_rate,
                    req_run_rate=context.required_run_rate,
                    innings=context.innings,
                    balls_remaining=context.balls_remaining,
                    batting_team=context.batting_team,
                    bowling_team=context.bowling_team,
                    status=context.status.value,
                    match_format=context.match_format,
                )
                session.add(record)

        except Exception as e:
            logger.debug("context_publish_error", match_id=context.match_id, error=str(e))

    # ── S47: Bookmaker / session markets via one read-only page per live match ──

    async def _ensure_market_worker(self, event: OddsEvent) -> None:
        """Start one read-only market-page worker for an eligible live match.

        Never clicks, never inputs, never logs in. Simulated matches never start a worker.
        Workers stop when the match completes or on shutdown.
        """
        self._sweep_market_workers()
        mid = event.match_id
        if mid in self._market_attempted or not _env_flag("SCRAPER_MARKET_PAGES", default=True):
            return
        eligible, reason = eligibility(
            is_live=event.is_live,
            team_home=event.team_home,
            team_away=event.team_away,
            competition=event.competition,
        )
        if not eligible:
            if reason == "simulated":
                self._market_attempted.add(mid)
            return
        if not mid.startswith("lb_") or self._listing_page is None or self._context is None:
            return
        if len(self._market_tasks) >= MARKET_WORKER_CAP:
            logger.warning("market_worker_cap_reached", cap=MARKET_WORKER_CAP, match_id=mid)
            return
        self._market_attempted.add(mid)
        try:
            href = await resolve_match_href(self._listing_page, mid[len("lb_"):])
            if not href:
                logger.warning("market_href_not_found", match_id=mid)
                return
            url = build_match_url(self.settings.scraper.betting_site_url, href)
            page = await open_match_page(self._context, url)
        except Exception as e:
            logger.warning("market_page_open_failed", match_id=mid, error=str(e))
            return
        stop = asyncio.Event()
        worker = MarketPageWorker(
            match_id=mid,
            page=page,
            store=self._store_market_records,
            poll_interval=self.settings.scraper.poll_interval,
        )
        self._market_stops[mid] = stop
        self._market_tasks[mid] = asyncio.create_task(self._run_market_worker(mid, worker, page, stop))
        logger.info("market_worker_started", match_id=mid, url=url)

    async def _run_market_worker(self, mid: str, worker: MarketPageWorker, page: Any, stop: asyncio.Event) -> None:
        try:
            await worker.run(stop)
        finally:
            try:
                await page.close()
            except Exception:
                pass
            self._market_tasks.pop(mid, None)
            self._market_stops.pop(mid, None)
            logger.info("market_worker_stopped", match_id=mid, snapshots=worker.stored_snapshots)

    def _sweep_market_workers(self) -> None:
        for mid in list(self._market_stops):
            if mid in self._completed_match_ids:
                self._market_stops[mid].set()

    async def _store_market_records(self, match_id: str, records: list[MarketRecord]) -> None:
        """Write changed market records to market_ticks (storage call only)."""
        from backend.models.market_ticks import MarketTick

        try:
            async with get_session() as session:
                session.add_all(
                    [
                        MarketTick(
                            time=r.time,
                            match_id=r.match_id,
                            market_type=r.market_type,
                            market_name=r.market_name,
                            selection=r.selection,
                            back=r.back,
                            lay=r.lay,
                            line_no=r.line_no,
                            rate_no=r.rate_no,
                            line_yes=r.line_yes,
                            rate_yes=r.rate_yes,
                            status=r.status,
                            source=r.source,
                        )
                        for r in records
                    ]
                )
        except Exception as e:
            logger.error("market_db_store_error", match_id=match_id, error=str(e))

    async def _store_odds_tick(self, event: OddsEvent) -> None:
        """Store an odds event to TimescaleDB with volume + context data."""
        # Dedup: skip storing if prices/score haven't changed
        fp = f"{event.back_home}|{event.lay_home}|{event.back_away}|{event.lay_away}|{event.score_text}"
        if self._last_stored_fingerprint.get(event.match_id) == fp:
            return
        self._last_stored_fingerprint[event.match_id] = fp
        import re as _re
        from backend.models.odds import OddsTick

        # Compute derived fields
        ip_home = 1.0 / event.back_home if event.back_home else None
        ip_away = 1.0 / event.back_away if event.back_away else None
        ip_draw = 1.0 / event.back_draw if event.back_draw else None

        overround = None
        if ip_home is not None and ip_away is not None:
            overround = (ip_home or 0) + (ip_away or 0) + (ip_draw or 0) - 1.0

        # Parse score_text into structured fields (e.g. "45/2 (8.3)" -> score=45, wickets=2, overs=8.3)
        score_val = None
        wickets_val = None
        overs_val = None
        if event.score_text:
            score_match = _re.search(r"(\d+)/(\d+)", event.score_text)
            if score_match:
                score_val = int(score_match.group(1))
                wickets_val = int(score_match.group(2))
            overs_match = _re.search(r"\((\d+(?:\.\d+)?)\)", event.score_text)
            if overs_match:
                overs_val = float(overs_match.group(1))

        try:
            async with get_session() as session:
                tick = OddsTick(
                    time=event.timestamp,
                    match_id=event.match_id,
                    team_home=event.team_home,
                    team_away=event.team_away,
                    competition=event.competition,
                    back_home=event.back_home,
                    lay_home=event.lay_home,
                    back_draw=event.back_draw,
                    lay_draw=event.lay_draw,
                    back_away=event.back_away,
                    lay_away=event.lay_away,
                    volume_back_home=event.volume_back_home,
                    volume_lay_home=event.volume_lay_home,
                    volume_back_away=event.volume_back_away,
                    volume_lay_away=event.volume_lay_away,
                    implied_prob_home=ip_home,
                    implied_prob_away=ip_away,
                    overround=overround,
                    score=score_val,
                    wickets=wickets_val,
                    overs=overs_val,
                    is_live=event.is_live,
                    source=event.source,
                    scrape_latency_ms=event.scrape_latency_ms or None,
                )
                session.add(tick)
                from sqlalchemy import text
                await session.execute(
                    text("UPDATE match_training_status SET tick_count = COALESCE(tick_count, 0) + 1 WHERE match_id = :mid"),
                    {"mid": event.match_id},
                )
        except Exception as e:
            logger.error("db_store_error", match_id=event.match_id, error=str(e))

    async def _upsert_training_status(self, event: OddsEvent, auto_approve_enabled: bool = False) -> None:
        """Insert match_training_status for a newly discovered match.

        Two-phase approval:
        1. scrape_status: 'discovered' (default) or 'scrape_approved' (auto or manual)
        2. training_status: 'pending' until user reviews collected data

        When auto_approve_enabled is True, ICC/international/major franchise matches
        get scrape_approved automatically. Non-international matches and SRL/virtual/cyber
        are NEVER auto-approved. Otherwise all start as 'discovered'.
        """
        from backend.models.match_status import MatchTrainingStatus

        category = self._category_classifier.classify(
            competition=event.competition,
            team_home=event.team_home,
            team_away=event.team_away,
        )
        is_intl_major = self._match_filter.is_international_or_major(event)
        if auto_approve_enabled and is_intl_major:
            auto_tiers = {
                MatchTier.ICC_EVENT,
                MatchTier.INTERNATIONAL_BILATERAL,
                MatchTier.MAJOR_FRANCHISE,
            }
            should_auto = category.tier in auto_tiers
            scrape_status = "scrape_approved" if should_auto else "discovered"
        else:
            should_auto = False
            scrape_status = "discovered"

        try:
            async with get_session() as session:
                from sqlalchemy import select as sa_select
                existing = await session.execute(
                    sa_select(MatchTrainingStatus).where(
                        MatchTrainingStatus.match_id == event.match_id
                    )
                )
                record = existing.scalar_one_or_none()
                if record is not None:
                    # Update start time if it was missing and we now have it
                    if record.match_start_time is None and event.scheduled_time is not None:
                        record.match_start_time = event.scheduled_time

                    # Auto-upgrade to scrape_approved if should_auto is True
                    if should_auto and record.scrape_status != "scrape_approved":
                        record.scrape_status = "scrape_approved"
                        record.auto_approved = True
                        if record.training_status == "pending":
                            record.training_status = "approved"

                    # Already tracked — update in-memory cache
                    if record.scrape_status == "scrape_approved":
                        self._scrape_approved_ids.add(event.match_id)
                        if record.match_start_time:
                            if not hasattr(self, "_scrape_approved_times"):
                                self._scrape_approved_times = {}
                            self._scrape_approved_times[event.match_id] = record.match_start_time
                    else:
                        self._scrape_approved_ids.discard(event.match_id)

                    return

                # Auto-approve both scraping AND training for high-tier matches.
                # This eliminates the manual approval bottleneck — ICC,
                # international, and major franchise matches go straight
                # to training-ready once scraped.
                training_status = "approved" if should_auto else "pending"

                record = MatchTrainingStatus(
                    match_id=event.match_id,
                    team_home=event.team_home,
                    team_away=event.team_away,
                    competition=event.competition,
                    scrape_status=scrape_status,
                    training_status=training_status,
                    auto_approved=should_auto,
                    match_start_time=event.scheduled_time,
                )
                session.add(record)

            # Update in-memory cache
            if scrape_status == "scrape_approved":
                self._scrape_approved_ids.add(event.match_id)

            logger.info(
                "match_discovered",
                match_id=event.match_id,
                scrape_status=scrape_status,
                auto_approved=should_auto,
                tier=category.tier.value,
                teams=f"{event.team_home} vs {event.team_away}",
            )
        except Exception as e:
            logger.error(
                "training_status_error",
                match_id=event.match_id,
                error=str(e),
            )

    async def stop(self) -> None:
        """Gracefully stop scraping."""
        logger.info("scraper_stopping")
        self._running = False

        for mid in list(self._market_stops):
            self._market_stops[mid].set()
        if self._market_tasks:
            await asyncio.wait(list(self._market_tasks.values()), timeout=10)

        # Clean up result detector memory
        if self._result_detector:
            self._result_detector.cleanup()
        if self._worker:
            self._worker.stop()
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

        logger.info("scraper_stopped")


async def run_scraper() -> None:
    """Entry point for running the scraper service."""
    manager = ScraperManager()

    loop = asyncio.get_running_loop()

    def _shutdown() -> None:
        asyncio.ensure_future(manager.stop())

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _shutdown)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass

    try:
        await manager.start()
    except KeyboardInterrupt:
        await manager.stop()


if __name__ == "__main__":
    asyncio.run(run_scraper())
