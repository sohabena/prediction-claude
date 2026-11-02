"""
Betfair Historical Data Parser.
Parses Betfair historic exchange data (JSON/JSONL stream format and CSV format)
into normalized OddsEvent / OddsTick representations for PHOENIX training.

Supported input formats:
1. Betfair Stream API files (JSON/JSONL, .json, .json.gz, .bz2)
   Format contains marketDefinition, rc (runner changes), ltp, tv, batb, batl.
2. Betfair Historical CSV files (.csv, .csv.gz)
   Standard historical data downloads with selection prices and settled flags.

Stream semantics (S35):
- ``ltp`` (last traded price) is kept separate from the back/lay ladder. A missing
  ladder is reported as ``None``; ltp is never copied into back or lay.
- ``batb``/``batl`` are per-level updates ``[level, price, size]``; size 0 removes the
  level. The best price is the lowest level still present.
- ``tv`` (traded volume) is kept per runner when present.
- ``marketDefinition.inPlay`` is recorded on every tick as ``in_play``.
- A runner named "The Draw" is the draw selection; home/away are the first two others.
- A winner exists only when a runner carries status WINNER. No winner is inferred from prices.
"""

from __future__ import annotations

import bz2
import csv
import gzip
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator, Iterable, Optional

from shared.constants import (
    FRANCHISE_TEAMS,
    INTERNATIONAL_TEAMS,
    VALID_ODDS_MAX,
    VALID_ODDS_MIN,
)
from shared.logging import setup_logging
from shared.schemas import OddsEvent

logger = setup_logging("betfair_historical_parser")

_DRAW_NAME_RE = re.compile(r"^(the\s+)?draw$", re.IGNORECASE)


@dataclass
class ParsedHistoricalMatch:
    """Represents a complete parsed historical match with its ticks and result."""

    match_id: str
    team_home: str
    team_away: str
    competition: str
    start_time: datetime
    completed_at: datetime
    winner: str = ""
    loser: str = ""
    result_type: str = "win"  # win | draw | no_result
    ticks: list[dict[str, Any]] = field(default_factory=list)
    closing_back_home: Optional[float] = None
    closing_back_away: Optional[float] = None
    closing_lay_home: Optional[float] = None
    closing_lay_away: Optional[float] = None
    # Stream-only metadata (defaults keep the CSV path and older callers unchanged)
    market_id: str = ""
    event_name: str = ""
    market_type: str = ""
    data_tier: str = ""  # basic | advanced | pro (detected from ladder depth)
    runner_count: int = 0  # match-odds selections, draw excluded
    in_play_ticks: int = 0


class BetfairHistoricalParser:
    """
    Parses Betfair historical datasets into normalized PHOENIX ticks and episodes.
    """

    def __init__(
        self,
        resample_seconds: float = 3.0,
        min_ticks: int = 50,
    ) -> None:
        """
        Args:
            resample_seconds: Minimum time between emitted ticks for the same match.
                              Raw Betfair data can have 10-50ms tick changes; resampling
                              aligns with the live scraping interval (~3-5s).
            min_ticks: Minimum valid in-play ticks for an episode to qualify.
        """
        self.resample_seconds = resample_seconds
        self.min_ticks = min_ticks

    # ================================================================
    # Public Entry Points
    # ================================================================

    def parse_file(self, file_path: str | Path) -> list[ParsedHistoricalMatch]:
        """
        Parse a Betfair historical file (.json, .jsonl, .json.gz, .bz2, .csv).
        Returns a list of parsed matches found in the file.
        """
        path = Path(file_path)
        if not path.exists():
            logger.error("file_not_found", path=str(path))
            return []

        ext = path.name.lower()
        if ext.endswith(".csv") or ext.endswith(".csv.gz"):
            return self.parse_csv_file(path)
        else:
            return self.parse_stream_file(path)

    def parse_stream_file(self, file_path: Path) -> list[ParsedHistoricalMatch]:
        """Parse Betfair streaming format (JSON/JSONL/BZ2/GZ)."""
        lines = self._read_lines(file_path)
        return self.parse_stream_lines(lines, source_name=file_path.stem)

    def parse_stream_market_file(self, file_path: str | Path) -> list[ParsedHistoricalMatch]:
        """Parse one stream file and return every market in it, without the tick-count filter."""
        path = Path(file_path)
        return self.parse_stream_markets(self._read_lines(path), source_name=path.stem)

    def parse_stream_lines(
        self, lines: Iterable[str], source_name: str = "stream"
    ) -> list[ParsedHistoricalMatch]:
        """
        Parse a sequence of raw stream lines (JSON objects) and keep the markets with at
        least ``min_ticks`` ticks.
        """
        return [
            match
            for match in self.parse_stream_markets(lines, source_name)
            if match.ticks and len(match.ticks) >= self.min_ticks
        ]

    def parse_stream_markets(
        self, lines: Iterable[str], source_name: str = "stream"
    ) -> list[ParsedHistoricalMatch]:
        """
        Parse stream lines into one ParsedHistoricalMatch per market, including markets
        with no ticks or no winner. Handles market definition changes, runner mappings
        and price updates.
        """
        # Group by market_id -> match state
        markets: dict[str, dict[str, Any]] = {}

        for line_str in lines:
            line = line_str.strip()
            if not line:
                continue

            try:
                data = json.loads(line)
            except Exception:
                continue

            # Betfair stream envelope: {"op": "mcm", "pt": 154..., "mc": [...]}
            pt = data.get("pt")  # publish time in ms
            if pt is not None:
                timestamp = datetime.fromtimestamp(pt / 1000.0, tz=timezone.utc)
            else:
                timestamp = datetime.now(timezone.utc)

            mc_list = data.get("mc", [])
            for mc in mc_list:
                market_id = mc.get("id")
                if not market_id:
                    continue

                if market_id not in markets:
                    markets[market_id] = {
                        "market_id": market_id,
                        "market_def": {},
                        "runners": {},  # selection_id -> see _new_runner
                        "ticks": [],
                        "last_tick_time": None,
                        "first_time": timestamp,
                        "last_time": timestamp,
                        "winner_selection_id": None,
                        "ladder_seen": False,
                        "max_ladder_level": 0,
                    }

                m_state = markets[market_id]
                m_state["last_time"] = timestamp

                # Market definition updates
                market_def = mc.get("marketDefinition")
                if market_def:
                    m_state["market_def"].update(market_def)
                    # Check runner definitions
                    for r_def in market_def.get("runners", []):
                        r_id = r_def.get("id")
                        r_name = r_def.get("name", "")
                        status = r_def.get("status")
                        if r_id not in m_state["runners"]:
                            m_state["runners"][r_id] = self._new_runner(r_name)
                        elif r_name:
                            m_state["runners"][r_id]["name"] = r_name

                        if status == "WINNER":
                            m_state["winner_selection_id"] = r_id

                # Runner changes (rc)
                rc_list = mc.get("rc", [])
                for rc in rc_list:
                    r_id = rc.get("id")
                    if r_id not in m_state["runners"]:
                        m_state["runners"][r_id] = self._new_runner(f"Runner_{r_id}")

                    runner = m_state["runners"][r_id]

                    # Ladder levels: [level, price, size]; size 0 removes the level
                    if "batb" in rc:
                        self._apply_ladder_updates(runner["book_back"], rc.get("batb"), m_state)
                    if "batl" in rc:
                        self._apply_ladder_updates(runner["book_lay"], rc.get("batl"), m_state)

                    # Last traded price, kept separate from the ladder
                    if rc.get("ltp") is not None:
                        runner["ltp"] = self._validate_odds(rc.get("ltp"))

                    # Traded volume on this runner
                    if rc.get("tv") is not None:
                        runner["tv"] = self._to_float(rc.get("tv"))

                # Sample tick if minimum time interval elapsed
                last_time = m_state["last_tick_time"]
                elapsed = (timestamp - last_time).total_seconds() if last_time else 999.0

                if elapsed >= self.resample_seconds:
                    tick = self._extract_stream_tick(m_state, timestamp)
                    if tick:
                        m_state["ticks"].append(tick)
                        m_state["last_tick_time"] = timestamp

        # Post-process parsed markets into ParsedHistoricalMatch objects
        return [
            self._build_match_from_stream(market_id, m_state)
            for market_id, m_state in markets.items()
        ]

    # ================================================================
    # CSV Parser Support
    # ================================================================

    def parse_csv_file(self, file_path: Path) -> list[ParsedHistoricalMatch]:
        """Parse Betfair Historical CSV export."""
        lines = self._read_lines(file_path)
        reader = csv.DictReader(lines)
        if not reader.fieldnames:
            return []

        # Normalize column names to uppercase stripped
        normalized_fieldnames = {col: col.strip().upper() for col in reader.fieldnames}

        # Group by EVENT_ID or MATCH_NAME
        events_data: dict[str, list[dict[str, Any]]] = {}

        for row in reader:
            clean_row = {normalized_fieldnames[k]: v.strip() for k, v in row.items() if k in normalized_fieldnames}
            event_id = clean_row.get("EVENT_ID") or clean_row.get("MATCH_NAME") or clean_row.get("MARKET_ID")
            if not event_id:
                continue

            if event_id not in events_data:
                events_data[event_id] = []
            events_data[event_id].append(clean_row)

        parsed_matches: list[ParsedHistoricalMatch] = []
        for event_id, rows in events_data.items():
            match = self._build_match_from_csv(event_id, rows)
            if match and len(match.ticks) >= self.min_ticks:
                parsed_matches.append(match)

        return parsed_matches

    # ================================================================
    # Helper & Construction Methods
    # ================================================================

    def _read_lines(self, file_path: Path) -> list[str]:
        """Read lines supporting gzip, bzip2, or plain text transparently."""
        name = file_path.name.lower()
        if name.endswith(".bz2"):
            with bz2.open(file_path, "rt", encoding="utf-8", errors="replace") as f:
                return f.readlines()
        elif name.endswith(".gz"):
            with gzip.open(file_path, "rt", encoding="utf-8", errors="replace") as f:
                return f.readlines()
        else:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                return f.readlines()

    def _clean_team_name(self, raw_name: str) -> str:
        """Standardize team names removing market tags and extra spaces."""
        cleaned = re.sub(r"\s+", " ", raw_name).strip()
        # Remove trailing odds, parenthesis, or annotations
        cleaned = re.sub(r"\s*\([^)]*\)", "", cleaned).strip()
        return cleaned

    @staticmethod
    def _new_runner(name: str) -> dict[str, Any]:
        """Per-selection state: ltp and traded volume, plus back/lay ladders keyed by level."""
        return {
            "name": name,
            "ltp": None,
            "tv": None,
            "book_back": {},  # level -> (price, size)
            "book_lay": {},  # level -> (price, size)
        }

    @staticmethod
    def _to_float(val: Any) -> Optional[float]:
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _best_level(book: dict[int, tuple[float, Optional[float]]]) -> Optional[tuple[float, Optional[float]]]:
        """Best price on one side of the ladder: the lowest level still present."""
        if not book:
            return None
        return book[min(book)]

    def _apply_ladder_updates(
        self, book: dict[int, tuple[float, Optional[float]]], entries: Any, m_state: dict[str, Any]
    ) -> None:
        """Apply per-level ``[level, price, size]`` updates; size 0 removes the level."""
        if not entries:
            return
        m_state["ladder_seen"] = True
        for entry in entries:
            if not entry or len(entry) < 2:
                continue
            try:
                level = int(entry[0])
                price = float(entry[1])
            except (TypeError, ValueError):
                continue
            size = self._to_float(entry[2]) if len(entry) >= 3 else None
            if size == 0:
                book.pop(level, None)
                continue
            book[level] = (price, size)
            m_state["max_ladder_level"] = max(m_state["max_ladder_level"], level)

    @staticmethod
    def _runner_roles(m_state: dict[str, Any]) -> tuple[Any, Any, Any]:
        """Return (home_id, away_id, draw_id). The draw is the runner named "The Draw"."""
        draw_id = None
        others: list[Any] = []
        for r_id, runner in m_state["runners"].items():
            if draw_id is None and _DRAW_NAME_RE.match((runner.get("name") or "").strip()):
                draw_id = r_id
            else:
                others.append(r_id)
        home_id = others[0] if len(others) >= 1 else None
        away_id = others[1] if len(others) >= 2 else None
        return home_id, away_id, draw_id

    @staticmethod
    def _data_tier(m_state: dict[str, Any]) -> str:
        """basic = no ladder in file; advanced = ladder up to level 2; pro = deeper ladder."""
        if not m_state.get("ladder_seen"):
            return "basic"
        return "pro" if m_state.get("max_ladder_level", 0) > 2 else "advanced"

    def _extract_stream_tick(
        self, m_state: dict[str, Any], timestamp: datetime
    ) -> Optional[dict[str, Any]]:
        """Extract a single snapshot tick from stream state."""
        home_id, away_id, draw_id = self._runner_roles(m_state)
        if home_id is None or away_id is None:
            return None

        runners = m_state["runners"]
        r0 = runners[home_id]
        r1 = runners[away_id]
        r2 = runners[draw_id] if draw_id is not None else None

        best_b0 = self._best_level(r0["book_back"])
        best_l0 = self._best_level(r0["book_lay"])
        best_b1 = self._best_level(r1["book_back"])
        best_l1 = self._best_level(r1["book_lay"])

        back_home = self._validate_odds(best_b0[0]) if best_b0 else None
        lay_home = self._validate_odds(best_l0[0]) if best_l0 else None
        back_away = self._validate_odds(best_b1[0]) if best_b1 else None
        lay_away = self._validate_odds(best_l1[0]) if best_l1 else None

        ltp_home = r0.get("ltp")
        ltp_away = r1.get("ltp")
        ltp_draw = r2.get("ltp") if r2 else None

        # Must have at least one price (ladder or last traded) for a side
        if back_home is None and back_away is None and ltp_home is None and ltp_away is None:
            return None

        # Spread consistency check: back <= lay (pre-existing correction, kept for the DB path)
        if back_home and lay_home and back_home > lay_home:
            lay_home = back_home * 1.02
        if back_away and lay_away and back_away > lay_away:
            lay_away = back_away * 1.02

        draw_back = None
        draw_lay = None
        if r2:
            best_b2 = self._best_level(r2["book_back"])
            best_l2 = self._best_level(r2["book_lay"])
            draw_back = self._validate_odds(best_b2[0]) if best_b2 else None
            draw_lay = self._validate_odds(best_l2[0]) if best_l2 else None

        # Calculate implied probabilities and overround
        ip_home = (1.0 / back_home) if back_home else None
        ip_away = (1.0 / back_away) if back_away else None
        ip_draw = (1.0 / draw_back) if draw_back else 0.0

        overround = None
        if ip_home is not None and ip_away is not None:
            overround = ip_home + ip_away + (ip_draw or 0.0) - 1.0

        return {
            "timestamp": timestamp,
            "back_home": back_home,
            "lay_home": lay_home,
            "back_away": back_away,
            "lay_away": lay_away,
            "back_draw": draw_back,
            "lay_draw": draw_lay,
            "volume_back_home": best_b0[1] if best_b0 else None,
            "volume_lay_home": best_l0[1] if best_l0 else None,
            "volume_back_away": best_b1[1] if best_b1 else None,
            "volume_lay_away": best_l1[1] if best_l1 else None,
            "ltp_home": ltp_home,
            "ltp_away": ltp_away,
            "ltp_draw": ltp_draw,
            "traded_volume_home": r0.get("tv"),
            "traded_volume_away": r1.get("tv"),
            "implied_prob_home": ip_home,
            "implied_prob_away": ip_away,
            "overround": overround,
            "is_live": True,
            "in_play": bool(m_state["market_def"].get("inPlay")),
        }

    def _build_match_from_stream(
        self, market_id: str, m_state: dict[str, Any]
    ) -> ParsedHistoricalMatch:
        """Convert accumulated stream state into ParsedHistoricalMatch (ticks may be empty)."""
        ticks = m_state["ticks"]

        m_def = m_state["market_def"]
        event_name = m_def.get("eventName", "") or ""
        competition = m_def.get("competitionName", "") or event_name

        runners = m_state["runners"]
        home_id, away_id, draw_id = self._runner_roles(m_state)

        def _runner_name(r_id: Any, fallback: str) -> str:
            if r_id is None:
                return fallback
            return self._clean_team_name(runners[r_id].get("name") or fallback)

        team_home = _runner_name(home_id, "Home")
        team_away = _runner_name(away_id, "Away")

        # Parse event name "Team A v Team B" if runner names are generic
        if ("Runner_" in team_home or "Home" == team_home) and (" v " in event_name or " vs " in event_name):
            parts = re.split(r"\s+v(?:s)?\.?\s+", event_name, flags=re.IGNORECASE)
            if len(parts) >= 2:
                team_home = self._clean_team_name(parts[0])
                team_away = self._clean_team_name(parts[1])

        # Result comes only from the WINNER status of a runner; prices are never used to infer it
        winner = ""
        loser = ""
        result_type = "no_result"
        winner_id = m_state.get("winner_selection_id")
        if winner_id is not None and winner_id == home_id:
            winner, loser, result_type = team_home, team_away, "win"
        elif winner_id is not None and winner_id == away_id:
            winner, loser, result_type = team_away, team_home, "win"
        elif winner_id is not None and winner_id == draw_id:
            result_type = "draw"

        match_id = f"bf_{market_id.replace('.', '_')}"

        # Assign match metadata to all ticks
        for t in ticks:
            t["match_id"] = match_id
            t["team_home"] = team_home
            t["team_away"] = team_away
            t["competition"] = competition

        last_tick = ticks[-1] if ticks else {}
        start_time = ticks[0]["timestamp"] if ticks else m_state["first_time"]
        completed_at = last_tick.get("timestamp", m_state["last_time"])

        return ParsedHistoricalMatch(
            match_id=match_id,
            team_home=team_home,
            team_away=team_away,
            competition=competition,
            start_time=start_time,
            completed_at=completed_at,
            winner=winner,
            loser=loser,
            result_type=result_type,
            ticks=ticks,
            closing_back_home=last_tick.get("back_home"),
            closing_back_away=last_tick.get("back_away"),
            closing_lay_home=last_tick.get("lay_home"),
            closing_lay_away=last_tick.get("lay_away"),
            market_id=market_id,
            event_name=event_name,
            market_type=m_def.get("marketType", "") or "",
            data_tier=self._data_tier(m_state),
            runner_count=(home_id is not None) + (away_id is not None),
            in_play_ticks=sum(1 for t in ticks if t.get("in_play")),
        )

    def _build_match_from_csv(
        self, event_id: str, rows: list[dict[str, Any]]
    ) -> Optional[ParsedHistoricalMatch]:
        """Convert CSV rows for an event into ParsedHistoricalMatch."""
        if not rows:
            return None

        # Sort rows by timestamp
        def _parse_ts(row: dict[str, Any]) -> datetime:
            for key in ("RECORD_TIME", "TIMESTAMP", "DATE_TIME", "SETTLED_DATE", "OPEN_DATE"):
                val = row.get(key)
                if val:
                    try:
                        return datetime.fromisoformat(val.replace("Z", "+00:00"))
                    except ValueError:
                        pass
                    try:
                        return datetime.strptime(val, "%d-%m-%Y %H:%M:%S").replace(tzinfo=timezone.utc)
                    except ValueError:
                        pass
            return datetime.now(timezone.utc)

        rows_sorted = sorted(rows, key=_parse_ts)

        # Identify teams from selection names
        selections: dict[str, list[dict[str, Any]]] = {}
        winner_name = ""
        for r in rows_sorted:
            s_name = r.get("SELECTION_NAME") or r.get("RUNNER_NAME", "")
            if not s_name:
                continue
            if s_name not in selections:
                selections[s_name] = []
            selections[s_name].append(r)

            win_flag = r.get("WIN_FLAG") or r.get("WIN")
            if win_flag in ("1", "Y", "TRUE", "WINNER"):
                winner_name = s_name

        team_names = list(selections.keys())
        if len(team_names) < 2:
            return None

        team_home = self._clean_team_name(team_names[0])
        team_away = self._clean_team_name(team_names[1])
        competition = rows_sorted[0].get("EVENT_NAME") or rows_sorted[0].get("COMPETITION") or "Betfair Historical"

        match_id = f"bf_{event_id.replace(' ', '_').lower()}"

        ticks: list[dict[str, Any]] = []
        last_emitted_time: Optional[datetime] = None

        # Group rows by timestamp slice
        time_buckets: dict[str, dict[str, Any]] = {}
        for r in rows_sorted:
            ts = _parse_ts(r)
            s_name = r.get("SELECTION_NAME") or r.get("RUNNER_NAME", "")
            ts_key = ts.strftime("%Y-%m-%d %H:%M:%S")

            if ts_key not in time_buckets:
                time_buckets[ts_key] = {"timestamp": ts, "prices": {}}

            back = self._validate_odds(r.get("BEST_BACK_PRICE") or r.get("BACK_PRICE"))
            lay = self._validate_odds(r.get("BEST_LAY_PRICE") or r.get("LAY_PRICE"))
            time_buckets[ts_key]["prices"][s_name] = {"back": back, "lay": lay}

        for ts_key, bucket in sorted(time_buckets.items(), key=lambda x: x[1]["timestamp"]):
            ts = bucket["timestamp"]
            if last_emitted_time:
                if (ts - last_emitted_time).total_seconds() < self.resample_seconds:
                    continue

            prices = bucket["prices"]
            h_price = prices.get(team_names[0], {})
            a_price = prices.get(team_names[1], {})

            back_home = h_price.get("back")
            lay_home = h_price.get("lay")
            back_away = a_price.get("back")
            lay_away = a_price.get("lay")

            if not (back_home or back_away):
                continue

            ip_home = (1.0 / back_home) if back_home else None
            ip_away = (1.0 / back_away) if back_away else None
            overround = (ip_home + ip_away - 1.0) if (ip_home and ip_away) else None

            ticks.append({
                "match_id": match_id,
                "team_home": team_home,
                "team_away": team_away,
                "competition": competition,
                "timestamp": ts,
                "back_home": back_home,
                "lay_home": lay_home,
                "back_away": back_away,
                "lay_away": lay_away,
                "back_draw": None,
                "lay_draw": None,
                "volume_back_home": None,
                "volume_lay_home": None,
                "volume_back_away": None,
                "volume_lay_away": None,
                "implied_prob_home": ip_home,
                "implied_prob_away": ip_away,
                "overround": overround,
                "is_live": True,
            })
            last_emitted_time = ts

        if len(ticks) < self.min_ticks:
            return None

        winner = self._clean_team_name(winner_name) if winner_name else ""
        loser = team_away if winner == team_home else (team_home if winner == team_away else "")

        last_tick = ticks[-1]
        return ParsedHistoricalMatch(
            match_id=match_id,
            team_home=team_home,
            team_away=team_away,
            competition=competition,
            start_time=ticks[0]["timestamp"],
            completed_at=last_tick["timestamp"],
            winner=winner,
            loser=loser,
            result_type="win" if winner else "no_result",
            ticks=ticks,
            closing_back_home=last_tick.get("back_home"),
            closing_back_away=last_tick.get("back_away"),
            closing_lay_home=last_tick.get("lay_home"),
            closing_lay_away=last_tick.get("lay_away"),
        )

    def _validate_odds(self, val: Any) -> Optional[float]:
        """Validate and return float decimal odds within permissible range."""
        if val is None:
            return None
        try:
            f = float(val)
            if VALID_ODDS_MIN <= f <= VALID_ODDS_MAX:
                return round(f, 3)
        except (ValueError, TypeError):
            pass
        return None
