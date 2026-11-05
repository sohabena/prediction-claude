"""S102: verify every historical-odds store before any model is judged on it.

Reads ``data/historical_odds/<source>/`` for each source (S98 contract), the Cricsheet matches in ``data/raw/``, runs
the checks in ``training/historical_odds_verify.py`` and writes::

    reports/historical_odds/verify-<UTC date>/report.json
    reports/historical_odds/verify-<UTC date>/report.md

Usage::

    python scripts/verify_historical_odds.py
    python scripts/verify_historical_odds.py --root data/historical_odds --out reports/historical_odds

Exit code 1 when any check is FAIL, 0 otherwise. No database, no network.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from training import historical_odds as ho  # noqa: E402
from training.historical_odds_verify import (  # noqa: E402
    load_cricsheet_matches,
    render_markdown,
    verify,
)

RAW_DIR = ROOT / "data" / "raw"
DEFAULT_ROOT = ROOT / "data" / "historical_odds"
DEFAULT_OUT = ROOT / "reports" / "historical_odds"


def _team_alias() -> dict[str, str]:
    """Franchise and national-team aliases shared with the Cricsheet harmonizer (as build_training_dataset does)."""
    from scripts.cricsheet_harmonizer import CANONICAL_TEAMS

    return dict(CANONICAL_TEAMS)


def run(
    root: Path,
    out: Path,
    *,
    raw_dir: Path = RAW_DIR,
    aliases: Optional[Mapping[str, str]] = None,
    now: Optional[datetime] = None,
) -> tuple[dict[str, Any], Path]:
    """Read the stores, verify them and write ``report.json`` and ``report.md``; returns (result, folder)."""
    records_by_source: dict[str, list[ho.OddsRecord]] = {}
    manifests: dict[str, dict[str, Any]] = {}
    read_errors: dict[str, str] = {}
    for source in ho.SOURCES:
        try:
            records, manifest = ho.read_store(source, root=Path(root))
        except (OSError, ValueError, EOFError, KeyError, TypeError) as exc:
            records, manifest = [], {}
            read_errors[source] = f"{type(exc).__name__}: {exc}"
        records_by_source[source] = records
        manifests[source] = {
            key: manifest.get(key) for key in ("contract_version", "created_at", "provenance", "problems")
        }

    cricsheet, unreadable = load_cricsheet_matches(Path(raw_dir))
    result = verify(
        records_by_source,
        cricsheet,
        aliases=_team_alias() if aliases is None else aliases,
        now=now,
        read_errors=read_errors,
    )
    result["manifests"] = manifests
    result["cricsheet_files"] = {"read": len(cricsheet), "unreadable": unreadable}

    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    folder = Path(out) / f"verify-{stamp}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "report.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    (folder / "report.md").write_text(render_markdown(result), encoding="utf-8")
    return result, folder


def _print_summary(result: Mapping[str, Any], folder: Path) -> None:
    print(f"Historical odds verification: {result['status']}")
    for source, info in result["sources"].items():
        print(f"  {source}: {info['records']} records, {info['events']} events")
    for name, check in result["checks"].items():
        print(f"  {check['status']:4} {check['title']}")
    print(f"Report: {folder / 'report.md'}")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Verify the historical-odds stores (S102). No network, no database.")
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="store root (default data/historical_odds)")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="report folder (default reports/historical_odds)")
    parser.add_argument("--raw-dir", default=str(RAW_DIR), help="Cricsheet JSON folder (default data/raw)")
    args = parser.parse_args(argv)
    result, folder = run(Path(args.root), Path(args.out), raw_dir=Path(args.raw_dir))
    _print_summary(result, folder)
    return 1 if result["status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
