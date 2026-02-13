"""
Scraper worker: manages a Playwright page for scraping LotusBook cricket odds.
Uses DOM polling with optional WebSocket interception.

Tailored to LotusBook's Tailwind CSS grid layout:
  - .eventHeadName containers group matches by competition
  - a#inPlayTeamName links contain team names
  - Odds in sibling col-span-6 divs with back/lay cells
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any
from urllib.parse import unquote

from playwright.async_api import Page, Route

from scraper.parsers.lotusbook_parser import LotusBookParser
from shared.logging import setup_logging
from shared.schemas import OddsEvent

logger = setup_logging("scraper_worker")

# JavaScript to extract all match data from LotusBook's cricket page.
# This runs in the browser context and returns structured match data.
LOTUSBOOK_EXTRACT_JS = """
() => {
    const results = [];

    // Find every match link on the cricket page.
    // Each match is an <a id="inPlayTeamName"> with href containing "vs".
    const matchLinks = document.querySelectorAll('a#inPlayTeamName');

    for (const link of matchLinks) {
        try {
            const href = link.getAttribute('href') || '';

            // Skip competition-level links (no "vs" in href)
            if (!href.includes('vs')) continue;

            // --- Team names ---
            // Team names are in span.text-text_color_primary1 with direct text
            const teamSpans = link.querySelectorAll(
                'span[class*="text-text_color_primary1"]'
            );
            const teams = [];
            for (const span of teamSpans) {
                const t = span.textContent.trim();
                if (t && t.length > 1) teams.push(t);
            }
            if (teams.length < 2) continue;

            // --- Match ID from URL ---
            // URL format: /cricket/{name}%20vs%20{name}/{type}/{matchId}
            const urlParts = href.split('/');
            const lotusId = urlParts[urlParts.length - 1] || '';

            // --- Live / time detection ---
            // The parent row div contains an #inPlayTime span sibling
            const rowDiv = link.parentElement;  // col-span-6 grid grid-cols-7
            let isLive = false;
            let timeText = '';
            let scoreText = '';
            let scheduledTime = '';

            if (rowDiv) {
                const timeEl = rowDiv.querySelector('#inPlayTime, [id="inPlayTime"]');
                if (timeEl) {
                    const raw = timeEl.textContent.trim();
                    timeText = raw;

                    // === PRIORITY 1: Definitive LIVE indicators ===
                    // These ALWAYS win, even if the text also contains a date.
                    // On LotusBook, #inPlayTime textContent can include both
                    // a date AND a nested #inPlay "LIVE" badge, so we must
                    // check live signals BEFORE scheduled patterns.

                    // Check for #inPlay LIVE badge element
                    let hasLiveBadge = false;
                    const liveEl = timeEl.querySelector('#inPlay, [id="inPlay"]');
                    if (liveEl) {
                        const liveText = liveEl.textContent.trim().toUpperCase();
                        if (liveText.includes('LIVE') || liveEl.querySelector('[class*="animate"]') || liveEl.querySelector('[class*="pulse"]') || liveEl.querySelector('[class*="blink"]')) {
                            hasLiveBadge = true;
                        }
                    }

                    // Score pattern like "45/2" or "123/5" — definitive live signal
                    const hasScorePattern = /\\d+\\/\\d/.test(raw);
                    if (hasScorePattern) {
                        scoreText = raw;
                    }

                    if (hasLiveBadge || hasScorePattern) {
                        // Definitive live signal found — mark live regardless of date text
                        isLive = true;
                    } else {
                        // === PRIORITY 2: Section-aware detection (fallback) ===
                        // No live signal found — check if scheduled/upcoming.

                        // Walk up DOM to find section header ("In Play" vs "Upcoming Events")
                        let inUpcomingSection = false;
                        let ancestor = link.closest('.eventHeadName') || link.parentElement;
                        while (ancestor && ancestor !== document.body) {
                            const headerText = ancestor.textContent || '';
                            if (/upcoming\\s*events?/i.test(headerText.substring(0, 200))) {
                                const sectionHeaders = ancestor.querySelectorAll('h1, h2, h3, h4, h5, h6, [class*="header"], [class*="title"], [class*="heading"]');
                                for (const hdr of sectionHeaders) {
                                    if (/upcoming/i.test(hdr.textContent)) {
                                        inUpcomingSection = true;
                                        break;
                                    }
                                }
                                if (!inUpcomingSection) {
                                    const directText = Array.from(ancestor.childNodes)
                                        .filter(n => n.nodeType === 3)
                                        .map(n => n.textContent.trim())
                                        .join('');
                                    if (/upcoming/i.test(directText)) {
                                        inUpcomingSection = true;
                                    }
                                }
                            }
                            if (inUpcomingSection) break;
                            ancestor = ancestor.parentElement;
                        }

                        // Date/time patterns for SCHEDULED matches
                        const scheduledPattern = /tomorrow|\\d{1,2}[-/]\\d{1,2}[-/]\\d{2,4}/i;
                        const hasScheduledTime = scheduledPattern.test(raw);

                        if (inUpcomingSection || hasScheduledTime) {
                            isLive = false;
                            scheduledTime = raw;
                        }
                    }
                }
            }

            // --- Enhanced score extraction ---
            // Search multiple DOM locations for score data if not found above.
            // LotusBook can show scores in various elements near the match row.
            if (!scoreText && rowDiv) {
                // Strategy 1: Look for score-related elements in the match row area
                const searchAreas = [
                    rowDiv,
                    rowDiv.parentElement,
                    link,
                ];
                const scorePattern = /(\\d{1,3})\\/(\\d{1,2})(?:\\s*\\(?([\\d.]+)\\)?)?/;
                for (const area of searchAreas) {
                    if (!area || scoreText) break;
                    // Check all text-containing elements for score patterns
                    const allEls = area.querySelectorAll(
                        'span, div, p, [class*="score"], [class*="Score"], [class*="run"], [class*="inning"]'
                    );
                    for (const el of allEls) {
                        const t = el.textContent.trim();
                        if (scorePattern.test(t) && t.length < 50) {
                            scoreText = t;
                            isLive = true;
                            break;
                        }
                    }
                }
            }

            // --- Competition name ---
            // Walk up to the .eventHeadName parent to find competition
            let competition = '';
            let container = link.closest('.eventHeadName');
            if (container) {
                // The first link in the container that has a competition icon
                const compLink = container.querySelector(
                    'a#inPlayTeamName[href*="/cricket/"]:not([href*="vs"])'
                );
                if (compLink) {
                    // Clean: remove emoji prefix
                    competition = compLink.textContent.trim().replace(/^[\\u{1F300}-\\u{1FAFF}\\u{2600}-\\u{27BF}]+/u, '').trim();
                }
            }

            // --- Odds extraction ---
            // The odds are in the nextElementSibling of the parent row div
            let back_home = null, lay_home = null;
            let back_draw = null, lay_draw = null;
            let back_away = null, lay_away = null;
            let volume_back_home = null, volume_lay_home = null;
            let volume_back_away = null, volume_lay_away = null;

            if (rowDiv && rowDiv.nextElementSibling) {
                const oddsDiv = rowDiv.nextElementSibling;

                // Inside the odds div, find all individual cells.
                // Structure: 3 x col-span-4 groups (Home/1, Draw/X, Away/2)
                // Each col-span-4 has 2 cells (back, lay) with odds + volume spans.
                //
                // The cells have classes like bg-bg_color_backBtnBg / bg-bg_color_layBtnBg
                // OR they might just be divs inside col-span-4 groups.
                //
                // Strategy: Get all leaf-level clickable cells in DOM order.
                // They appear in order: back_1, lay_1, back_X, lay_X, back_2, lay_2
                const cells = oddsDiv.querySelectorAll(
                    '[class*="backBtnBg"], [class*="layBtnBg"]'
                );

                // Parse a cell: first span/div with a decimal number is the odds,
                // second is the volume. Volume may have currency/abbreviation
                // suffixes like '₹1.2L', '50K', '1,200', '$500'.
                function parseVolume(txt) {
                    if (!txt) return null;
                    const cleaned = txt.replace(/[₹$,\\s]/g, '').trim();
                    if (!cleaned || cleaned === '-') return null;
                    // Handle abbreviations: K=1000, L/Lac=100000, Cr=10000000
                    const abbrevMatch = cleaned.match(/^([\\d.]+)\\s*(K|L|Lac|Cr|M)?$/i);
                    if (abbrevMatch) {
                        let val = parseFloat(abbrevMatch[1]);
                        const suffix = (abbrevMatch[2] || '').toUpperCase();
                        if (suffix === 'K') val *= 1000;
                        else if (suffix === 'L' || suffix === 'LAC') val *= 100000;
                        else if (suffix === 'CR') val *= 10000000;
                        else if (suffix === 'M') val *= 1000000;
                        return val;
                    }
                    const num = parseFloat(cleaned);
                    return isNaN(num) ? null : num;
                }

                function parseCell(cell) {
                    if (!cell) return { odds: null, volume: null };
                    const spans = cell.querySelectorAll('span');
                    let odds = null;
                    let volume = null;
                    for (const span of spans) {
                        const txt = span.textContent.trim();
                        if (!txt || txt === '-') continue;
                        const num = parseFloat(txt.replace(/,/g, ''));
                        if (!isNaN(num)) {
                            if (odds === null) {
                                odds = num;
                            } else if (volume === null) {
                                volume = parseVolume(txt);
                            }
                        }
                    }
                    // If spans didn't work, try parsing the full text
                    if (odds === null) {
                        const fullText = cell.textContent.trim();
                        const match = fullText.match(/^([\\d.]+)/);
                        if (match) odds = parseFloat(match[1]);
                    }
                    // If still no volume, try finding it in a deeper child
                    if (volume === null) {
                        const volEls = cell.querySelectorAll(
                            '[class*="vol"], [class*="Vol"], [class*="amount"], [class*="size"]'
                        );
                        for (const ve of volEls) {
                            volume = parseVolume(ve.textContent.trim());
                            if (volume !== null) break;
                        }
                    }
                    return { odds, volume };
                }

                // Map cells: 0=back_1, 1=lay_1, 2=back_X, 3=lay_X, 4=back_2, 5=lay_2
                if (cells.length >= 2) {
                    const c0 = parseCell(cells[0]);
                    const c1 = parseCell(cells[1]);
                    back_home = c0.odds;
                    volume_back_home = c0.volume;
                    lay_home = c1.odds;
                    volume_lay_home = c1.volume;
                }
                if (cells.length >= 4) {
                    const c2 = parseCell(cells[2]);
                    const c3 = parseCell(cells[3]);
                    back_draw = c2.odds;
                    lay_draw = c3.odds;
                }
                if (cells.length >= 6) {
                    const c4 = parseCell(cells[4]);
                    const c5 = parseCell(cells[5]);
                    back_away = c4.odds;
                    volume_back_away = c4.volume;
                    lay_away = c5.odds;
                    volume_lay_away = c5.volume;
                }
                // If only 4 cells (no draw market, common in cricket):
                // cells are: back_1, lay_1, back_2, lay_2
                if (cells.length === 4) {
                    const c2 = parseCell(cells[2]);
                    const c3 = parseCell(cells[3]);
                    back_away = c2.odds;
                    volume_back_away = c2.volume;
                    lay_away = c3.odds;
                    volume_lay_away = c3.volume;
                    back_draw = null;
                    lay_draw = null;
                }
            }

            results.push({
                team_home: teams[0],
                team_away: teams[1],
                competition: competition,
                is_live: isLive,
                lotus_id: lotusId,
                href: href,
                time_text: timeText,
                score_text: scoreText,
                scheduled_time: scheduledTime,
                back_home: back_home,
                lay_home: lay_home,
                back_draw: back_draw,
                lay_draw: lay_draw,
                back_away: back_away,
                lay_away: lay_away,
                volume_back_home: volume_back_home,
                volume_lay_home: volume_lay_home,
                volume_back_away: volume_back_away,
                volume_lay_away: volume_lay_away,
            });
        } catch (e) {
            // Skip this match on error
        }
    }

    return results;
}
"""


class ScraperWorker:
    """
    LotusBook cricket scraper using Playwright + CDP.

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
        Extract match data from LotusBook's DOM using targeted JS evaluation.

        LotusBook uses a Tailwind CSS grid layout:
          - .eventHeadName containers group matches by competition
          - a#inPlayTeamName links hold team names (span.text-text_color_primary1)
          - Odds in the next sibling col-span-6 div with backBtnBg/layBtnBg cells
          - Live indicator via #inPlay element or score pattern in #inPlayTime
        """
        try:
            result = await self.page.evaluate(LOTUSBOOK_EXTRACT_JS)
            if isinstance(result, list):
                if result:
                    logger.debug(
                        "dom_extraction_raw",
                        match_count=len(result),
                        first_match=result[0].get("team_home", "?"),
                    )
                return result
            return []
        except Exception as e:
            logger.error("dom_extraction_error", error=str(e))
            return []

    async def setup_ws_interception(self) -> None:
        """Set up WebSocket interception for real-time data."""

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
