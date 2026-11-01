"""
Betfair Historical Data Parser.
Parses Betfair historic exchange data (JSON/JSONL stream format and CSV format)
into normalized OddsEvent / OddsTick representations for PHOENIX training.

Supported input formats:
1. Betfair Stream API files (JSON/JSONL, .json, .json.gz, .bz2)
   Format contains marketDefinition, rc (runner changes), ltp, batb, batl, tv.
2. Betfair Historical CSV files (.csv, .csv.gz)
   Standard historical data downloads with selection prices and settled flags.
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
    result_type: str = "win"
    ticks: list[dict[str, Any]] = field(default_factory=list)
    closing_back_home: Optional[float] = None
    closing_back_away: Optional[float] = None
    closing_lay_home: Optional[float] = None
    closing_lay_away: Optional[float] = None


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

    def parse_stream_lines(
        self, lines: Iterable[str], source_name: str = "stream"
    ) -> list[ParsedHistoricalMatch]:
        """
        Parse a sequence of raw stream lines (JSON objects).
        Handles market definition changes, runner mappings, and price updates.
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
                        "runners": {},  # selection_id -> {"name": str, "back": float, "lay": float, "vol_b": float, "vol_l": float}
                        "ticks": [],
                        "last_tick_time": None,
                        "first_time": timestamp,
                        "last_time": timestamp,
                        "winner_selection_id": None,
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
                            m_state["runners"][r_id] = {
                                "name": r_name,
                                "back": None,
                                "lay": None,
                                "vol_b": None,
                                "vol_l": None,
                            }
                        elif r_name:
                            m_state["runners"][r_id]["name"] = r_name

                        if status == "WINNER":
                            m_state["winner_selection_id"] = r_id

                # Runner changes (rc)
                rc_list = mc.get("rc", [])
                for rc in rc_list:
                    r_id = rc.get("id")
                    if r_id not in m_state["runners"]:
                        m_state["runners"][r_id] = {
                            "name": f"Runner_{r_id}",
                            "back": None,
                            "lay": None,
                            "vol_b": None,
                            "vol_l": None,
                        }

                    runner = m_state["runners"][r_id]

                    # Best available to back [level, price, size]
                    batb = rc.get("batb")
                    if batb and len(batb) > 0 and len(batb[0]) >= 2:
                        runner["back"] = float(batb[0][1])
                        if len(batb[0]) >= 3:
                            runner["vol_b"] = float(batb[0][2])

                    # Best available to lay [level, price, size]
                    batl = rc.get("batl")
                    if batl and len(batl) > 0 and len(batl[0]) >= 2:
                        runner["lay"] = float(batl[0][1])
                        if len(batl[0]) >= 3:
                            runner["vol_l"] = float(batl[0][2])

                    # Last traded price fallback
                    ltp = rc.get("ltp")
                    if ltp is not None:
                        if runner["back"] is None:
                            runner["back"] = float(ltp)
                        if runner["lay"] is None:
                            runner["lay"] = float(ltp) * 1.02

                # Sample tick if minimum time interval elapsed
                last_time = m_state["last_tick_time"]
                elapsed = (timestamp - last_time).total_seconds() if last_time else 999.0

                if elapsed >= self.resample_seconds:
                    tick = self._extract_stream_tick(m_state, timestamp)
                    if tick:
                        m_state["ticks"].append(tick)
                        m_state["last_tick_time"] = timestamp

        # Post-process parsed markets into ParsedHistoricalMatch objects
        parsed_matches: list[ParsedHistoricalMatch] = []
        for market_id, m_state in markets.items():
            match = self._build_match_from_stream(market_id, m_state)
            if match and len(match.ticks) >= self.min_ticks:
                parsed_matches.append(match)

        return parsed_matches

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

    def _extract_stream_tick(
        self, m_state: dict[str, Any], timestamp: datetime
    ) -> Optional[dict[str, Any]]:
        """Extract a single snapshot tick from stream state."""
        runners = list(m_state["runners"].values())
        if len(runners) < 2:
            return None

        r0 = runners[0]
        r1 = runners[1]

        back_home = r0.get("back")
        lay_home = r0.get("lay")
        back_away = r1.get("back")
        lay_away = r1.get("lay")

        # Must have at least one valid price
        if not (back_home or back_away):
            return None

        # Clean/validate odds
        back_home = self._validate_odds(back_home)
        lay_home = self._validate_odds(lay_home)
        back_away = self._validate_odds(back_away)
        lay_away = self._validate_odds(lay_away)

        # Spread consistency check: back <= lay
        if back_home and lay_home and back_home > lay_home:
            lay_home = back_home * 1.02
        if back_away and lay_away and back_away > lay_away:
            lay_away = back_away * 1.02

        draw_back = None
        draw_lay = None
        if len(runners) > 2:
            r2 = runners[2]
            draw_back = self._validate_odds(r2.get("back"))
            draw_lay = self._validate_odds(r2.get("lay"))

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
            "volume_back_home": r0.get("vol_b"),
            "volume_lay_home": r0.get("vol_l"),
            "volume_back_away": r1.get("vol_b"),
            "volume_lay_away": r1.get("vol_l"),
            "implied_prob_home": ip_home,
            "implied_prob_away": ip_away,
            "overround": overround,
            "is_live": True,
        }

    def _build_match_from_stream(
        self, market_id: str, m_state: dict[str, Any]
    ) -> Optional[ParsedHistoricalMatch]:
        """Convert accumulated stream state into ParsedHistoricalMatch."""
        ticks = m_state["ticks"]
        if not ticks:
            return None

        m_def = m_state["market_def"]
        event_name = m_def.get("eventName", "")
        competition = m_def.get("competitionName", "") or m_def.get("eventName", "")

        runners = list(m_state["runners"].items())
        if len(runners) < 2:
            return None

        r0_id, r0_info = runners[0]
        r1_id, r1_info = runners[1]

        team_home = self._clean_team_name(r0_info.get("name") or "Home")
        team_away = self._clean_team_name(r1_info.get("name") or "Away")

        # Parse event name "Team A v Team B" if runner names are generic
        if ("Runner_" in team_home or "Home" == team_home) and (" v " in event_name or " vs " in event_name):
            parts = re.split(r"\s+v(?:s)?\.?\s+", event_name, flags=re.IGNORECASE)
            if len(parts) >= 2:
                team_home = self._clean_team_name(parts[0])
                team_away = self._clean_team_name(parts[1])

        # Determine winner
        winner = ""
        loser = ""
        winner_id = m_state.get("winner_selection_id")
        if winner_id == r0_id:
            winner = team_home
            loser = team_away
        elif winner_id == r1_id:
            winner = team_away
            loser = team_home
        else:
            # Infer from last tick closing odds
            last_tick = ticks[-1]
            last_b_home = last_tick.get("back_home") or 0.0
            last_b_away = last_tick.get("back_away") or 0.0
            if 1.0 < last_b_home <= 1.15 and last_b_away >= 5.0:
                winner = team_home
                loser = team_away
            elif 1.0 < last_b_away <= 1.15 and last_b_home >= 5.0:
                winner = team_away
                loser = team_home

        match_id = f"bf_{market_id.replace('.', '_')}"

        # Assign match metadata to all ticks
        for t in ticks:
            t["match_id"] = match_id
            t["team_home"] = team_home
            t["team_away"] = team_away
            t["competition"] = competition

        last_tick = ticks[-1]
        start_time = ticks[0]["timestamp"]
        completed_at = last_tick["timestamp"]

        return ParsedHistoricalMatch(
            match_id=match_id,
            team_home=team_home,
            team_away=team_away,
            competition=competition,
            start_time=start_time,
            completed_at=completed_at,
            winner=winner,
            loser=loser,
            result_type="win" if winner else "no_result",
            ticks=ticks,
            closing_back_home=last_tick.get("back_home"),
            closing_back_away=last_tick.get("back_away"),
            closing_lay_home=last_tick.get("lay_home"),
            closing_lay_away=last_tick.get("lay_away"),
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

