"""
Storage and publishing for Betfair market-data ticks (read-only).

- ``TickPublisher`` publishes ticks to the tick bus (``stream:match_events``) with
  ``source="betfair"``, at most one per market per ``min_interval_s``.
- ``ClosingPriceRecorder`` keeps the last in-play best prices per market and, at
  market close, writes them to ``match_results.closing_*``. ``match_results.source`` is left alone:
  it records where the *result* came from. Price provenance lives in ``odds_ticks.source`` ("betfair"),
  which is what CLV reads (S33 auditor, S42).
  The write is an UPDATE. It does not create result rows, because winner and loser
  are NOT NULL and belong to the result writer. If the result row does not exist yet,
  nothing is written and the next closed tick retries.
- ``run_feed`` connects a stream client to both.

The SQL goes through an injected executor and the bus through an injected client,
so tests use fakes. Nothing here places orders.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Mapping, Optional, Protocol

from scraper.betfair_stream import BetfairStreamClient, BetfairTick
from shared.logging import setup_logging
from shared.redis_client import STREAM_FOR_CHANNEL

logger = setup_logging("betfair_store")

MATCH_EVENTS_STREAM = STREAM_FOR_CHANNEL["match_events"]
DEFAULT_MIN_INTERVAL_S = 3.0  # matches the live scrape cadence used by S35 resampling
SOURCE = "betfair"

CLOSING_UPDATE_SQL = """
UPDATE match_results
   SET closing_back_home = :closing_back_home,
       closing_back_away = :closing_back_away,
       closing_lay_home = :closing_lay_home,
       closing_lay_away = :closing_lay_away
 WHERE match_id = :match_id
"""


class TickBus(Protocol):
    async def xadd_event(self, stream: str, data: dict[str, Any], maxlen: int = ...) -> str: ...


class SqlExecutor(Protocol):
    async def execute(self, sql: str, params: Mapping[str, Any]) -> int:
        """Run one write statement and return the affected row count."""
        ...


class SessionExecutor:
    """SqlExecutor backed by a SQLAlchemy async session factory."""

    def __init__(self, session_factory: Callable[[], Any]) -> None:
        self._session_factory = session_factory

    async def execute(self, sql: str, params: Mapping[str, Any]) -> int:
        from sqlalchemy import text

        async with self._session_factory() as session:
            result = await session.execute(text(sql), dict(params))
            await session.commit()
            return int(result.rowcount or 0)


class TickPublisher:
    """Publishes ticks to the bus, throttled per market."""

    def __init__(
        self,
        bus: TickBus,
        stream: str = MATCH_EVENTS_STREAM,
        min_interval_s: float = DEFAULT_MIN_INTERVAL_S,
    ) -> None:
        self._bus = bus
        self._stream = stream
        self._min_interval_s = min_interval_s
        self._last_published: dict[str, datetime] = {}

    async def publish(self, tick: BetfairTick) -> bool:
        """Publish the tick unless the market was published less than ``min_interval_s`` ago. Closing ticks always go out."""
        last = self._last_published.get(tick.market_id)
        if not tick.closed and last is not None and (tick.timestamp - last).total_seconds() < self._min_interval_s:
            return False
        await self._bus.xadd_event(self._stream, tick.to_payload())
        self._last_published[tick.market_id] = tick.timestamp
        return True


class ClosingPriceRecorder:
    """Records the last in-play best prices of a market as its closing price."""

    def __init__(self, executor: SqlExecutor, resolve_match_id: Callable[[str], Optional[str]]) -> None:
        self._executor = executor
        self._resolve_match_id = resolve_match_id
        self._last_in_play: dict[str, BetfairTick] = {}
        self._recorded: set[str] = set()

    async def observe(self, tick: BetfairTick) -> bool:
        """Track in-play prices; on a closed tick, store the closing price. Returns True when a row was written."""
        if tick.closed:
            return await self._record_close(tick.market_id)
        if tick.in_play:
            self._last_in_play[tick.market_id] = tick
        return False

    async def _record_close(self, market_id: str) -> bool:
        if market_id in self._recorded:
            return False
        last = self._last_in_play.get(market_id)
        if last is None:
            logger.warning("closing_price_missing", market_id=market_id)
            return False
        match_id = self._resolve_match_id(market_id)
        if not match_id:
            logger.warning("closing_price_unlinked", market_id=market_id)
            return False
        params = {
            "match_id": match_id,
            "closing_back_home": last.event.back_home,
            "closing_back_away": last.event.back_away,
            "closing_lay_home": last.event.lay_home,
            "closing_lay_away": last.event.lay_away,
        }
        rows = await self._executor.execute(CLOSING_UPDATE_SQL, params)
        if rows == 0:
            logger.warning("closing_result_row_missing", match_id=match_id)
            return False
        self._recorded.add(market_id)
        logger.info("closing_price_recorded", match_id=match_id, source=SOURCE)
        return True


async def run_feed(
    client: BetfairStreamClient,
    publisher: TickPublisher,
    recorder: ClosingPriceRecorder,
) -> None:
    """Forward every tick to the publisher and the closing-price recorder until the stream ends."""
    async for tick in client.stream():
        await publisher.publish(tick)
        await recorder.observe(tick)
