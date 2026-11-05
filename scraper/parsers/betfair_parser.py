"""
Betfair Exchange REST client: read-only market data (secondary price source).

Reads the cricket match-odds catalogue (``listMarketCatalogue``) and the best
prices with the depth used by the strategy (``listMarketBook`` with
``EX_BEST_OFFERS`` and ``EX_TRADED``). The client only reads prices. Its
exchange close is the reference for closing line value (CLV).

Credentials come from the ``BETFAIR_APP_KEY`` and ``BETFAIR_SESSION_TOKEN``
environment variables. They are sent only as request headers, never logged and
never written to disk.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

import httpx

from shared.constants import VALID_ODDS_MAX, VALID_ODDS_MIN
from shared.logging import setup_logging
from shared.schemas import OddsEvent

logger = setup_logging("betfair_parser")

BETFAIR_API_URL = "https://api.betfair.com/exchange/betting/rest/v1.0"
CRICKET_EVENT_TYPE_ID = "4"  # Betfair event type ID for cricket
MATCH_ODDS_CODE = "MATCH_ODDS"
BEST_PRICES_DEPTH = 3
MARKET_BOOK_CHUNK = 25  # market ids per listMarketBook call

APP_KEY_ENV = "BETFAIR_APP_KEY"
SESSION_TOKEN_ENV = "BETFAIR_SESSION_TOKEN"

_DRAW_NAME_RE = re.compile(r"^(the\s+)?draw$", re.IGNORECASE)


def betfair_match_id(market_id: str) -> str:
    """Match id used for Betfair ticks: ``bf_<market_id>`` with dots replaced (S35 contract)."""
    return f"bf_{str(market_id).replace('.', '_')}"


def _valid_price(value: Any) -> Optional[float]:
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return round(price, 3) if VALID_ODDS_MIN <= price <= VALID_ODDS_MAX else None


def _as_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def odds_event_from_book(catalogue: dict[str, Any], book: dict[str, Any]) -> Optional[OddsEvent]:
    """Build an OddsEvent from one catalogue entry and its market book (best offers only)."""
    market_id = str(catalogue.get("marketId") or book.get("marketId") or "")
    if not market_id:
        return None

    runners = catalogue.get("runners") or []
    draw_sel = None
    others: list[dict[str, Any]] = []
    for runner in runners:
        if draw_sel is None and _DRAW_NAME_RE.match(str(runner.get("runnerName", "")).strip()):
            draw_sel = runner.get("selectionId")
        else:
            others.append(runner)
    if len(others) < 2:
        return None
    home, away = others[0], others[1]
    team_home = str(home.get("runnerName") or "").strip()
    team_away = str(away.get("runnerName") or "").strip()
    if not team_home or not team_away:
        return None

    book_runners = {r.get("selectionId"): r for r in (book.get("runners") or [])}

    def best(selection_id: Any, side: str) -> tuple[Optional[float], Optional[float]]:
        runner = book_runners.get(selection_id)
        if runner is None:
            return None, None
        levels = (runner.get("ex") or {}).get(side) or []
        if not levels:
            return None, None
        return _valid_price(levels[0].get("price")), _as_float(levels[0].get("size"))

    home_sel = home.get("selectionId")
    away_sel = away.get("selectionId")
    back_home, vol_back_home = best(home_sel, "availableToBack")
    lay_home, vol_lay_home = best(home_sel, "availableToLay")
    back_away, vol_back_away = best(away_sel, "availableToBack")
    lay_away, vol_lay_away = best(away_sel, "availableToLay")
    back_draw, _ = best(draw_sel, "availableToBack") if draw_sel is not None else (None, None)
    lay_draw, _ = best(draw_sel, "availableToLay") if draw_sel is not None else (None, None)

    competition = (catalogue.get("competition") or {}).get("name") or (catalogue.get("event") or {}).get("name", "")
    return OddsEvent(
        match_id=betfair_match_id(market_id),
        timestamp=datetime.now(timezone.utc),
        team_home=team_home,
        team_away=team_away,
        competition=competition or "",
        back_home=back_home,
        lay_home=lay_home,
        back_draw=back_draw,
        lay_draw=lay_draw,
        back_away=back_away,
        lay_away=lay_away,
        volume_back_home=vol_back_home,
        volume_lay_home=vol_lay_home,
        volume_back_away=vol_back_away,
        volume_lay_away=vol_lay_away,
        is_live=bool(book.get("inplay", False)),
        scheduled_time=_parse_iso(catalogue.get("marketStartTime")),
        source="betfair",
        scrape_method="rest",
    )


class BetfairParser:
    """
    Read-only secondary odds source via the Betfair Exchange REST API.

    Usage:
        parser = BetfairParser.from_env()
        events = await parser.fetch_live_cricket_odds()
    """

    def __init__(
        self,
        app_key: str = "",
        session_token: str = "",
        enabled: bool = False,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.app_key = app_key
        self.session_token = session_token
        self.enabled = enabled
        self._client = client

    @classmethod
    def from_env(cls, enabled: bool = True) -> "BetfairParser":
        """Read credentials from the environment; disabled when either value is missing."""
        app_key = os.environ.get(APP_KEY_ENV, "")
        session_token = os.environ.get(SESSION_TOKEN_ENV, "")
        return cls(
            app_key=app_key,
            session_token=session_token,
            enabled=enabled and bool(app_key and session_token),
        )

    def __repr__(self) -> str:
        return f"BetfairParser(enabled={self.enabled}, app_key=***, session_token=***)"

    @property
    def configured(self) -> bool:
        return bool(self.app_key and self.session_token)

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Create or return the HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=10.0)
        return self._client

    def _headers(self) -> dict[str, str]:
        return {
            "X-Application": self.app_key,
            "X-Authentication": self.session_token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _post(self, endpoint: str, body: dict[str, Any]) -> Any:
        """POST to one read-only REST endpoint; returns the decoded JSON or None on HTTP error."""
        client = await self._ensure_client()
        response = await client.post(f"{BETFAIR_API_URL}/{endpoint}/", json=body, headers=self._headers())
        if response.status_code != 200:
            logger.warning("betfair_rest_error", endpoint=endpoint, status=response.status_code)
            return None
        return response.json()

    async def list_match_odds_catalogue(self, max_results: int = 200) -> list[dict[str, Any]]:
        """Cricket MATCH_ODDS markets (listMarketCatalogue)."""
        if not self.enabled or not self.configured:
            return []
        body = {
            "filter": {"eventTypeIds": [CRICKET_EVENT_TYPE_ID], "marketTypeCodes": [MATCH_ODDS_CODE]},
            "maxResults": max_results,
            "marketProjection": ["RUNNER_DESCRIPTION", "EVENT", "COMPETITION", "MARKET_START_TIME"],
        }
        data = await self._post("listMarketCatalogue", body)
        return list(data) if isinstance(data, list) else []

    async def list_market_books(self, market_ids: Sequence[str]) -> list[dict[str, Any]]:
        """Best offers (depth 3) and traded volume for the given markets (listMarketBook)."""
        if not self.enabled or not self.configured or not market_ids:
            return []
        books: list[dict[str, Any]] = []
        ids = list(market_ids)
        for start in range(0, len(ids), MARKET_BOOK_CHUNK):
            body = {
                "marketIds": ids[start:start + MARKET_BOOK_CHUNK],
                "priceProjection": {
                    "priceData": ["EX_BEST_OFFERS", "EX_TRADED"],
                    "exBestOffersOverrides": {"bestPricesDepth": BEST_PRICES_DEPTH},
                },
            }
            data = await self._post("listMarketBook", body)
            if isinstance(data, list):
                books.extend(data)
        return books

    async def fetch_live_cricket_odds(self) -> list[OddsEvent]:
        """Return OddsEvents for cricket match-odds markets that are in play now."""
        if not self.enabled or not self.configured:
            return []
        try:
            catalogue = await self.list_match_odds_catalogue()
            by_id = {str(c["marketId"]): c for c in catalogue if c.get("marketId")}
            books = await self.list_market_books(list(by_id))
            events: list[OddsEvent] = []
            for book in books:
                if not book.get("inplay"):
                    continue
                catalogue_entry = by_id.get(str(book.get("marketId")))
                if catalogue_entry is None:
                    continue
                event = odds_event_from_book(catalogue_entry, book)
                if event is not None:
                    events.append(event)
            logger.info("betfair_fetched", events=len(events))
            return events
        except Exception as e:  # never let the secondary source break the scraper
            logger.error("betfair_fetch_error", error=type(e).__name__)
            return []

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
