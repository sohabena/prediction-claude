"""
Betfair Exchange Stream client: read-only market data, live ladder and last-traded prices.

Opens the Exchange Stream API, authenticates, and subscribes to the given match-odds
markets (EX_BEST_OFFERS, EX_LTP, EX_TRADED_VOL, EX_MARKET_DEF, three ladder levels).
Each processed price change yields one BetfairTick. The client sends only the
``authentication`` and ``marketSubscription`` operations.

Ladder maintenance and tick extraction are the S35 stream helpers in
``scraper/parsers/betfair_historical.py``, reused by import.

Resume: the last ``clk`` / ``initialClk`` are sent with the subscription after a
reconnect, so the server continues from the same position. Image messages (``img``
or ``SUB_IMAGE``) replace the ladder of the market they carry. Heartbeats count as
liveness. A silent connection is dropped after ``heartbeat_timeout_s``.

Credentials are never logged. Log messages are redacted before they leave the module.
"""

from __future__ import annotations

import asyncio
import json
import re
import ssl
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Awaitable, Callable, Mapping, Optional, Protocol, Sequence

from scraper.parsers.betfair_historical import BetfairHistoricalParser
from scraper.parsers.betfair_parser import betfair_match_id
from shared.logging import setup_logging
from shared.schemas import OddsEvent

logger = setup_logging("betfair_stream")

STREAM_HOST = "stream-api.betfair.com"
STREAM_PORT = 443
MARKET_DATA_FIELDS = ["EX_BEST_OFFERS", "EX_LTP", "EX_TRADED_VOL", "EX_MARKET_DEF"]
LADDER_LEVELS = 3
HEARTBEAT_MS = 5000
HEARTBEAT_TIMEOUT_S = 15.0
AUTH_ERROR_CODES = frozenset(
    {"NO_APP_KEY", "INVALID_APP_KEY", "NO_SESSION", "INVALID_SESSION_INFORMATION", "INVALID_SESSION"}
)
_SEPARATOR_RE = re.compile(r"\s+v(?:s)?\.?\s+", re.IGNORECASE)
_GENERIC_NAMES = frozenset({"Home", "Away"})


class BetfairStreamError(RuntimeError):
    """The stream gave up: retries exhausted or the subscription was refused."""


class BetfairAuthError(BetfairStreamError):
    """The server rejected the app key or session. Not retried."""


class StreamDropped(ConnectionError):
    """The server closed the subscription or reported a retryable failure."""


class StreamConnection(Protocol):
    async def send_line(self, line: str) -> None: ...

    async def read_line(self) -> str: ...

    async def close(self) -> None: ...


class StreamConnector(Protocol):
    async def open(self) -> StreamConnection: ...


class SocketConnection:
    """One TLS connection to the Exchange Stream (CRLF-delimited JSON)."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader = reader
        self._writer = writer

    async def send_line(self, line: str) -> None:
        self._writer.write((line + "\r\n").encode("utf-8"))
        await self._writer.drain()

    async def read_line(self) -> str:
        raw = await self._reader.readline()
        if not raw:
            raise ConnectionError("stream closed by server")
        return raw.decode("utf-8", errors="replace")

    async def close(self) -> None:
        self._writer.close()
        with suppress(Exception):
            await self._writer.wait_closed()


class SocketConnector:
    """Opens the production stream endpoint over TLS."""

    def __init__(self, host: str = STREAM_HOST, port: int = STREAM_PORT) -> None:
        self._host = host
        self._port = port

    async def open(self) -> StreamConnection:
        context = ssl.create_default_context()
        reader, writer = await asyncio.open_connection(self._host, self._port, ssl=context, server_hostname=self._host)
        return SocketConnection(reader, writer)


@dataclass(frozen=True)
class BetfairTick:
    """One processed price change for one market. Closing is signalled by ``closed``."""

    market_id: str
    match_id: str
    timestamp: datetime
    in_play: bool
    market_status: str
    closed: bool
    event: OddsEvent
    levels: dict[str, list[tuple[float, Optional[float]]]]  # back_home, lay_home, ... top 3 levels
    ltp: dict[str, Optional[float]]
    traded_volume: dict[str, Optional[float]]

    def to_payload(self) -> dict[str, Any]:
        """JSON-safe payload for the tick bus: OddsEvent fields plus the extra stream state."""
        payload = self.event.model_dump(mode="json")
        payload.update(
            {
                "market_id": self.market_id,
                "in_play": self.in_play,
                "market_status": self.market_status,
                "closed": self.closed,
                "levels": {k: [[p, s] for p, s in v] for k, v in self.levels.items()},
                "ltp": dict(self.ltp),
                "traded_volume": dict(self.traded_volume),
            }
        )
        return payload


def _to_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _top_levels(book: Mapping[int, tuple[float, Optional[float]]], n: int = LADDER_LEVELS) -> list[tuple[float, Optional[float]]]:
    return [book[level] for level in sorted(book)[:n]]


class BetfairStreamClient:
    """
    Read-only stream subscriber with reconnect, backoff and clk-based resume.

    ``stream()`` is an async generator. Breaking out of it closes the connection.
    """

    def __init__(
        self,
        app_key: str,
        session_token: str,
        market_ids: Sequence[str],
        connector: Optional[StreamConnector] = None,
        *,
        max_attempts: Optional[int] = None,
        base_backoff_s: float = 1.0,
        max_backoff_s: float = 60.0,
        heartbeat_timeout_s: float = HEARTBEAT_TIMEOUT_S,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        extractor: Optional[BetfairHistoricalParser] = None,
    ) -> None:
        if not app_key or not session_token:
            raise BetfairAuthError("Betfair app key and session token are required")
        self._app_key = app_key
        self._session_token = session_token
        self._market_ids = [str(m) for m in market_ids]
        self._connector: StreamConnector = connector or SocketConnector()
        self._max_attempts = max_attempts
        self._base_backoff_s = base_backoff_s
        self._max_backoff_s = max_backoff_s
        self._heartbeat_timeout_s = heartbeat_timeout_s
        self._sleep = sleep
        self._extractor = extractor or BetfairHistoricalParser()

        self._markets: dict[str, dict[str, Any]] = {}
        self._clk: Optional[str] = None
        self._initial_clk: Optional[str] = None
        self._message_id = 0
        self._subscription_id = 0
        self._failures = 0
        self.connections = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def stream(self) -> AsyncIterator[BetfairTick]:
        """Yield ticks forever, reconnecting on drops. Raises BetfairStreamError when giving up."""
        while True:
            conn: Optional[StreamConnection] = None
            try:
                logger.info("betfair_stream_connecting", markets=len(self._market_ids), resume=self._clk is not None)
                conn = await self._connector.open()
                await conn.send_line(json.dumps({
                    "op": "authentication",
                    "id": self._next_id(),
                    "appKey": self._app_key,
                    "session": self._session_token,
                }))
                await conn.send_line(json.dumps(self._subscription_message()))
                self.connections += 1
                while True:
                    raw = await asyncio.wait_for(conn.read_line(), timeout=self._heartbeat_timeout_s)
                    for tick in self._handle_message(raw):
                        yield tick
            except (OSError, EOFError, asyncio.TimeoutError) as exc:
                self._failures += 1
                logger.warning("betfair_stream_disconnected", error=self._redact(f"{type(exc).__name__}: {exc}"))
                if self._max_attempts is not None and self._failures > self._max_attempts:
                    raise BetfairStreamError(f"gave up after {self._failures - 1} reconnect attempts") from None
                delay = min(self._max_backoff_s, self._base_backoff_s * (2 ** (self._failures - 1)))
                logger.info("betfair_stream_reconnect_wait", delay_s=delay, attempt=self._failures)
                await self._sleep(delay)
            finally:
                if conn is not None:
                    await conn.close()

    # ------------------------------------------------------------------
    # Protocol handling
    # ------------------------------------------------------------------

    def _next_id(self) -> int:
        self._message_id += 1
        return self._message_id

    def _subscription_message(self) -> dict[str, Any]:
        self._subscription_id = self._next_id()
        message: dict[str, Any] = {
            "op": "marketSubscription",
            "id": self._subscription_id,
            "marketFilter": {"marketIds": self._market_ids},
            "marketDataFilter": {"fields": MARKET_DATA_FIELDS, "ladderLevels": LADDER_LEVELS},
            "heartbeatMs": HEARTBEAT_MS,
        }
        if self._clk is not None and self._initial_clk is not None:
            message["initialClk"] = self._initial_clk
            message["clk"] = self._clk
        return message

    def _handle_message(self, raw: str) -> list[BetfairTick]:
        try:
            data = json.loads(raw)
        except ValueError:
            logger.warning("betfair_stream_bad_json", size=len(raw))
            return []
        if not isinstance(data, dict):
            return []
        op = data.get("op")
        if op == "status":
            self._on_status(data)
            return []
        if op == "heartbeat" or data.get("ct") == "HEARTBEAT":
            return []
        if op != "mcm":
            return []
        return self._on_market_change(data)

    def _on_status(self, data: dict[str, Any]) -> None:
        if data.get("statusCode") == "SUCCESS":
            if data.get("id") == self._subscription_id:
                self._failures = 0
                logger.info("betfair_stream_subscribed", markets=len(self._market_ids))
            return
        error = str(data.get("errorCode", ""))
        if error in AUTH_ERROR_CODES:
            raise BetfairAuthError(f"authentication rejected: {error}")
        if data.get("connectionClosed"):
            raise StreamDropped(f"server closed the connection: {error}")
        raise StreamDropped(f"subscription failed: {error}")

    def _on_market_change(self, data: dict[str, Any]) -> list[BetfairTick]:
        timestamp = _to_datetime(data.get("pt")) or datetime.now(timezone.utc)
        ticks: list[BetfairTick] = []
        for mc in data.get("mc") or []:
            tick = self._on_market(mc, timestamp, image=data.get("ct") == "SUB_IMAGE")
            if tick is not None:
                ticks.append(tick)
        if data.get("clk") is not None:
            self._clk = str(data["clk"])
        if data.get("initialClk") is not None:
            self._initial_clk = str(data["initialClk"])
        return ticks

    def _on_market(self, mc: dict[str, Any], timestamp: datetime, image: bool) -> Optional[BetfairTick]:
        market_id = mc.get("id")
        if not market_id:
            return None
        market_id = str(market_id)
        state = self._markets.setdefault(
            market_id,
            {"market_id": market_id, "market_def": {}, "runners": {}, "ladder_seen": False, "max_ladder_level": 0},
        )

        if image or mc.get("img"):
            for runner in state["runners"].values():
                runner["book_back"].clear()
                runner["book_lay"].clear()

        market_def = mc.get("marketDefinition")
        rc_list = mc.get("rc") or []
        if not market_def and not rc_list:
            return None

        if market_def:
            state["market_def"].update(market_def)
            for r_def in market_def.get("runners", []):
                r_id = r_def.get("id")
                r_name = r_def.get("name", "")
                if r_id not in state["runners"]:
                    state["runners"][r_id] = self._extractor._new_runner(r_name)
                elif r_name:
                    state["runners"][r_id]["name"] = r_name

        for rc in rc_list:
            r_id = rc.get("id")
            if r_id not in state["runners"]:
                state["runners"][r_id] = self._extractor._new_runner(f"Runner_{r_id}")
            runner = state["runners"][r_id]
            if "batb" in rc:
                self._extractor._apply_ladder_updates(runner["book_back"], rc.get("batb"), state)
            if "batl" in rc:
                self._extractor._apply_ladder_updates(runner["book_lay"], rc.get("batl"), state)
            if rc.get("ltp") is not None:
                runner["ltp"] = self._extractor._validate_odds(rc.get("ltp"))
            if rc.get("tv") is not None:
                runner["tv"] = self._extractor._to_float(rc.get("tv"))

        extracted = self._extractor._extract_stream_tick(state, timestamp)
        if extracted is None:
            return None
        return self._build_tick(state, extracted, timestamp)

    def _build_tick(self, state: dict[str, Any], t: dict[str, Any], timestamp: datetime) -> BetfairTick:
        market_def = state["market_def"]
        runners = state["runners"]
        home_id, away_id, draw_id = self._extractor._runner_roles(state)

        def name_of(r_id: Any, fallback: str) -> str:
            if r_id is None:
                return fallback
            return self._extractor._clean_team_name(runners[r_id].get("name") or fallback)

        team_home = name_of(home_id, "Home")
        team_away = name_of(away_id, "Away")
        event_name = str(market_def.get("eventName") or "")
        if (team_home.startswith("Runner_") or team_home in _GENERIC_NAMES) and _SEPARATOR_RE.search(event_name):
            parts = _SEPARATOR_RE.split(event_name, maxsplit=1)
            team_home = self._extractor._clean_team_name(parts[0])
            team_away = self._extractor._clean_team_name(parts[1])

        in_play = bool(market_def.get("inPlay", False))
        status = str(market_def.get("status") or "")
        event = OddsEvent(
            match_id=betfair_match_id(state["market_id"]),
            timestamp=timestamp,
            team_home=team_home,
            team_away=team_away,
            competition=str(market_def.get("competitionName") or event_name or ""),
            back_home=t["back_home"],
            lay_home=t["lay_home"],
            back_draw=t["back_draw"],
            lay_draw=t["lay_draw"],
            back_away=t["back_away"],
            lay_away=t["lay_away"],
            volume_back_home=t["volume_back_home"],
            volume_lay_home=t["volume_lay_home"],
            volume_back_away=t["volume_back_away"],
            volume_lay_away=t["volume_lay_away"],
            is_live=in_play,
            scheduled_time=_to_datetime(market_def.get("marketTime")),
            source="betfair",
            scrape_method="betfair_stream",
        )

        levels: dict[str, list[tuple[float, Optional[float]]]] = {}
        for label, r_id, side in (
            ("home", home_id, "book_back"), ("home", home_id, "book_lay"),
            ("away", away_id, "book_back"), ("away", away_id, "book_lay"),
            ("draw", draw_id, "book_back"), ("draw", draw_id, "book_lay"),
        ):
            if r_id is None:
                continue
            key = f"{side.replace('book_', '')}_{label}"
            levels[key] = _top_levels(runners[r_id][side])

        return BetfairTick(
            market_id=state["market_id"],
            match_id=event.match_id,
            timestamp=timestamp,
            in_play=in_play,
            market_status=status,
            closed=status == "CLOSED",
            event=event,
            levels=levels,
            ltp={"home": t["ltp_home"], "away": t["ltp_away"], "draw": t["ltp_draw"]},
            traded_volume={"home": t["traded_volume_home"], "away": t["traded_volume_away"]},
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _redact(self, text: str) -> str:
        for secret in (self._app_key, self._session_token):
            if secret:
                text = text.replace(secret, "[redacted]")
        return text
