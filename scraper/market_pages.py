"""Read-only market-page worker for LotusBook Bookmaker and session markets (S47).

Rules enforced here: one page per live recorded match, polled no faster than every 3 s,
read via ``page.content()`` only (no clicks, no form input, no login). Simulated matches
are never started. Records are stored only when their content changes.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import urljoin

from scraper.match_filter import is_simulated_name
from scraper.parsers.lotusbook_markets import MarketRecord, content_fingerprint, parse_market_html
from shared.logging import setup_logging

logger = setup_logging("market_pages")

MIN_POLL_INTERVAL_S = 3.0
MAX_CONSECUTIVE_ERRORS = 5
PAGE_LOAD_TIMEOUT_MS = 60_000

StoreFn = Callable[[str, list[MarketRecord]], Awaitable[None]]


def clamp_poll_interval(interval: float) -> float:
    """Never poll faster than the 3 s politeness floor."""
    return max(MIN_POLL_INTERVAL_S, float(interval))


def eligibility(*, is_live: bool, team_home: str, team_away: str, competition: str = "") -> tuple[bool, str]:
    """Return ``(eligible, reason)``. Simulated (SRL and similar) matches are never eligible."""
    if not is_live:
        return False, "not_live"
    if is_simulated_name(team_home, team_away, competition):
        return False, "simulated"
    return True, "ok"


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


class MarketPageWorker:
    """Polls one match page and hands changed records to ``store``."""

    def __init__(
        self,
        match_id: str,
        page: Any,
        store: StoreFn,
        poll_interval: float = MIN_POLL_INTERVAL_S,
    ) -> None:
        self.match_id = match_id
        self.page = page
        self.store = store
        self.poll_interval = clamp_poll_interval(poll_interval)
        self._last_fingerprint: Optional[str] = None
        self.stored_snapshots = 0

    async def poll_once(self) -> int:
        """Read the page once. Return the number of records stored (0 when unchanged)."""
        html = await self.page.content()
        records = parse_market_html(html, self.match_id, datetime.now(timezone.utc))
        if not records:
            return 0
        fingerprint = content_fingerprint(records)
        if fingerprint == self._last_fingerprint:
            return 0
        self._last_fingerprint = fingerprint
        await self.store(self.match_id, records)
        self.stored_snapshots += 1
        return len(records)

    async def run(self, stop: asyncio.Event) -> None:
        """Poll until ``stop`` is set or too many consecutive errors occur."""
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
