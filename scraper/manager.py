"""
Scraper manager: orchestrates LotusBook scraping across all live matches.
Manages browser lifecycle, coordinates workers, publishes to Redis, stores to DB.
"""

from __future__ import annotations

import asyncio
import signal
from datetime import datetime, timezone
from typing import Any

from playwright.async_api import Browser, BrowserContext, Playwright, async_playwright

from features.data_quality import TickValidator
from features.extractors.category_features import MatchCategoryClassifier, MatchTier
from scraper.live_match_tracker import LiveMatchTracker
from scraper.match_filter import MatchClassifier
from scraper.lotus_result_detector import LotusResultDetector
from scraper.worker import ScraperWorker
from shared.config import get_settings
from shared.constants import CHANNEL_MATCH_CONTEXT, CHANNEL_MATCH_EVENTS, KEY_ACTIVE_MATCHES, KEY_MATCH_CONTEXT
from shared.db import get_session
from shared.logging import setup_logging
from shared.redis_client import get_redis
from shared.schemas import OddsEvent

logger = setup_logging("scraper_manager")


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
        self._completed_match_ids: set[str] = set()  # matches with results
        self._quality_stats = {"stored": 0, "warnings": 0, "rejected": 0}
        self._result_detector = LotusResultDetector()

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
        await page.goto(
            self.settings.scraper.betting_site_url,
            wait_until="domcontentloaded",
            timeout=self.settings.scraper.session_timeout,
        )

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
        """Load scrape-approved match IDs from DB into memory cache."""
        try:
            from sqlalchemy import select
            from backend.models.match_status import MatchTrainingStatus
            async with get_session() as session:
                result = await session.execute(
                    select(MatchTrainingStatus.match_id).where(
                        MatchTrainingStatus.scrape_status == "scrape_approved"
                    )
                )
                ids = {row[0] for row in result.fetchall()}
                self._scrape_approved_ids = ids
                logger.info("scrape_approved_loaded", count=len(ids))
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
            pubsub = await redis.subscribe(CHANNEL_MATCH_RESULTS)
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                try:
                    import json
                    data = json.loads(message["data"])
                    match_id = data.get("match_id", "")
                    if match_id:
                        self._completed_match_ids.add(match_id)
                        logger.info("match_completed_via_result",
                                    match_id=match_id)
                except Exception as e:
                    logger.warning("result_listener_parse_error", error=str(e))
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

        for event in events:
            # Match filter: skip non-qualifying matches
            if not self._match_filter.qualifies(event):
                continue

            accepted_count += 1
            current_batch_ids.add(event.match_id)

            # Track active matches (always, for the frontend listing)
            self._active_matches[event.match_id] = {
                "match_id": event.match_id,
                "team_home": event.team_home,
                "team_away": event.team_away,
                "competition": event.competition,
                "is_live": event.is_live,
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

            # Register first-seen matches as "discovered" (no tick storage yet)
            if event.match_id not in self._seen_match_ids:
                self._seen_match_ids.add(event.match_id)
                await self._upsert_training_status(event)

            # ── SCRAPE GATE: only collect data for approved matches ──
            if event.match_id not in self._scrape_approved_ids:
                continue

            # ── LIVE GATE: only collect ticks during live matches ──
            # Approved but not-yet-live matches wait; completed matches stop.
            if event.match_id in self._completed_match_ids:
                continue
            if not event.is_live:
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

            # Publish to Redis for live consumers (AFTER quality gates)
            event_data = event.model_dump(mode="json")
            await redis.publish_event(CHANNEL_MATCH_EVENTS, event_data)

            # Store to DB
            self._quality_stats["stored"] += 1
            await self._store_odds_tick(event)

            # Derive and publish MatchContext from LotusBook data
            context = self._live_match_tracker.update(event)
            if context is not None:
                await self._publish_match_context(redis, context)
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
        # and remove matches not seen for >2 hours (likely completed/removed)
        stale_ids = []
        for mid, mdata in self._active_matches.items():
            if mid not in current_batch_ids:
                # Match was not in this scrape — mark as not live
                if mdata.get("is_live"):
                    mdata["is_live"] = False
                # Check if stale (not seen for >2 hours) — remove entirely
                try:
                    last = datetime.fromisoformat(mdata["last_update"])
                    if (now - last).total_seconds() > 7200:
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

    async def _publish_match_context(self, redis: Any, context: Any) -> None:
        """Publish derived MatchContext to Redis and store to DB.

        Context is derived inline from LotusBook score_text with zero
        latency gap vs odds data.
        """
        from backend.models.match import MatchContextRecord

        try:
            ctx_data = context.model_dump(mode="json")

            # Publish to Redis pub/sub (consumed by LiveTradingLoop, FeaturePipeline)
            await redis.publish_event(CHANNEL_MATCH_CONTEXT, ctx_data)

            # Cache in Redis (consumed by context_resolver.get_match_context)
            key = KEY_MATCH_CONTEXT.format(match_id=context.match_id)
            await redis.set_json(key, ctx_data, ttl=60)

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

    async def _store_odds_tick(self, event: OddsEvent) -> None:
        """Store an odds event to TimescaleDB with volume + context data."""
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
        except Exception as e:
            logger.error("db_store_error", match_id=event.match_id, error=str(e))

    async def _upsert_training_status(self, event: OddsEvent) -> None:
        """Insert match_training_status for a newly discovered match.

        Two-phase approval:
        1. scrape_status: 'discovered' (default) or 'scrape_approved' (auto or manual)
        2. training_status: 'pending' until user reviews collected data

        When auto_approve_matches is True, ICC/international/franchise matches
        get scrape_approved automatically. Otherwise all start as 'discovered'.
        """
        from backend.models.match_status import MatchTrainingStatus

        category = self._category_classifier.classify(
            competition=event.competition,
            team_home=event.team_home,
            team_away=event.team_away,
        )
        if self.settings.scraper.auto_approve_matches:
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
                    # Already tracked — but update scrape cache if approved
                    if record.scrape_status == "scrape_approved":
                        self._scrape_approved_ids.add(event.match_id)
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
