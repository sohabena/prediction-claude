"""DOM -> market records for LotusBook match pages: Winner (match odds), Bookmaker and session ("fancy") markets.

Selector status (see docs/stories/S47 discovery notes):
- VERIFIED on a live match page (2026-11-02): runner rows are ``role="listitem"`` with a
  ``role="rowheader"`` carrying ``aria-label="Runner: <name>"``; a suspended market shows
  ``role="status"`` with ``aria-label="Market suspended"`` and the text ``SUSPENDED``.
- UNVERIFIED: the Bookmaker and session blocks are not present on the one live page checked.
  The hooks ``data-market-name`` (block), ``data-odd-type="back|lay|no|yes"`` (price cells)
  and ``data-status="ball_running"`` are assumptions, and the synthetic fixture
  ``tests/fixtures/lotusbook/bookmaker_session_SYNTHETIC_UNVERIFIED.html`` encodes them.
  Update this module and the fixture once a real Bookmaker / Fancy block has been captured.

No account data is parsed. The parser reads only the markup it is given.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from typing import Iterator, Optional

from shared.logging import setup_logging

logger = setup_logging("lotusbook_markets")

SOURCE = "lotusbook"
MARKET_TYPES = ("match_odds", "bookmaker", "session", "other")
STATUSES = ("open", "suspended", "ball_running", "closed")

_RUNNER_LIST_SUFFIX = " runners and odds"
# "Winner (incl. super over)", "Match Odds"
_MATCH_ODDS_RE = re.compile(r"^\s*(winner\b|match\s*odds\b)", re.IGNORECASE)

# "6 over runs IND", "20 overs runs", "10 over runs"
_OVER_RE = re.compile(r"^\s*(\d{1,2})\s*overs?\s*runs?\b\s*(.*)$", re.IGNORECASE)
_BOOKMAKER_NAMES = {"bookmaker", "bookmakers", "book maker"}
_TEAM_ALIASES = {
    "IND": "India",
    "NZ": "New Zealand",
    "AUS": "Australia",
    "ENG": "England",
    "PAK": "Pakistan",
    "SA": "South Africa",
    "WI": "West Indies",
    "SL": "Sri Lanka",
    "BAN": "Bangladesh",
}


@dataclass(frozen=True)
class MarketRecord:
    """One row of ``market_ticks``. ``selection`` is the runner name for bookmaker rows
    and the session line (or a row index) for session rows."""

    time: datetime
    match_id: str
    market_type: str
    market_name: str
    selection: str
    back: Optional[float] = None
    lay: Optional[float] = None
    line_no: Optional[float] = None
    rate_no: Optional[float] = None
    line_yes: Optional[float] = None
    rate_yes: Optional[float] = None
    status: str = "open"
    source: str = SOURCE
    # S56: fields from the page's own market JSON (None for DOM-parsed records)
    market_id: Optional[str] = None
    back_size: Optional[float] = None
    lay_size: Optional[float] = None
    runner_status: Optional[str] = None
    source_updated_at: Optional[datetime] = None

    def content_key(self) -> tuple:
        """Everything except the timestamp. Used to skip unchanged snapshots."""
        return (
            self.market_type,
            self.market_name,
            self.selection,
            self.back,
            self.lay,
            self.line_no,
            self.rate_no,
            self.line_yes,
            self.rate_yes,
            self.status,
            self.back_size,
            self.lay_size,
            self.runner_status,
        )


def normalise_market_name(raw: str) -> tuple[str, str]:
    """Map a raw market heading to ``(market_type, market_name)``.

    ``"6 over runs IND"`` -> ``("session", "session:6_over:India")``;
    ``"Bookmaker"`` -> ``("bookmaker", "bookmaker")``; anything else -> ``("other", raw)``.
    """
    text = " ".join((raw or "").split())
    if text.lower() in _BOOKMAKER_NAMES:
        return "bookmaker", "bookmaker"
    match = _OVER_RE.match(text)
    if match:
        overs, team = match.group(1), match.group(2).strip()
        if not team:
            team_part = "match"
        else:
            team_part = _TEAM_ALIASES.get(team.upper(), team).replace(" ", "_")
        return "session", f"session:{int(overs)}_over:{team_part}"
    return "other", text


def content_fingerprint(records: list[MarketRecord]) -> str:
    """Order-independent fingerprint of the market content, ignoring time."""
    return "|".join(sorted(repr(r.content_key()) for r in records))


# ── minimal DOM tree (stdlib only; bs4 is not a declared dependency) ──────────

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class _Node:
    __slots__ = ("tag", "attrs", "children")

    def __init__(self, tag: str, attrs: dict[str, str]) -> None:
        self.tag = tag
        self.attrs = attrs
        self.children: list = []


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root", {})
        self._stack = [self.root]

    def handle_starttag(self, tag, attrs):  # type: ignore[override]
        node = _Node(tag, {k: (v or "") for k, v in attrs})
        self._stack[-1].children.append(node)
        if tag not in _VOID:
            self._stack.append(node)

    def handle_endtag(self, tag):  # type: ignore[override]
        for i in range(len(self._stack) - 1, 0, -1):
            if self._stack[i].tag == tag:
                del self._stack[i:]
                break

    def handle_data(self, data):  # type: ignore[override]
        self._stack[-1].children.append(data)


def _build(html: str) -> _Node:
    builder = _TreeBuilder()
    builder.feed(html or "")
    builder.close()
    return builder.root


def _walk(node: _Node) -> Iterator[_Node]:
    for child in node.children:
        if isinstance(child, _Node):
            yield child
            yield from _walk(child)


def _text(node: _Node) -> str:
    parts: list[str] = []

    def collect(n: _Node) -> None:
        for child in n.children:
            if isinstance(child, _Node):
                collect(child)
            else:
                parts.append(child)

    collect(node)
    return " ".join(" ".join(parts).split())


def _numbers(text: str) -> list[float]:
    out: list[float] = []
    for token in text.split():
        try:
            out.append(float(token))
        except ValueError:
            continue
    return out


def _find_odd(row: _Node, kind: str) -> Optional[_Node]:
    for n in _walk(row):
        if n.attrs.get("data-odd-type", "").lower() == kind:
            return n
    return None


def _runner_name(row: _Node) -> Optional[str]:
    for n in _walk(row):
        if n.attrs.get("role") == "rowheader":
            label = n.attrs.get("aria-label", "")
            if label.lower().startswith("runner:"):
                return label.split(":", 1)[1].strip() or None
            text = _text(n)
            return text or None
    return None


def _status_of(node: _Node, default: str = "open") -> str:
    own_text = _text(node).lower()
    labels = " ".join(
        f"{n.attrs.get('aria-label', '')} {n.attrs.get('title', '')}".lower() for n in _walk(node)
    )
    if "ball running" in own_text or any(
        n.attrs.get("data-status", "").lower() == "ball_running" for n in _walk(node)
    ):
        return "ball_running"
    if "market suspended" in labels or "suspended" in own_text:
        return "suspended"
    return default


def parse_market_html(html: str, match_id: str, observed_at: datetime) -> list[MarketRecord]:
    """Parse a LotusBook match page into Bookmaker / session / other market records.

    Returns ``[]`` when the page has no market blocks (for example the match-odds-only page
    observed on 2026-11-02).
    """
    root = _build(html)
    records: list[MarketRecord] = []
    for block in _walk(root):
        raw_name = block.attrs.get("data-market-name")
        if raw_name is None:
            continue
        market_type, market_name = normalise_market_name(raw_name)
        block_status = block.attrs.get("data-status", "").lower()
        block_status = block_status if block_status in STATUSES else "open"
        rows = [n for n in _walk(block) if n.attrs.get("role") == "listitem"] or [block]

        if market_type == "session":
            for index, row in enumerate(rows):
                no_node, yes_node = _find_odd(row, "no"), _find_odd(row, "yes")
                if no_node is None and yes_node is None:
                    continue
                no_nums = _numbers(_text(no_node)) if no_node is not None else []
                yes_nums = _numbers(_text(yes_node)) if yes_node is not None else []
                line_no = no_nums[0] if len(no_nums) >= 2 else None
                rate_no = no_nums[1] if len(no_nums) >= 2 else None
                line_yes = yes_nums[0] if len(yes_nums) >= 2 else None
                rate_yes = yes_nums[1] if len(yes_nums) >= 2 else None
                selection = f"{line_no:g}" if line_no is not None else f"row{index}"
                status = _status_of(row, default=block_status)
                records.append(
                    MarketRecord(
                        time=observed_at,
                        match_id=match_id,
                        market_type=market_type,
                        market_name=market_name,
                        selection=selection,
                        line_no=line_no,
                        rate_no=rate_no,
                        line_yes=line_yes,
                        rate_yes=rate_yes,
                        status=status,
                    )
                )
            continue

        for row in rows:
            name = _runner_name(row)
            back_node, lay_node = _find_odd(row, "back"), _find_odd(row, "lay")
            back = _price(_text(back_node)) if back_node is not None else None
            lay = _price(_text(lay_node)) if lay_node is not None else None
            if not name or (back is None and lay is None):
                continue
            records.append(
                MarketRecord(
                    time=observed_at,
                    match_id=match_id,
                    market_type=market_type,
                    market_name=market_name,
                    selection=name,
                    back=back,
                    lay=lay,
                    status=_status_of(row, default=block_status),
                )
            )
    records.extend(_parse_runner_lists(root, match_id, observed_at))
    logger.debug("market_blocks_parsed", match_id=match_id, records=len(records))
    return records


# S52: SRL-style per-innings markets, e.g. "1st innings over 18 - Trinbago Knight Riders SRL total"
_INN_OVER_TOTAL_RE = re.compile(
    r"^\s*(\d+)(?:st|nd|rd|th)\s+innings\s+over\s+(\d+)\s+-\s+(.+?)\s+total\s*$", re.IGNORECASE
)
_INN_OVER_ODD_EVEN_RE = re.compile(
    r"^\s*(\d+)(?:st|nd|rd|th)\s+innings\s+over\s+(\d+)\s+-\s+(.+?)\s+odd/even\s*$", re.IGNORECASE
)
_INN_DISMISSAL_RE = re.compile(
    r"^\s*(\d+)(?:st|nd|rd|th)\s+innings\s+-\s+(.+?)\s+dismissal\s+method(?:\s*\(extended\))?\s*$",
    re.IGNORECASE,
)
_WICKET_ORDINAL_RE = re.compile(r"\b(\d+)(?:st|nd|rd|th)\b", re.IGNORECASE)
_LINE_SELECTION_RE = re.compile(r"^\s*(under|over)\s+(\d+(?:\.\d+)?)\s*$", re.IGNORECASE)


def _slug(text: str) -> str:
    """Team or player text as a name component: words joined with underscores."""
    return "_".join(text.split())


def _capitalise(text: str) -> str:
    """"under 6.5" -> "Under 6.5"; the first letter only, so team and player spelling is kept."""
    text = " ".join(text.split())
    return text[:1].upper() + text[1:]


def _player_slug(subject: str) -> str:
    """"Hosein, Akeal Jerome (SRL)" and "Narine, Sunil - SRL" -> "Hosein_Akeal_Jerome", "Narine_Sunil"."""
    name = re.sub(r"\s*\(SRL\)", "", subject)
    name = re.sub(r"\s*-\s*SRL\s*$", "", name)
    return _slug(name.replace(",", " "))


def _classify_list_market(raw: str) -> tuple[str, str]:
    """Market type and name for a list-market label (the text before " runners and odds").

    S52 names: "Winner" / "Match Odds" -> match_odds; "<n>st|nd|rd|th innings over <k> - <team> total" ->
    over_runs:inn<n>:over<k>:<Team>; "... odd/even" -> odd_even:inn<n>:over<k>:<Team>;
    "<n>st innings - <player> dismissal method" -> dismissal:inn<n>:<Player> (or <Team>:wicket<n> for a
    team-wide "<Team> 8th dismissal method"); "Bookmaker ..." -> bookmaker, with the raw label. Anything else ->
    other, with the raw label.
    """
    text = " ".join((raw or "").split())
    match = _INN_OVER_TOTAL_RE.match(text)
    if match:
        inn, over, team = match.groups()
        return "over_runs", f"over_runs:inn{int(inn)}:over{int(over)}:{_slug(team)}"
    match = _INN_OVER_ODD_EVEN_RE.match(text)
    if match:
        inn, over, team = match.groups()
        return "other", f"odd_even:inn{int(inn)}:over{int(over)}:{_slug(team)}"
    match = _INN_DISMISSAL_RE.match(text)
    if match:
        inn, subject = match.groups()
        ordinal = _WICKET_ORDINAL_RE.search(subject)
        if ordinal:
            # Team-wide "<Team> 1st dismissal method": keep the team, or both teams' markets collide
            team = subject[: ordinal.start()].strip()
            team_part = f"{_slug(team)}:" if team else ""
            return "other", f"dismissal:inn{int(inn)}:{team_part}wicket{int(ordinal.group(1))}"
        return "other", f"dismissal:inn{int(inn)}:{_player_slug(subject)}"
    market_type, market_name = normalise_market_name(text)
    if market_type == "other" and _MATCH_ODDS_RE.match(market_name):
        return "match_odds", "match_odds"
    if market_type == "other" and text.lower().startswith("bookmaker"):
        # The site's in-house bookmaker, e.g. "Bookmaker 0 Commission" (2026-11-03): its runners are bettable,
        # unlike the exchange page's `_BR` match-odds market. The raw name is kept.
        return "bookmaker", text
    return market_type, market_name


def _list_selection(market_type: str, market_name: str, runner: str) -> tuple[str, Optional[float], Optional[float]]:
    """``(selection, line_no, line_yes)`` for a runner in a list market.

    Over-run lines ("under 6.5" / "over 6.5") keep their line as a float: ``line_no`` for Under,
    ``line_yes`` for Over. Two blocks with different lines differ in selection, so they never share a PK.
    """
    if market_type == "over_runs":
        match = _LINE_SELECTION_RE.match(runner)
        if match:
            side, line = match.group(1).lower(), float(match.group(2))
            selection = f"{side.capitalize()} {match.group(2)}"
            if side == "under":
                return selection, line, None
            return selection, None, line
        return _capitalise(runner), None, None
    if market_type == "other" and market_name.startswith("odd_even:"):
        return _capitalise(runner), None, None
    return runner, None, None


def _cell_price(button: Optional[_Node]) -> Optional[float]:
    """Price from a cell's ``data-price`` (``0`` is an empty "-" cell); falls back to its text."""
    if button is None:
        return None
    raw = button.attrs.get("data-price")
    if raw is not None:
        try:
            value = float(raw)
        except ValueError:
            return None
        return value if value > 1.0 else None
    return _price(_text(button))


def _best_cells(row: _Node) -> tuple[Optional[_Node], Optional[_Node]]:
    """Best back and best lay cell of a runner row: the mobile view holds exactly those two;
    the desktop ladder labels them "Back odds 1" / "Lay odds 1"."""
    for n in _walk(row):
        if n.attrs.get("aria-label", "").lower().endswith("odds - mobile view"):
            return _find_odd(n, "back"), _find_odd(n, "lay")
    back = lay = None
    for n in _walk(row):
        label = n.attrs.get("aria-label", "").lower()
        if label == "back odds 1":
            back = _find_odd(n, "back")
        elif label == "lay odds 1":
            lay = _find_odd(n, "lay")
    return back, lay


def _parse_runner_lists(root: _Node, match_id: str, observed_at: datetime) -> list[MarketRecord]:
    """Markets rendered as ``role="list"`` blocks labelled "<market> runners and odds".

    VERIFIED on a live match page (2026-11-03, fixture ``winner_market_live_20261103.html``):
    the Winner (match odds) market. On the listing page the same match showed no lay prices.
    """
    records: list[MarketRecord] = []
    for block in _walk(root):
        label = block.attrs.get("aria-label", "")
        if block.attrs.get("role") != "list" or not label.lower().endswith(_RUNNER_LIST_SUFFIX):
            continue
        market_type, market_name = _classify_list_market(label[: -len(_RUNNER_LIST_SUFFIX)])
        for row in (n for n in _walk(block) if n.attrs.get("role") == "listitem"):
            name = _runner_name(row)
            back_cell, lay_cell = _best_cells(row)
            back, lay = _cell_price(back_cell), _cell_price(lay_cell)
            status = _status_of(row)
            if back is None and lay is None and status == "open":
                if market_type != "match_odds":
                    continue
                # A Winner runner with every cell "-" is a suspended market, not a missing one: record it
                status = "suspended"
            if not name:
                continue
            selection, line_no, line_yes = _list_selection(market_type, market_name, name)
            records.append(
                MarketRecord(
                    time=observed_at,
                    match_id=match_id,
                    market_type=market_type,
                    market_name=market_name,
                    selection=selection,
                    back=back,
                    lay=lay,
                    line_no=line_no,
                    line_yes=line_yes,
                    status=status,
                )
            )
    return records


def _price(text: str) -> Optional[float]:
    nums = _numbers(text)
    if not nums:
        return None
    value = nums[0]
    return value if value > 1.0 else None
