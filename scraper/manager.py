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
from scraper.match_filter import MatchClassifier
from scraper.result_collector import MatchResultCollector
from scraper.worker import ScraperWorker
from shared.config import get_settings
from shared.constants import CHANNEL_MATCH_EVENTS, KEY_ACTIVE_MATCHES
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
        self._seen_match_ids: set[str] = set()
        self._quality_stats = {"stored": 0, "warnings": 0, "rejected": 0}
        self._result_collector = MatchResultCollector(poll_interval=60)

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

        # Start result collector as a background task
        self._result_collector_task = asyncio.create_task(
            self._result_collector.start()
        )

        await self._worker.poll_loop(callback=self._on_events)

    async def _on_events(self, events: list[OddsEvent]) -> None:
        """Handle new odds events: filter, publish to Redis, store to DB, update active matches."""
        if not events:
            return

        redis = await get_redis()
        accepted_count = 0

        for event in events:
            # Match filter: skip non-qualifying matches
            if not self._match_filter.qualifies(event):
                continue

            accepted_count += 1

            # Publish to Redis
            event_data = event.model_dump(mode="json")
            await redis.publish_event(CHANNEL_MATCH_EVENTS, event_data)

            # Track active matches
            self._active_matches[event.match_id] = {
                "match_id": event.match_id,
                "team_home": event.team_home,
                "team_away": event.team_away,
                "competition": event.competition,
                "is_live": event.is_live,
                "last_update": datetime.now(timezone.utc).isoformat(),
            }

            # Tick-level quality validation before storage
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

            # Store to DB
            self._quality_stats["stored"] += 1
            await self._store_odds_tick(event)

            # Auto-approval: insert training status for first-seen matches
            if event.match_id not in self._seen_match_ids:
                self._seen_match_ids.add(event.match_id)
                await self._upsert_training_status(event)

        # Update active matches list in Redis
        await redis.set_json(
            KEY_ACTIVE_MATCHES,
            {"matches": list(self._active_matches.values())},
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

    async def _store_odds_tick(self, event: OddsEvent) -> None:
        """Store an odds event to TimescaleDB."""
        from backend.models.odds import OddsTick

        # Compute derived fields
        ip_home = 1.0 / event.back_home if event.back_home else None
        ip_away = 1.0 / event.back_away if event.back_away else None
        ip_draw = 1.0 / event.back_draw if event.back_draw else None

        overround = None
        if ip_home is not None and ip_away is not None:
            overround = (ip_home or 0) + (ip_away or 0) + (ip_draw or 0) - 1.0

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
                    implied_prob_home=ip_home,
                    implied_prob_away=ip_away,
                    overround=overround,
                    is_live=event.is_live,
                    source=event.source,
                )
                session.add(tick)
        except Exception as e:
            logger.error("db_store_error", match_id=event.match_id, error=str(e))

    async def _upsert_training_status(self, event: OddsEvent) -> None:
        """Insert or update match_training_status for a newly seen match.

        Auto-approves ICC events, international bilaterals, and major franchise
        league matches. Everything else is set to 'pending' for manual review.
        """
        from backend.models.match_status import MatchTrainingStatus

        category = self._category_classifier.classify(
            competition=event.competition,
            team_home=event.team_home,
            team_away=event.team_away,
        )

        auto_tiers = {
            MatchTier.ICC_EVENT,
            MatchTier.INTERNATIONAL_BILATERAL,
            MatchTier.MAJOR_FRANCHISE,
        }
        should_auto = category.tier in auto_tiers
        status = "approved" if should_auto else "pending"

        try:
            async with get_session() as session:
                # Check if already exists (e.g. from a previous run)
                from sqlalchemy import select
                existing = await session.execute(
                    select(MatchTrainingStatus).where(
                        MatchTrainingStatus.match_id == event.match_id
                    )
                )
                if existing.scalar_one_or_none() is not None:
                    return  # Already tracked, don't overwrite manual decisions

                now = datetime.now(timezone.utc)
                record = MatchTrainingStatus(
                    match_id=event.match_id,
                    team_home=event.team_home,
                    team_away=event.team_away,
                    competition=event.competition,
                    training_status=status,
                    auto_approved=should_auto,
                    approved_at=now if should_auto else None,
                )
                session.add(record)

            logger.info(
                "training_status_set",
                match_id=event.match_id,
                status=status,
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

        if self._result_collector:
            self._result_collector.stop()
        if hasattr(self, "_result_collector_task"):
            self._result_collector_task.cancel()
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

    loop = asyncio.get_event_loop()

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
