"""
Betfair Exchange parser: secondary odds data source.

Provides resilience (fallback if LotusBook is down) and cross-book features
(odds divergence between LotusBook and Betfair indicates potential value).

NOTE: Betfair requires API authentication. Set BETFAIR_APP_KEY and
BETFAIR_SESSION_TOKEN environment variables.

This is a structural placeholder with the interface defined.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from shared.logging import setup_logging
from shared.schemas import OddsEvent

logger = setup_logging("betfair_parser")

BETFAIR_API_URL = "https://api.betfair.com/exchange/betting/rest/v1.0"
CRICKET_EVENT_TYPE_ID = "4"  # Betfair event type ID for cricket


class BetfairParser:
    """
    Secondary odds source via Betfair Exchange API.

    Usage:
        parser = BetfairParser(app_key="your_key", session_token="your_token")
        events = await parser.fetch_live_cricket_odds()
    """

    def __init__(
        self,
        app_key: str = "",
        session_token: str = "",
        enabled: bool = False,
    ) -> None:
        self.app_key = app_key
        self.session_token = session_token
        self.enabled = enabled
        self._client: Optional[httpx.AsyncClient] = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Create or return the HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=10.0,
                headers={
                    "X-Application": self.app_key,
                    "X-Authentication": self.session_token,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
        return self._client

    async def fetch_live_cricket_odds(self) -> list[OddsEvent]:
        """
        Fetch live cricket odds from Betfair Exchange.

        Returns a list of OddsEvent objects matching the same schema as LotusBook.
        """
        if not self.enabled or not self.app_key:
            return []

        try:
            client = await self._ensure_client()

            # Step 1: Get live cricket events
            events_resp = await client.post(
                f"{BETFAIR_API_URL}/listEvents/",
                json={
                    "filter": {
                        "eventTypeIds": [CRICKET_EVENT_TYPE_ID],
                        "inPlayOnly": True,
                    },
                },
            )

            if events_resp.status_code != 200:
                logger.warning("betfair_events_error", status=events_resp.status_code)
                return []

            events_data = events_resp.json()
            odds_events: list[OddsEvent] = []

            for event_item in events_data:
                event = event_item.get("event", {})
                event_id = event.get("id", "")

                # Step 2: Get market catalogue for each event
                markets_resp = await client.post(
                    f"{BETFAIR_API_URL}/listMarketCatalogue/",
                    json={
                        "filter": {
                            "eventIds": [event_id],
                            "marketTypeCodes": ["MATCH_ODDS"],
                        },
                        "maxResults": 1,
                        "marketProjection": ["RUNNER_DESCRIPTION"],
                    },
                )

                if markets_resp.status_code != 200:
                    continue

                markets = markets_resp.json()
                if not markets:
                    continue

                market = markets[0]
                market_id = market.get("marketId", "")
                runners = market.get("runners", [])

                if len(runners) < 2:
                    continue

                team_home = runners[0].get("runnerName", "Home")
                team_away = runners[1].get("runnerName", "Away")

                # Step 3: Get market book (prices)
                book_resp = await client.post(
                    f"{BETFAIR_API_URL}/listMarketBook/",
                    json={
                        "marketIds": [market_id],
                        "priceProjection": {
                            "priceData": ["EX_BEST_OFFERS"],
                        },
                    },
                )

                if book_resp.status_code != 200:
                    continue

                books = book_resp.json()
                if not books:
                    continue

                book = books[0]
                book_runners = book.get("runners", [])

                if len(book_runners) < 2:
                    continue

                # Extract best back/lay prices
                home_runner = book_runners[0]
                away_runner = book_runners[1]

                home_back = self._get_best_price(home_runner, "availableToBack")
                home_lay = self._get_best_price(home_runner, "availableToLay")
                away_back = self._get_best_price(away_runner, "availableToBack")
                away_lay = self._get_best_price(away_runner, "availableToLay")

                # Handle draw runner if present
                draw_back = None
                draw_lay = None
                if len(book_runners) > 2:
                    draw_runner = book_runners[2]
                    draw_back = self._get_best_price(draw_runner, "availableToBack")
                    draw_lay = self._get_best_price(draw_runner, "availableToLay")

                odds_event = OddsEvent(
                    match_id=f"bf_{event_id}",
                    timestamp=datetime.now(timezone.utc),
                    team_home=team_home,
                    team_away=team_away,
                    competition=event.get("name", ""),
                    back_home=home_back,
                    lay_home=home_lay,
                    back_draw=draw_back,
                    lay_draw=draw_lay,
                    back_away=away_back,
                    lay_away=away_lay,
                    is_live=book.get("inplay", False),
                    source="betfair",
                )
                odds_events.append(odds_event)

            logger.info("betfair_fetched", events=len(odds_events))
            return odds_events

        except Exception as e:
            logger.error("betfair_fetch_error", error=str(e))
            return []

    def _get_best_price(self, runner: dict[str, Any], side: str) -> Optional[float]:
        """Extract the best price from a Betfair runner's exchange data."""
        ex = runner.get("ex", {})
        prices = ex.get(side, [])
        if prices:
            return float(prices[0].get("price", 0))
        return None

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
