"""DOM -> market records for LotusBook Bookmaker and session ("fancy") markets (S47).

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
MARKET_TYPES = ("bookmaker", "session", "other")
STATUSES = ("open", "suspended", "ball_running")

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
    logger.debug("market_blocks_parsed", match_id=match_id, records=len(records))
    return records


def _price(text: str) -> Optional[float]:
    nums = _numbers(text)
    if not nums:
        return None
    value = nums[0]
    return value if value > 1.0 else None
