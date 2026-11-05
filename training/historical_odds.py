"""S98: the shared contract for bought or published historical cricket match odds.

Every historical-odds source (S99 aussportsbetting, S100 OddsPapi, S101 The Odds API) converts its data into
:class:`OddsRecord` rows and writes them with :func:`write_store`. The verifier (S102) and the R1 benchmark (S103)
read them back with :func:`read_store`. Nothing here touches the database or the network.

Store layout (``data/`` is gitignored)::

    data/historical_odds/incoming/<source>/   files the operator downloaded by hand (never edited)
    data/historical_odds/raw/<source>/        cached API responses, so a re-run never spends credits twice
    data/historical_odds/<source>/records.jsonl.gz
    data/historical_odds/<source>/manifest.json

Golden Rulebook: Betfair data is never used in any form (rule 3); historical odds are bought or published, never
scraped. :func:`write_store` refuses any record whose bookmaker is Betfair.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import math
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from shared.naming import canonical_team_name
from training.market import devig_power

DEFAULT_ROOT = Path("data/historical_odds")
RECORDS_FILE = "records.jsonl.gz"
MANIFEST_FILE = "manifest.json"
CONTRACT_VERSION = 1

SOURCES = ("aussportsbetting", "oddspapi", "the_odds_api")
FORMATS = ("t20", "odi", "test", "other")
GENDERS = ("male", "female")
KINDS = ("open", "close", "min", "max", "average", "snapshot")
WINNERS = ("home", "away", "tie", "no_result")
FORBIDDEN_BOOKMAKER_TOKENS = ("betfair",)


class ForbiddenSourceError(ValueError):
    """A record came from a source the operator ruled out (Betfair)."""


@dataclass(frozen=True)
class OddsRecord:
    """Two-way match-winner prices for one match, one bookmaker, one moment (decimal odds)."""

    source: str
    source_event_id: str
    competition: str
    format: str
    gender: str
    start_time_utc: datetime
    home: str
    away: str
    bookmaker: str
    kind: str
    price_home: float
    price_away: float
    snapshot_time_utc: Optional[datetime] = None
    price_draw: Optional[float] = None
    winner: Optional[str] = None


def is_betfair(bookmaker: Any) -> bool:
    """True when a bookmaker name or slug is Betfair in any form (``betfair_ex_uk``, ``Betfair Exchange``)."""
    text = str(bookmaker or "").casefold().replace(" ", "").replace("-", "").replace("_", "")
    return any(token in text for token in FORBIDDEN_BOOKMAKER_TOKENS)


def normalise_team(name: Any, aliases: Optional[Mapping[str, str]] = None) -> str:
    """Whitespace-normalised team name, mapped through ``aliases`` (exact match on the normalised name)."""
    text = canonical_team_name(name)
    if aliases:
        text = aliases.get(text, text)
    return text


def _valid_price(value: Any) -> bool:
    try:
        price = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(price) and price > 1.0


def validate(record: OddsRecord) -> list[str]:
    """Problems with one record (empty when it is usable). Codes are stable; the verifier counts them."""
    problems: list[str] = []
    if record.source not in SOURCES:
        problems.append("unknown_source")
    if not str(record.source_event_id or "").strip():
        problems.append("missing_event_id")
    if record.format not in FORMATS:
        problems.append("unknown_format")
    if record.gender not in GENDERS:
        problems.append("unknown_gender")
    if record.kind not in KINDS:
        problems.append("unknown_kind")
    if not isinstance(record.start_time_utc, datetime) or record.start_time_utc.tzinfo is None:
        problems.append("start_not_utc")
    if record.snapshot_time_utc is not None and record.snapshot_time_utc.tzinfo is None:
        problems.append("snapshot_not_utc")
    home, away = normalise_team(record.home), normalise_team(record.away)
    if not home or not away:
        problems.append("missing_team")
    elif home.casefold() == away.casefold():
        problems.append("same_team")
    if not _valid_price(record.price_home) or not _valid_price(record.price_away):
        problems.append("bad_price")
    if record.price_draw is not None and not _valid_price(record.price_draw):
        problems.append("bad_draw_price")
    if record.winner is not None and record.winner not in WINNERS:
        problems.append("unknown_winner")
    if is_betfair(record.bookmaker):
        problems.append("forbidden_bookmaker")
    return problems


def overround(record: OddsRecord) -> float:
    """Booksum of the two-way prices (plus the draw when quoted), e.g. 1.05 for a 5% margin."""
    total = 1.0 / float(record.price_home) + 1.0 / float(record.price_away)
    if record.price_draw is not None:
        total += 1.0 / float(record.price_draw)
    return total


def p_home_fair(record: OddsRecord) -> float:
    """De-vigged P(home wins) from the two-way prices (power method, the research default)."""
    p_home, _ = devig_power(1.0 / float(record.price_home), 1.0 / float(record.price_away))
    return p_home


def _encode(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return value


def _decode_time(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def record_to_dict(record: OddsRecord) -> dict[str, Any]:
    return {key: _encode(value) for key, value in asdict(record).items()}


def record_from_dict(row: Mapping[str, Any]) -> OddsRecord:
    names = {f.name for f in fields(OddsRecord)}
    data = {key: row.get(key) for key in names}
    data["start_time_utc"] = _decode_time(data["start_time_utc"])
    data["snapshot_time_utc"] = _decode_time(data["snapshot_time_utc"])
    for key in ("price_home", "price_away"):
        data[key] = float(data[key])
    if data["price_draw"] is not None:
        data["price_draw"] = float(data["price_draw"])
    return OddsRecord(**data)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_store(
    source: str,
    records: Sequence[OddsRecord],
    *,
    provenance: str,
    inputs: Iterable[Path] = (),
    extra: Optional[Mapping[str, Any]] = None,
    root: Path = DEFAULT_ROOT,
) -> Path:
    """Write one source's records and manifest; returns the source folder.

    Refuses (``ForbiddenSourceError``) if any record is from Betfair, and ``ValueError`` if a record names another
    source. Invalid records are still written (the verifier reports them); their problem counts go in the manifest.
    ``provenance`` says where the prices come from in the source's own words (bookmakers, licence, URL).
    """
    if source not in SOURCES:
        raise ValueError(f"unknown source {source!r}")
    forbidden = sorted({r.bookmaker for r in records if is_betfair(r.bookmaker)})
    if forbidden:
        raise ForbiddenSourceError(f"Betfair data is never used: {forbidden}")
    if is_betfair(provenance):
        raise ForbiddenSourceError("the provenance names Betfair as the price source")
    others = sorted({r.source for r in records if r.source != source})
    if others:
        raise ValueError(f"records from other sources in the {source} store: {others}")
    folder = Path(root) / source
    folder.mkdir(parents=True, exist_ok=True)
    problems: dict[str, int] = {}
    with gzip.open(folder / RECORDS_FILE, "wt", encoding="utf-8") as handle:
        for record in records:
            for code in validate(record):
                problems[code] = problems.get(code, 0) + 1
            handle.write(json.dumps(record_to_dict(record), separators=(",", ":")) + "\n")
    manifest = {
        "contract_version": CONTRACT_VERSION,
        "source": source,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "provenance": provenance,
        "records": len(records),
        "events": len({r.source_event_id for r in records}),
        "bookmakers": sorted({r.bookmaker for r in records}),
        "kinds": sorted({r.kind for r in records}),
        "problems": dict(sorted(problems.items())),
        "inputs": [{"path": str(p), "sha256": sha256_of(Path(p)), "bytes": Path(p).stat().st_size} for p in inputs],
        **dict(extra or {}),
    }
    (folder / MANIFEST_FILE).write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    return folder


def read_store(source: str, *, root: Path = DEFAULT_ROOT) -> tuple[list[OddsRecord], dict[str, Any]]:
    """Records and manifest of one source; ``([], {})`` when the source has no store yet."""
    folder = Path(root) / source
    if not (folder / RECORDS_FILE).exists():
        return [], {}
    with gzip.open(folder / RECORDS_FILE, "rt", encoding="utf-8") as handle:
        records = [record_from_dict(json.loads(line)) for line in handle if line.strip()]
    manifest_path = folder / MANIFEST_FILE
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    return records, manifest


def link_detail(record: OddsRecord, index: Any) -> Optional[dict[str, Any]]:
    """Cricsheet link for a record via ``training.dataset.CricsheetIndex`` (same teams, date within one day).

    Returns ``{cricsheet_match_id, reversed, day_delta}`` (``reversed``: Cricsheet lists our away side first) or
    None when no single match fits.
    """
    return index.link_detail(
        {"team_home": record.home, "team_away": record.away, "match_start_time": record.start_time_utc}
    )
