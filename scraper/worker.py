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

            // 1. Check for explicit inPlay badge inside link or child elements
            const inPlayEl = link.querySelector('#inPlay, [id="inPlay"], [class*="inPlay"], [class*="in_play"]');
            if (inPlayEl) {
                isLive = true;
                timeText = inPlayEl.textContent.trim();
            } else if (/LIVE/i.test(link.textContent || '')) {
                isLive = true;
            }

            // 2. Check for scheduled time in spans (e.g. "Today at 10:00 PM", "Tomorrow 12:15 PM")
            const spans = link.querySelectorAll('span');
            for (const span of spans) {
                const raw = span.textContent.trim();
                if (/today|tomorrow|\d{1,2}[-/]\d{1,2}/i.test(raw)) {
                    scheduledTime = raw;
                    break;
                }
            }

            // If scheduled time exists and no inPlay badge, it's upcoming (not live)
            if (scheduledTime && !inPlayEl) {
                isLive = false;
            }

            // 3. Score extraction (e.g. 142/3 (18.2))
            const scorePattern = /(\d{1,3})\/(\d{1,2})(?:\s*\(?([\d.]+)\)?)?/;
            for (const span of spans) {
                const t = span.textContent.trim();
                if (scorePattern.test(t)) {
                    scoreText = t;
                    isLive = true;
                    break;
                }
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
            // On lotusbook.cc, odds sit directly in link.nextElementSibling (col-span-6 lg:col-span-7 div).
            let oddsDiv = link.nextElementSibling;
            if (!oddsDiv || !oddsDiv.querySelector('button, [class*="backBtnBg"], [class*="layBtnBg"]')) {
                let w = link;
                oddsDiv = null;
                for (let depth = 0; depth < 10 && w; depth++) {
                    const sib = w.nextElementSibling;
                    if (sib && sib.querySelector &&
                        sib.querySelector('button[data-odd-type], [class*="backBtnBg"], [class*="layBtnBg"]')) {
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
                function parseVolume(txt) {
                    if (!txt) return null;
                    const cleaned = txt.replace(/[₹$,\s]/g, '').trim();
                    if (!cleaned || cleaned === '-') return null;
                    const abbrevMatch = cleaned.match(/^([\d.]+)\s*(K|L|Lac|Cr|M)?$/i);
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

                function parseBtn(btn) {
                    if (!btn) return { odds: null, volume: null };
                    // 1. Try explicit data attributes (lotusbook.cc)
                    const dataPrice = btn.getAttribute('data-price');
                    const dataSize = btn.getAttribute('data-size');
                    if (dataPrice !== null && dataPrice !== undefined) {
                        let odds = parseFloat(dataPrice);
                        let vol = dataSize ? parseFloat(dataSize) : null;
                        if (!isNaN(odds) && odds > 0) {
                            return { odds: odds, volume: (!isNaN(vol) && vol > 0) ? vol : null };
                        }
                    }
                    // 2. Fallback: Parse spans or text
                    const spans = btn.querySelectorAll('span');
                    let odds = null;
                    let volume = null;
                    for (const span of spans) {
                        const txt = span.textContent.trim();
                        if (!txt || txt === '-') continue;
                        const num = parseFloat(txt.replace(/,/g, ''));
                        if (!isNaN(num) && num > 0) {
                            if (odds === null) odds = num;
                            else if (volume === null) volume = parseVolume(txt);
                        }
                    }
                    if (odds === null) {
                        const fullText = btn.textContent.trim();
                        const match = fullText.match(/^([\d.]+)/);
                        if (match) {
                            const val = parseFloat(match[1]);
                            if (!isNaN(val) && val > 0) odds = val;
                        }
                    }
                    return { odds, volume };
                }

                // Check buttons inside oddsDiv
                const buttons = Array.from(oddsDiv.querySelectorAll('button[data-odd-type]'));
                if (buttons.length >= 6) {
                    // Modern lotusbook.cc grid:
                    // buttons[0..2] = Back Home, Back Draw, Back Away
                    // buttons[3..5] = Lay Home, Lay Draw, Lay Away
                    const bH = parseBtn(buttons[0]);
                    const bD = parseBtn(buttons[1]);
                    const bA = parseBtn(buttons[2]);
                    const lH = parseBtn(buttons[3]);
                    const lD = parseBtn(buttons[4]);
                    const lA = parseBtn(buttons[5]);

                    back_home = bH.odds; volume_back_home = bH.volume;
                    back_draw = bD.odds;
                    back_away = bA.odds; volume_back_away = bA.volume;

                    lay_home = lH.odds; volume_lay_home = lH.volume;
                    lay_draw = lD.odds;
                    lay_away = lA.odds; volume_lay_away = lA.volume;
                } else {
                    // Fallback to class-based query
                    const backs = Array.from(oddsDiv.querySelectorAll('[class*="backBtnBg"]'));
                    const lays = Array.from(oddsDiv.querySelectorAll('[class*="layBtnBg"]'));
                    if (backs.length > 0 && lays.length > 0) {
                        const homeB = parseBtn(backs[0]);
                        const homeL = parseBtn(lays[0]);
                        back_home = homeB.odds; volume_back_home = homeB.volume;
                        lay_home = homeL.odds; volume_lay_home = homeL.volume;

                        const awayB = parseBtn(backs[backs.length - 1]);
                        const awayL = parseBtn(lays[lays.length - 1]);
                        back_away = awayB.odds; volume_back_away = awayB.volume;
                        lay_away = awayL.odds; volume_lay_away = awayL.volume;

                        if (backs.length >= 3 && lays.length >= 3) {
                            const drawB = parseBtn(backs[1]);
                            const drawL = parseBtn(lays[1]);
                            back_draw = drawB.odds;
                            lay_draw = drawL.odds;
                        }
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
