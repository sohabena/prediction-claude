"""
Scraper worker: manages a Playwright page for scraping a single match or match list.
Uses DOM polling with optional WebSocket interception.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from playwright.async_api import Page, Route

from scraper.parsers.lotusbook_parser import LotusBookParser
from shared.logging import setup_logging
from shared.schemas import OddsEvent

logger = setup_logging("scraper_worker")


class ScraperWorker:
    """
    Individual match scraper using Playwright + CDP.

    Two data extraction modes:
    1. WebSocket interception (preferred - lowest latency)
    2. DOM polling fallback (every 3 seconds)
    """

    def __init__(self, page: Page, poll_interval: int = 3) -> None:
        self.page = page
        self.poll_interval = poll_interval
        self.parser = LotusBookParser()
        self._ws_events: list[dict[str, Any]] = []
        self._running = False

    async def scrape_once(self) -> list[OddsEvent]:
        """
        Scrape all visible matches from the current page via DOM extraction.

        Returns:
            List of OddsEvent objects for all visible matches.
        """
        start = time.monotonic()

        try:
            raw_matches = await self._extract_dom_data()
            latency_ms = int((time.monotonic() - start) * 1000)

            for match in raw_matches:
                match["scrape_latency_ms"] = latency_ms

            events = self.parser.parse_dom_data(raw_matches)

            logger.info(
                "scrape_complete",
                match_count=len(events),
                latency_ms=latency_ms,
            )
            return events

        except Exception as e:
            logger.error("scrape_error", error=str(e))
            return []

    async def _extract_dom_data(self) -> list[dict[str, Any]]:
        """
        Extract match data from DOM elements.

        Parses the LotusBook cricket page structure to find
        match cards with team names and 1X2 odds.
        """
        matches: list[dict[str, Any]] = []

        # Try to find match containers - adapt selectors to LotusBook's DOM
        match_cards = await self.page.query_selector_all(
            "[class*='match'], [class*='event'], [class*='game'], "
            "[class*='Market'], [class*='coupon'], tr[class*='match']"
        )

        if not match_cards:
            # Fallback: try broader selectors
            match_cards = await self.page.query_selector_all(
                ".event-row, .match-row, .game-row, "
                "[data-match], [data-event]"
            )

        for card in match_cards:
            try:
                match_data = await self._parse_match_card(card)
                if match_data:
                    matches.append(match_data)
            except Exception as e:
                logger.debug("card_parse_error", error=str(e))

        # If structured selectors fail, try JS-based extraction
        if not matches:
            matches = await self._extract_via_js()

        return matches

    async def _parse_match_card(self, card: Any) -> dict[str, Any] | None:
        """Parse a single match card element into raw data dict."""
        text_content = await card.text_content() or ""

        if not text_content.strip():
            return None

        # Try to find team names
        team_elements = await card.query_selector_all(
            "[class*='team'], [class*='name'], [class*='runner']"
        )
        team_names = []
        for elem in team_elements:
            name = (await elem.text_content() or "").strip()
            if name and len(name) > 1:
                team_names.append(name)

        if len(team_names) < 2:
            return None

        # Try to find odds values
        odds_elements = await card.query_selector_all(
            "[class*='odds'], [class*='price'], [class*='rate'], "
            "[class*='back'], [class*='lay']"
        )
        odds_values: list[float | None] = []
        for elem in odds_elements:
            val_text = (await elem.text_content() or "").strip()
            try:
                odds_values.append(float(val_text))
            except (ValueError, TypeError):
                odds_values.append(None)

        # Check for live indicator
        is_live = "live" in text_content.lower() or "in-play" in text_content.lower()

        # Competition
        comp_elem = await card.query_selector("[class*='competition'], [class*='league'], [class*='tournament']")
        competition = ""
        if comp_elem:
            competition = (await comp_elem.text_content() or "").strip()

        # Map odds values to 1X2 format (expected: back_home, lay_home, back_draw, lay_draw, back_away, lay_away)
        result: dict[str, Any] = {
            "team_home": team_names[0],
            "team_away": team_names[1] if len(team_names) > 1 else "",
            "competition": competition,
            "is_live": is_live,
        }

        odds_fields = ["back_home", "lay_home", "back_draw", "lay_draw", "back_away", "lay_away"]
        for i, field in enumerate(odds_fields):
            result[field] = odds_values[i] if i < len(odds_values) else None

        return result

    async def _extract_via_js(self) -> list[dict[str, Any]]:
        """
        Fallback JS-based extraction.

        Evaluates JavaScript on the page to extract odds data
        that might be stored in JS variables or data attributes.
        """
        try:
            result = await self.page.evaluate("""
                () => {
                    const matches = [];

                    // Try to find odds from page data structures
                    // This needs to be adapted to LotusBook's actual JS structure
                    const rows = document.querySelectorAll(
                        'tr, [class*="event"], [class*="match"], [class*="game"]'
                    );

                    for (const row of rows) {
                        const text = row.textContent || '';
                        // Look for patterns that indicate match data
                        const numbers = text.match(/\\d+\\.\\d{2}/g);
                        if (numbers && numbers.length >= 2) {
                            const teamEls = row.querySelectorAll(
                                '[class*="team"], [class*="name"], [class*="runner"], td'
                            );
                            const teamNames = [];
                            for (const el of teamEls) {
                                const name = el.textContent.trim();
                                if (name && name.length > 2 && !/^\\d/.test(name)) {
                                    teamNames.push(name);
                                }
                            }
                            if (teamNames.length >= 2) {
                                const odds = numbers.map(n => parseFloat(n));
                                matches.push({
                                    team_home: teamNames[0],
                                    team_away: teamNames[1],
                                    is_live: text.toLowerCase().includes('live'),
                                    back_home: odds[0] || null,
                                    lay_home: odds[1] || null,
                                    back_draw: odds[2] || null,
                                    lay_draw: odds[3] || null,
                                    back_away: odds[4] || null,
                                    lay_away: odds[5] || null,
                                });
                            }
                        }
                    }

                    return matches;
                }
            """)
            return result if isinstance(result, list) else []
        except Exception as e:
            logger.error("js_extraction_error", error=str(e))
            return []

    async def setup_ws_interception(self) -> None:
        """Set up WebSocket interception for real-time data."""

        async def handle_ws(route: Route) -> None:
            """Intercept WebSocket frames for odds data."""
            response = await route.fetch()
            body = await response.text()
            logger.debug("ws_frame_intercepted", size=len(body))
            await route.fulfill(response=response)

        # CDP-based WebSocket listening
        cdp = await self.page.context.new_cdp_session(self.page)
        await cdp.send("Network.enable")

        def on_ws_frame(params: dict[str, Any]) -> None:
            """Handle incoming WebSocket frame."""
            payload = params.get("response", {}).get("payloadData", "")
            if payload:
                import json
                try:
                    data = json.loads(payload)
                    self._ws_events.append(data)
                except (json.JSONDecodeError, TypeError):
                    pass

        cdp.on("Network.webSocketFrameReceived", on_ws_frame)
        logger.info("ws_interception_enabled")

    def get_ws_events(self) -> list[OddsEvent]:
        """Process and return any buffered WebSocket events."""
        events: list[OddsEvent] = []
        while self._ws_events:
            frame = self._ws_events.pop(0)
            parsed = self.parser.parse_websocket_data(frame)
            events.extend(parsed)
        return events

    async def poll_loop(self, callback: Any = None) -> None:
        """
        Continuous polling loop for DOM scraping.

        Args:
            callback: Async function called with list of OddsEvent on each tick.
        """
        self._running = True
        logger.info("poll_loop_started", interval=self.poll_interval)

        while self._running:
            try:
                events = await self.scrape_once()

                # Also check WebSocket buffer
                ws_events = self.get_ws_events()
                events.extend(ws_events)

                if events and callback:
                    await callback(events)

            except Exception as e:
                logger.error("poll_error", error=str(e))

            await asyncio.sleep(self.poll_interval)

    def stop(self) -> None:
        """Stop the polling loop."""
        self._running = False
        logger.info("poll_loop_stopped")
