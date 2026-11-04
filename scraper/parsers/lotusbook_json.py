"""LotusBook's own market JSON -> market records (S56, passive capture).

The match page receives ``GET .../odds/sma-event/events/4/<event_id>`` about every 3 s, and the listing page
receives ``GET .../odds/sma-menu/4`` once. Both are read by the pages' own ``response`` events; this module
never makes a request. It is a pure parser: JSON in, records out.

Shape (verified 2026-11-03, fixtures ``sma_event_*_20261103.json`` and ``sma_menu_cricket_20261103.json``):
- event ``result[]`` = markets: ``id``, ``name``, ``mtype``, ``status`` (OPEN / SUSPENDED / CLOSED),
  ``lastUpdateTime`` (epoch ms), ``event{id, openDate}`` (site time, e.g. "2026-11-03T04:30:00+05:30"),
  ``competition{id, name}``, ``runners[]{id, name, status, back[{price,size}], lay[{price,size}]}``.
  A price of ``0.0`` (or no entry) means no price.
- menu ``result[]`` event types; Cricket has ``id`` "4"; its ``children`` are GROUPs (competition ``name``,
  ``id``) whose ``children`` are EVENTs (``id`` = LotusBook event id).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from scraper.parsers.lotusbook_markets import MarketRecord, _classify_list_market, _list_selection

SOURCE = "lotusbook"
CRICKET_EVENT_TYPE_ID = "4"

EVENT_URL_RE = re.compile(r"/odds/sma-event/events/4/(\d+)(?:[/?#]|$)")
MENU_URL_RE = re.compile(r"/odds/sma-menu/4(?:[/?#]|$)")
WINNER_STATUSES = frozenset({"WINNER", "WON"})


@dataclass(frozen=True)
class EventMeta:
    """Competition and start time of one event, from its market JSON."""

    event_id: str
    competition_id: str
    competition_name: str
    open_date: Optional[datetime]  # UTC


@dataclass(frozen=True)
class Settlement:
    """A settled Winner (match odds) market. ``winner`` is the single WINNER/WON runner name, or None."""

    market_id: str
    winner: Optional[str]
    runner_statuses: tuple[tuple[str, str], ...]  # (runner name, runner status) as seen at close


def event_id_from_url(url: str) -> Optional[str]:
    """LotusBook event id of an ``sma-event`` URL, or None for any other URL."""
    match = EVENT_URL_RE.search(url or "")
    return match.group(1) if match else None


def is_menu_url(url: str) -> bool:
    """True for the ``sma-menu/4`` (cricket menu) URL."""
    return bool(MENU_URL_RE.search(url or ""))


def _price(entry: Any) -> Optional[float]:
    """Price of one back/lay entry; 0 or missing means no price."""
    if not isinstance(entry, dict):
        return None
    try:
        value = float(entry.get("price") or 0)
    except (TypeError, ValueError):
        return None
    return value if value > 1.0 else None


def _best(entries: Any, *, back: bool) -> tuple[Optional[float], Optional[float]]:
    """Best (price, size): highest back, lowest lay, over the entries that carry a price."""
    best_price: Optional[float] = None
    best_size: Optional[float] = None
    for entry in entries or []:
        price = _price(entry)
        if price is None:
            continue
        better = best_price is None or (price > best_price if back else price < best_price)
        if better:
            best_price = price
            size = entry.get("size")
            best_size = float(size) if size is not None else None
    return best_price, best_size


def _utc_from_ms(value: Any) -> Optional[datetime]:
    try:
        return datetime.fromtimestamp(int(value) / 1000.0, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _open_date(text: Any) -> Optional[datetime]:
    """ISO text with its offset ("2026-11-03T04:30:00+05:30") as an aware UTC datetime."""
    if not text or not isinstance(text, str):
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None  # the site always sends an offset; a naive value is not trusted
    return parsed.astimezone(timezone.utc)


def _markets(payload: Any) -> list[dict]:
    result = payload.get("result") if isinstance(payload, dict) else None
    return [m for m in (result or []) if isinstance(m, dict)]


def event_meta(payload: Any) -> Optional[EventMeta]:
    """Competition and start time of the event.

    The start time is the ``openDate`` most markets agree on, not the first market's: the exchange market on a real
    match page can carry its own creation time (2026-11-03, West Indies A v England Lions: one market said
    2026-11-02 13:28:36 UTC while the event starts at 17:00), and the first market's place in the list varies.
    """
    meta: Optional[EventMeta] = None
    votes: dict[datetime, int] = {}
    for market in _markets(payload):
        event = market.get("event") or {}
        competition = market.get("competition") or {}
        event_id = str(event.get("id") or market.get("groupById") or "")
        if not event_id:
            continue
        opened = _open_date(event.get("openDate"))
        # A scheduled start falls on the minute; an openDate with seconds is a creation stamp (2026-11-04, New Zealand
        # v India: most markets said 06:29:10 the day before a 02:00 start, and the page closed as "out of window").
        if opened is not None and opened.second == 0 and opened.microsecond == 0:
            votes[opened] = votes.get(opened, 0) + 1
        if meta is None:
            meta = EventMeta(
                event_id=event_id,
                competition_id=str(competition.get("id") or ""),
                competition_name=str(competition.get("name") or "").strip(),
                open_date=None,
            )
    if meta is None:
        return None
    if votes:
        # Most votes wins; on a tie the later time (a creation stamp precedes the start, never follows it)
        best = max(votes.items(), key=lambda item: (item[1], item[0]))[0]
        meta = EventMeta(meta.event_id, meta.competition_id, meta.competition_name, best)
    return meta


def event_records(payload: Any, match_id: str, observed_at: datetime) -> list[MarketRecord]:
    """One record per runner per market of the event JSON.

    Dedupe key is (market id, market type, market name, selection). Two markets with the same name and the
    same selection but different ids get a ``#<market id>`` suffix on the later one's market name, so the
    stored key (time, match, type, name, selection) never collides.
    """
    records: list[MarketRecord] = []
    seen_keys: set[tuple[str, str, str, str]] = set()
    owner_by_slot: dict[tuple[str, str, str], str] = {}

    for market in _markets(payload):
        market_id = str(market.get("id") or "")
        market_type, market_name = _classify_list_market(str(market.get("name") or ""))
        market_status = str(market.get("status") or "open").lower()
        source_updated_at = _utc_from_ms(market.get("lastUpdateTime"))

        for runner in market.get("runners") or []:
            if not isinstance(runner, dict):
                continue
            runner_name = str(runner.get("name") or "").strip()
            if not runner_name:
                continue
            selection, line_no, line_yes = _list_selection(market_type, market_name, runner_name)

            slot = (market_type, market_name, selection)
            owner = owner_by_slot.setdefault(slot, market_id)
            name_for_record = market_name if owner == market_id else f"{market_name}#{market_id}"

            key = (market_id, market_type, name_for_record, selection)
            if key in seen_keys:
                continue
            seen_keys.add(key)

            back, back_size = _best(runner.get("back"), back=True)
            lay, lay_size = _best(runner.get("lay"), back=False)
            records.append(
                MarketRecord(
                    time=observed_at,
                    match_id=match_id,
                    market_type=market_type,
                    market_name=name_for_record,
                    selection=selection,
                    back=back,
                    lay=lay,
                    line_no=line_no,
                    line_yes=line_yes,
                    status=market_status,
                    source=SOURCE,
                    market_id=market_id or None,
                    back_size=back_size,
                    lay_size=lay_size,
                    runner_status=str(runner.get("status") or "").lower() or None,
                    source_updated_at=source_updated_at,
                )
            )
    return records


def in_play_of(payload: Any) -> bool:
    """True when any market of the event JSON carries ``"inPlay": true`` (S86: the match has started).

    The flag is per market (fixture ``sma_event_real_76321652_20261103.json``: 92 of 92 markets ``"inPlay": true``
    on a match captured about four hours after its ``openDate``; fixture ``sma_event_srl_76108412_20261103.json``:
    40 true, one closed market false). The real fixture has no Winner market, so every market is checked. No
    pre-start body is stored as a fixture yet, so the flag being false before the off is not verified.
    """
    return any(market.get("inPlay") is True for market in _markets(payload))


def settlement_of(payload: Any) -> Optional[Settlement]:
    """The settled Winner (match odds) market, else None.

    Verified on a real finish (lb_36267634, 2026-11-03 14:08 UTC): the exchange market showed runner statuses
    ``winner`` / ``loser`` while the market itself was still ``suspended``; the bookmaker match-odds market closed
    with both runners ``suspended``. So a market counts as settled when it is CLOSED or a runner carries a winner
    status, and the first match-odds market naming exactly one winner is preferred over a closed one naming none.
    """
    fallback: Optional[Settlement] = None
    for market in _markets(payload):
        market_type, _name = _classify_list_market(str(market.get("name") or ""))
        if market_type != "match_odds":
            continue
        runners = [r for r in market.get("runners") or [] if isinstance(r, dict)]
        statuses = tuple(
            (str(r.get("name") or ""), str(r.get("status") or "").upper()) for r in runners
        )
        winners = [name for name, status in statuses if status in WINNER_STATUSES and name]
        closed = str(market.get("status") or "").upper() == "CLOSED"
        if not closed and not winners:
            continue
        settlement = Settlement(
            market_id=str(market.get("id") or ""),
            winner=winners[0] if len(winners) == 1 else None,
            runner_statuses=statuses,
        )
        if settlement.winner is not None:
            return settlement
        fallback = fallback or settlement
    return fallback


def menu_competitions(payload: Any) -> dict[str, tuple[str, str]]:
    """``{lotus event id: (competition id, competition name)}`` for every cricket event in the menu."""
    mapping: dict[str, tuple[str, str]] = {}
    if not isinstance(payload, dict):
        return mapping
    for event_type in payload.get("result") or []:
        if not isinstance(event_type, dict) or str(event_type.get("id")) != CRICKET_EVENT_TYPE_ID:
            continue
        for group in event_type.get("children") or []:
            if not isinstance(group, dict) or group.get("type") != "GROUP":
                continue
            group_id = str(group.get("id") or "")
            group_name = str(group.get("name") or "").strip()
            if not group_name:
                continue
            for event in group.get("children") or []:
                if isinstance(event, dict) and event.get("type") == "EVENT" and event.get("id"):
                    mapping[str(event["id"])] = (group_id, group_name)
    return mapping
