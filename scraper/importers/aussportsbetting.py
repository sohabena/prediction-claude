"""S99: parse an aussportsbetting.com historical odds file (Big Bash League and similar) into S98 odds records.

Pure: it reads a file the operator saved by hand and never touches the network or the database. The layout is found
by header name, not position: note rows above a header row that holds ``Home Team`` and ``Away Team``, then ``Date``,
a start time, scores, the odds columns (``Home Odds``, ``Home Odds Open/Min/Max/Close`` and the same for ``Away``),
and optional ``Notes``.

Each row becomes one :class:`training.historical_odds.OddsRecord` per odds kind with both prices valid. Start times
are read as Australia/Sydney local time and converted to UTC; a date with no time becomes 09:00 UTC on that date
(Cricsheet links within one day). The winner comes from a result column when present, otherwise from the scores when
both are plain numbers and differ. That second rule is a guess (a score is not always a winner: DLS can make it
wrong), so S102 checks it against Cricsheet.

Golden Rulebook: historical odds are bought or published, never scraped (section 8); a note that names Betfair as the
price source is refused by ``training.historical_odds.write_store`` (rule 3).
"""

from __future__ import annotations

import csv
import io
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from training import historical_odds as ho

SOURCE = "aussportsbetting"
BOOKMAKER = "aussportsbetting"
FORMAT = "t20"
SOURCE_PAGE = "https://www.aussportsbetting.com"
SYDNEY = ZoneInfo("Australia/Sydney")
EXCEL_EPOCH = datetime(1899, 12, 30)
DATE_ONLY_UTC_HOUR = 9
SPREADSHEET_SUFFIXES = (".xlsx", ".xlsm", ".xls", ".csv")

# File-name tokens (checked in order) that name the competition. Anything else is assumed to be the Big Bash League.
COMPETITION_TOKENS = (
    ("wbbl", "Women's Big Bash League", "female"),
    ("women", "Women's Big Bash League", "female"),
    ("bbl", "Big Bash League", "male"),
    ("big bash", "Big Bash League", "male"),
)
DEFAULT_COMPETITION = ("Big Bash League", "male")

# Spellings from the file mapped to the canonical name. Extend once the operator's file has been read.
TEAM_ALIASES: dict[str, str] = {}

# Header names (casefolded, whitespace collapsed) and the role each plays.
ROLE_BY_HEADER = {
    "home team": "home",
    "away team": "away",
    "date": "date",
    "match date": "date",
    "kick-off (local)": "clock",
    "kick off (local)": "clock",
    "kickoff (local)": "clock",
    "start time": "clock",
    "home score": "home_score",
    "away score": "away_score",
    "winner": "result",
    "result": "result",
    "match result": "result",
    "winning team": "result",
    "bookmakers surveyed": "surveyed",
    "notes": "notes",
}
ODDS_HEADER = re.compile(r"^(home|away) odds(?: (open|min|max|close))?$")
TIE_WORDS = ("tie", "tied", "draw")
NO_RESULT_WORDS = ("no result", "nr", "no-result", "abandoned", "cancelled", "canceled")

# A note that says plain "Home Odds" are closing (or opening) prices. "Home Odds Close" itself does not count.
_PLAIN_NOTE_CLOSE = re.compile(r"\b(?:home|away)\s+odds\b(?!\s+(?:open|min|max|close))[^.\n]{0,60}?\bclos", re.I)
_PLAIN_NOTE_OPEN = re.compile(r"\b(?:home|away)\s+odds\b(?!\s+(?:open|min|max|close))[^.\n]{0,60}?\bopen", re.I)

DATE_FORMATS = (
    "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d %b %Y", "%d %B %Y", "%a %d %b %Y", "%a %d %B %Y",
    "%b %d %Y", "%B %d %Y", "%d %b %y",
)
TIME_FORMATS = ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M%p", "%I:%M:%S %p", "%I %p", "%I%p")


@dataclass
class ParseResult:
    """What one file gave: records, the notes above the header, unknown columns, skipped rows and counts."""

    path: Path
    competition: str
    gender: str
    records: list[ho.OddsRecord] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    unknown_columns: list[str] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    plain_odds_kind: str = "average"
    teams: list[str] = field(default_factory=list)

    @property
    def events(self) -> int:
        return len({record.source_event_id for record in self.records})

    def count(self, name: str, n: int = 1) -> None:
        self.counts[name] = self.counts.get(name, 0) + n


def competition_for_file(path: Path) -> tuple[str, str, bool]:
    """(competition, gender, named) from the file name; ``named`` is False when the default was used."""
    name = Path(path).stem.casefold()
    for token, competition, gender in COMPETITION_TOKENS:
        if token in name:
            return competition, gender, True
    return DEFAULT_COMPETITION[0], DEFAULT_COMPETITION[1], False


def plain_odds_kind_from_notes(notes: list[str]) -> tuple[str, Optional[str]]:
    """Kind of the plain ``Home Odds`` / ``Away Odds`` columns: ``average`` unless the notes say otherwise."""
    text = " . ".join(notes)
    closing = bool(_PLAIN_NOTE_CLOSE.search(text))
    opening = bool(_PLAIN_NOTE_OPEN.search(text))
    if closing and not opening:
        return "close", "notes say plain Home/Away Odds are closing prices; recorded as close, check the notes"
    if opening and not closing:
        return "open", "notes say plain Home/Away Odds are opening prices; recorded as open, check the notes"
    return "average", None


def parse_workbook(path: Path) -> ParseResult:
    """Parse one aussportsbetting file (.xlsx, .xlsm, .xls or .csv). Raises ValueError when no header is found."""
    path = Path(path)
    competition, gender, named = competition_for_file(path)
    result = ParseResult(path=path, competition=competition, gender=gender)
    if not named:
        result.warnings.append(f"file name names no competition; assumed {competition} ({gender})")

    located: list[tuple[str, list[tuple[int, tuple[Any, ...]]], int, list[str]]] = []
    for sheet_name, rows in _read_sheets(path):
        position = _header_position(rows)
        if position is None:
            result.warnings.append(f"sheet {sheet_name!r} has no Home Team / Away Team header row; ignored")
            continue
        notes = [text for text in (_row_text(row) for _, row in rows[:position]) if text]
        located.append((sheet_name, rows, position, notes))
    if not located:
        raise ValueError(f"{path.name}: no row with 'Home Team' and 'Away Team' in any sheet")

    result.notes = [note for *_, notes in located for note in notes]
    result.plain_odds_kind, plain_message = plain_odds_kind_from_notes(result.notes)
    if plain_message:
        result.warnings.append(plain_message)

    teams: set[str] = set()
    for sheet_name, rows, position, _ in located:
        teams |= _parse_sheet(result, sheet_name, rows, position)
    result.teams = sorted(teams)
    return result


# --- reading the file -------------------------------------------------------------------------------------------

Sheet = tuple[str, list[tuple[int, tuple[Any, ...]]]]


def _read_sheets(path: Path) -> list[Sheet]:
    """Every sheet as (name, [(row number, cells)]); row numbers are 1-based, as Excel shows them."""
    suffix = path.suffix.lower()
    if suffix == ".csv":
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("cp1252")
        rows = [tuple(row) for row in csv.reader(io.StringIO(text))]
        return [("csv", [(number, row) for number, row in enumerate(rows, start=1)])]
    if suffix in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook

        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            return [
                (sheet.title, [(number, tuple(row)) for number, row in enumerate(sheet.iter_rows(values_only=True), 1)])
                for sheet in workbook.worksheets
            ]
        finally:
            workbook.close()
    if suffix == ".xls":
        import pandas as pd

        try:
            frames = pd.read_excel(path, header=None, sheet_name=None, dtype=object)
        except ImportError as exc:
            raise ValueError(f"{path.name}: reading .xls needs the xlrd package; save the file as .xlsx or .csv") from exc
        sheets: list[Sheet] = []
        for name, frame in frames.items():
            rows = [tuple(None if _blank(value) else value for value in row) for row in frame.itertuples(index=False)]
            sheets.append((str(name), [(number, row) for number, row in enumerate(rows, start=1)]))
        return sheets
    raise ValueError(f"{path.name}: not a spreadsheet (expected .xlsx, .xls or .csv)")


def _header_position(rows: list[tuple[int, tuple[Any, ...]]]) -> Optional[int]:
    """Index in ``rows`` of the first row holding both ``Home Team`` and ``Away Team``."""
    for index, (_, row) in enumerate(rows):
        cells = {_text(value).casefold() for value in row}
        if "home team" in cells and "away team" in cells:
            return index
    return None


def _map_columns(header: tuple[Any, ...]) -> tuple[dict[str, int], dict[Optional[str], dict[str, int]], list[str]]:
    """Role -> column index; odds kind (None for plain Home Odds) -> side -> column index; unknown header names."""
    roles: dict[str, int] = {}
    odds: dict[Optional[str], dict[str, int]] = {}
    unknown: list[str] = []
    for position, cell in enumerate(header):
        name = _text(cell)
        key = name.casefold()
        if not key:
            continue
        match = ODDS_HEADER.match(key)
        if match:
            odds.setdefault(match.group(2), {}).setdefault(match.group(1), position)
            continue
        role = ROLE_BY_HEADER.get(key)
        if role is not None and role not in roles:
            roles[role] = position
        else:
            unknown.append(name)
    return roles, odds, unknown


def _odds_kinds(
    result: ParseResult, sheet: str, odds: dict[Optional[str], dict[str, int]], plain_kind: str
) -> dict[str, dict[str, int]]:
    """Kinds that can be recorded (both sides present). Explicit Open/Min/Max/Close columns win over plain odds."""
    kinds: dict[str, dict[str, int]] = {}
    for raw_kind in sorted(odds, key=lambda kind: kind is None):
        sides = odds[raw_kind]
        kind = raw_kind or plain_kind
        if kind in kinds:
            result.warnings.append(f"sheet {sheet!r}: plain Home/Away Odds ignored because {kind} columns exist")
            continue
        missing = [side for side in ("home", "away") if side not in sides]
        if missing:
            result.warnings.append(f"sheet {sheet!r}: {kind} has no {' or '.join(missing)} column; not recorded")
            continue
        kinds[kind] = sides
    return kinds


def _parse_sheet(
    result: ParseResult, sheet: str, rows: list[tuple[int, tuple[Any, ...]]], position: int
) -> set[str]:
    roles, odds, unknown = _map_columns(rows[position][1])
    for name in unknown:
        if name not in result.unknown_columns:
            result.unknown_columns.append(name)
    if "date" not in roles:
        result.warnings.append(f"sheet {sheet!r}: no Date column; every row is skipped")
    if "clock" not in roles:
        result.warnings.append(f"sheet {sheet!r}: no start-time column; start is the date (09:00 UTC) or the date's time")
    kinds = _odds_kinds(result, sheet, odds, result.plain_odds_kind)
    if not kinds:
        result.warnings.append(f"sheet {sheet!r}: no complete odds columns; nothing to record")
    return _parse_rows(result, sheet, rows[position + 1:], roles, kinds)


def _parse_rows(
    result: ParseResult,
    sheet: str,
    rows: list[tuple[int, tuple[Any, ...]]],
    roles: dict[str, int],
    kinds: dict[str, dict[str, int]],
) -> set[str]:
    teams: set[str] = set()
    seen_events: set[str] = set()
    for row_no, row in rows:
        if all(_blank(value) for value in row):
            continue
        result.count("rows")
        home = ho.normalise_team(_cell(row, roles.get("home")), TEAM_ALIASES)
        away = ho.normalise_team(_cell(row, roles.get("away")), TEAM_ALIASES)
        if not home or not away:
            _skip(result, sheet, row_no, "missing_team")
            continue
        raw_date = _cell(row, roles.get("date"))
        day, clock = _split_date_time(raw_date)
        if day is None:
            _skip(result, sheet, row_no, "missing_date" if _blank(raw_date) else "bad_date", _text(raw_date))
            continue
        raw_clock = _cell(row, roles.get("clock"))
        if not _blank(raw_clock):
            clock = _as_time(raw_clock)
            if clock is None:
                _skip(result, sheet, row_no, "bad_time", _text(raw_clock))
                continue
        event_id = f"{day.isoformat()}_{_slug(home)}_{_slug(away)}"
        if event_id in seen_events:
            _skip(result, sheet, row_no, "duplicate_event", event_id)
            continue

        skips_before = len(result.skipped)
        winner, winner_how = _winner(row, roles, home, away)
        start = start_time_utc(day, clock)
        row_records: list[ho.OddsRecord] = []
        for kind, sides in kinds.items():
            values: dict[str, float] = {}
            usable = True
            for side in ("home", "away"):
                raw = _cell(row, sides[side])
                state, price = _price(raw)
                if state == "bad":
                    usable = False
                    _skip(result, sheet, row_no, "bad_price", f"{kind} {side} {_text(raw)}")
                elif state == "blank":
                    usable = False
                    result.count("missing_price")
                else:
                    values[side] = price
            if not usable:
                continue
            record = ho.OddsRecord(
                source=SOURCE, source_event_id=event_id, competition=result.competition, format=FORMAT,
                gender=result.gender, start_time_utc=start, home=home, away=away, bookmaker=BOOKMAKER,
                kind=kind, price_home=values["home"], price_away=values["away"], winner=winner,
            )
            problems = ho.validate(record)
            if problems:
                _skip(result, sheet, row_no, "invalid_record", ",".join(problems))
                continue
            row_records.append(record)

        if not row_records:
            if len(result.skipped) == skips_before:
                _skip(result, sheet, row_no, "no_prices")
            continue
        result.records.extend(row_records)
        result.count("records", len(row_records))
        if winner_how:
            result.count(winner_how)
        seen_events.add(event_id)
        teams.update({home, away})
    return teams


def _skip(result: ParseResult, sheet: str, row_no: int, code: str, detail: str = "") -> None:
    reason = f"{code}: {detail}" if detail else code
    result.skipped.append({"sheet": sheet, "row": row_no, "reason": reason})
    result.count(code)


# --- cell readers ---------------------------------------------------------------------------------------------


def _blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return isinstance(value, str) and not value.strip()


def _text(value: Any) -> str:
    """A cell as one line of text; whole floats lose the decimal point (165.0 -> 165)."""
    if _blank(value):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return " ".join(str(value).split())


def _row_text(row: tuple[Any, ...]) -> str:
    return " ".join(text for text in (_text(value) for value in row) if text)


def _cell(row: tuple[Any, ...], index: Optional[int]) -> Any:
    if index is None or index >= len(row):
        return None
    return row[index]


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")


def _fraction_to_time(fraction: float) -> time:
    seconds = min(86399, round(fraction * 86400))
    return time(seconds // 3600, seconds % 3600 // 60, seconds % 60)


def _parse_text_datetime(text: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    for date_format in DATE_FORMATS:
        for suffix in ("", " %H:%M", " %H:%M:%S", " %I:%M %p"):
            try:
                return datetime.strptime(text, date_format + suffix)
            except ValueError:
                continue
    return None


def _split_date_time(value: Any) -> tuple[Optional[date], Optional[time]]:
    """(day, clock) from a date cell: a datetime, an Excel serial number or text. (None, None) when unreadable."""
    if _blank(value) or isinstance(value, bool):
        return None, None
    if isinstance(value, datetime):
        clock = value.time() if (value.hour or value.minute or value.second) else None
        return value.date(), clock
    if isinstance(value, date):
        return value, None
    if isinstance(value, time):
        return None, None
    if isinstance(value, (int, float)):
        if not math.isfinite(value) or value < 0:
            return None, None
        whole = math.floor(value)
        try:
            day = (EXCEL_EPOCH + timedelta(days=whole)).date()
        except OverflowError:
            return None, None
        if not 1950 <= day.year <= 2100:
            return None, None
        fraction = value - whole
        return day, (_fraction_to_time(fraction) if fraction > 0 else None)
    parsed = _parse_text_datetime(_text(value))
    if parsed is None:
        return None, None
    return parsed.date(), (parsed.time() if (parsed.hour or parsed.minute or parsed.second) else None)


def _as_time(value: Any) -> Optional[time]:
    """A start-time cell as a clock time, or None when it cannot be read."""
    if isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value.time()
    if isinstance(value, time):
        return value
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            return None
        return _fraction_to_time(value - math.floor(value))
    text = _text(value)
    for time_format in TIME_FORMATS:
        try:
            return datetime.strptime(text, time_format).time()
        except ValueError:
            continue
    return None


def start_time_utc(day: date, clock: Optional[time]) -> datetime:
    """Local Sydney start converted to UTC; with no clock, 09:00 UTC on the day."""
    if clock is None:
        return datetime(day.year, day.month, day.day, DATE_ONLY_UTC_HOUR, 0, tzinfo=timezone.utc)
    local = datetime.combine(day, clock.replace(tzinfo=None), tzinfo=SYDNEY)
    return local.astimezone(timezone.utc)


def _price(raw: Any) -> tuple[str, Optional[float]]:
    """("blank" | "bad" | "ok", price). A decimal price must be finite and above 1.0."""
    if _blank(raw):
        return "blank", None
    if isinstance(raw, bool):
        return "bad", None
    try:
        price = float(raw.strip()) if isinstance(raw, str) else float(raw)
    except (TypeError, ValueError):
        return "bad", None
    if not math.isfinite(price) or price <= 1.0:
        return "bad", None
    return "ok", price


def _plain_number(raw: Any) -> Optional[float]:
    """A score as a number when it is plain (12, 12.0, "12"); anything else (e.g. "165/7") is None."""
    if _blank(raw) or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw) if math.isfinite(raw) else None
    text = str(raw).strip()
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return float(text)
    return None


def _winner_word(text: str, home: str, away: str) -> Optional[str]:
    key = text.casefold()
    home_key, away_key = home.casefold(), away.casefold()
    if key in ("home", home_key) or key.startswith(home_key + " "):
        return "home"
    if key in ("away", away_key) or key.startswith(away_key + " "):
        return "away"
    if key in TIE_WORDS:
        return "tie"
    if key in NO_RESULT_WORDS:
        return "no_result"
    return None


def _winner(row: tuple[Any, ...], roles: dict[str, int], home: str, away: str) -> tuple[Optional[str], Optional[str]]:
    """(winner, counter name). A result column wins when it has text; otherwise plain differing scores decide."""
    raw = _text(_cell(row, roles.get("result")))
    if raw:
        winner = _winner_word(raw, home, away)
        return winner, ("winner_from_column" if winner else "winner_unrecognised")
    home_score = _plain_number(_cell(row, roles.get("home_score")))
    away_score = _plain_number(_cell(row, roles.get("away_score")))
    if home_score is None or away_score is None or home_score == away_score:
        return None, None
    return ("home" if home_score > away_score else "away"), "winner_from_scores"
