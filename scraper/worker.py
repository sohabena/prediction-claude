"""
Scraper worker: manages a Playwright page for scraping LotusBook cricket odds.
Uses DOM polling with optional WebSocket interception.

Tailored to LotusBook's Tailwind CSS grid layout:
  - .eventHeadName containers group matches by competition
  - Match links use a[href*="/cricket/"][href*="vs"] (site dropped id=inPlayTeamName)
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
LOTUSBOOK_EXTRACT_JS = r"""
() => {
    const results = [];

    // Match links: historically a#inPlayTeamName; current LotusBook uses plain <a> with
    // href /cricket/{Team%20vs%20Team}/... and no id (verified Mar 2026).
    const matchLinks = document.querySelectorAll(
        'a[href*="/cricket/"][href*="vs"]'
    );

    for (const link of matchLinks) {
        try {
            const href = link.getAttribute('href') || '';

            // Skip competition-level links (no "vs" in href)
            if (!href.includes('vs')) continue;

            // --- Team names ---
            let teams = [];
            const teamSpans = link.querySelectorAll(
                'span[class*="text_color_primary"], span[class*="text-text_color_primary"]'
            );
            for (const span of teamSpans) {
                const t = span.textContent.trim();
                // Exclude the date/time span (primary1) and LIVE badge
                if (t && t.length > 1 && !/^LIVE$/i.test(t) && !span.className.includes('primary1')) {
                    teams.push(t);
                }
            }

            function parseTeamsFromHref(hrefStr) {
                try {
                    const m = hrefStr.match(/\/cricket\/([^/?#]+)/i);
                    if (!m) return [];
                    const seg = decodeURIComponent(m[1].replace(/\+/g, ' '));
                    const parts = seg.split(/\s+vs\s+|-vs-/i);
                    if (parts.length >= 2) {
                        return [parts[0].trim(), parts[1].trim()];
                    }
                } catch (e) {}
                return [];
            }
            function parseTeamsFromLinkText(el) {
                const raw = (el.textContent || '').trim().replace(/^LIVE\s*/i, '').trim();
                const parts = raw.split(/\s+vs\s+|-vs-/i);
                if (parts.length >= 2) {
                    return [parts[0].trim(), parts[1].trim()];
                }
                return [];
            }
            if (teams.length < 2) {
                const fromHref = parseTeamsFromHref(href);
                if (fromHref.length >= 2) teams = fromHref;
            }
            if (teams.length < 2) {
                const fromText = parseTeamsFromLinkText(link);
                if (fromText.length >= 2) teams = fromText;
            }
            if (teams.length < 2) continue;

            // --- Match ID from URL ---
            const urlParts = href.split('/');
            const lotusId = urlParts[urlParts.length - 1] || '';

            // --- Live / time detection ---
            let isLive = false;
            let timeText = '';
            let scoreText = '';
            let scheduledTime = '';

            // 1. Check for scheduled time in primary1 span
            const scheduledTimeEl = link.querySelector('span[class*="primary1"]');
            if (scheduledTimeEl) {
                const raw = scheduledTimeEl.textContent.trim();
                if (/today|tomorrow|\d{1,2}[-/]\d{1,2}/i.test(raw)) {
                    scheduledTime = raw;
                    isLive = false;
                }
            }

            // 2. Check for live indicators in sibling #inPlayTime
            const rowDiv = link.parentElement;
            if (rowDiv) {
                const timeEl = rowDiv.querySelector('#inPlayTime, [id="inPlayTime"]');
                if (timeEl) {
                    const raw = timeEl.textContent.trim();
                    if (raw.includes('LIVE') || timeEl.querySelector('#inPlay')) {
                        isLive = true;
                        timeText = raw;
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
                const scorePattern = /(\d{1,3})\/(\d{1,2})(?:\s*\(?([\d.]+)\)?)?/;
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

            // --- LIVE: "LIVE" prefix on the team link (some builds omit #inPlayTime here) ---
            const linkTextFull = (link.textContent || '').trim();
            if (/^LIVE/i.test(linkTextFull)) {
                isLive = true;
            }

            // --- Competition name ---
            // Walk up to the .eventHeadName parent to find competition
            let competition = '';
            let container = link.closest('.eventHeadName');
            if (container) {
                // The first link in the container that has a competition icon
                const compLink = container.querySelector(
                    'a[href*="/cricket/"]:not([href*="vs"])'
                );
                if (compLink) {
                    // Clean: remove emoji prefix
                    competition = compLink.textContent.trim().replace(/^[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]+/u, '').trim();
                }
                if (!competition) {
                    const ct = (container.textContent || '').substring(0, 1500);
                    if (/Indian Premier League|Tata\s*IPL|\bIPL\s*20/i.test(ct)) {
                        competition = 'Indian Premier League';
                    }
                }
            }

            // --- Odds extraction ---
            // Odds usually sit in the next sibling of the match row; if the <a> is nested
            // deeper, walk up until we find a sibling that contains back/lay cells.
            let oddsDiv = null;
            if (rowDiv && rowDiv.nextElementSibling) {
                const cand = rowDiv.nextElementSibling;
                if (cand.querySelector('[class*="backBtnBg"], [class*="layBtnBg"]')) {
                    oddsDiv = cand;
                }
            }
            if (!oddsDiv) {
                let w = link;
                for (let depth = 0; depth < 12 && w; depth++) {
                    const sib = w.nextElementSibling;
                    if (sib && sib.querySelector &&
                        sib.querySelector('[class*="backBtnBg"], [class*="layBtnBg"]')) {
                        oddsDiv = sib;
                        break;
                    }
                    w = w.parentElement;
                }
            }

            let back_home = null, lay_home = null;
            let back_draw = null, lay_draw = null;
            let back_away = null, lay_away = null;
            let volume_back_home = null, volume_lay_home = null;
            let volume_back_away = null, volume_lay_away = null;

            if (oddsDiv) {

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

                // Safest parsing: split backs and lays explicitly
                const backs = Array.from(oddsDiv.querySelectorAll('[class*="backBtnBg"]'));
                const lays = Array.from(oddsDiv.querySelectorAll('[class*="layBtnBg"]'));

                if (backs.length > 0 && lays.length > 0) {
                    let rCount = 2; // Default to Home, Away
                    let cCount = 1; // Default to 1 col per runner
                    
                    // Heuristics for LotusBook layouts
                    if (backs.length === 3 || backs.length === 9) rCount = 3;
                    if (backs.length === 6 || backs.length === 9) cCount = 3;
                    if (backs.length >= 12) { rCount = 2; cCount = 3; } // Assume 2 teams, 3 cols, ignore rest
                    
                    let hIdx = 0;
                    let aIdx = rCount === 3 ? 2 : 1; // Away is last if 3 runners
                    let dIdx = rCount === 3 ? 1 : -1; // Draw is middle if 3 runners
                    
                    // Helper to safely get from array
                    function getCellSafely(arr, idx) {
                        return idx >= 0 && idx < arr.length ? parseCell(arr[idx]) : {odds: null, volume: null};
                    }
                    
                    // In Backs: Best odds is the right-most cell (index cCount-1)
                    // In Lays: Best odds is the left-most cell (index 0)
                    const homeB = getCellSafely(backs, hIdx * cCount + cCount - 1);
                    const homeL = getCellSafely(lays, hIdx * cCount);
                    back_home = homeB.odds; volume_back_home = homeB.volume;
                    lay_home = homeL.odds; volume_lay_home = homeL.volume;
                    
                    const awayB = getCellSafely(backs, aIdx * cCount + cCount - 1);
                    const awayL = getCellSafely(lays, aIdx * cCount);
                    back_away = awayB.odds; volume_back_away = awayB.volume;
                    lay_away = awayL.odds; volume_lay_away = awayL.volume;
                    
                    if (dIdx !== -1) {
                        const drawB = getCellSafely(backs, dIdx * cCount + cCount - 1);
                        const drawL = getCellSafely(lays, dIdx * cCount);
                        back_draw = drawB.odds;
                        lay_draw = drawL.odds;
                    }
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
