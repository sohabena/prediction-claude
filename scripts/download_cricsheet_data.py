"""
Cricsheet Data Downloader for PHOENIX.

Primary dataset (S67): ``all_json.zip``, every Cricsheet match in every format and league (international,
franchise and domestic), extracted to ``data/raw/all/``. Each file is classified from its ``info`` block:

- ``info.match_type``: T20 / IT20 -> t20, ODI / ODM -> odi, Test / MDM -> test
- ``info.gender``: male / female (anything else is reported as ``unknown``)

Legacy per-format archives (t20s, odis, tests, ipl) remain available with ``--legacy`` and their folders still
work: the ratings read ``raw/all`` and the per-format folders together, and a match id present in both is read once.

Usage:
    python scripts/download_cricsheet_data.py               # download all_json.zip into data/raw/all
    python scripts/download_cricsheet_data.py --legacy      # also the four per-format archives
    python scripts/download_cricsheet_data.py --dry-run     # HEAD requests only: prints URLs and sizes, downloads nothing
"""

from __future__ import annotations

import argparse
import io
import json
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

DATA_RAW_DIR = Path("data/raw")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PHOENIX/2.0"

ALL_ARCHIVE_KEY = "all"
ALL_ARCHIVE = {
    "url": "https://cricsheet.org/downloads/all_json.zip",
    "description": "Every Cricsheet match (all formats and leagues)",
    "format": "mixed",
}

LEGACY_ARCHIVES = {
    "t20s": {
        "url": "https://cricsheet.org/downloads/t20s_json.zip",
        "description": "Men's T20 Internationals",
        "format": "T20",
    },
    "odis": {
        "url": "https://cricsheet.org/downloads/odis_json.zip",
        "description": "Men's One-Day Internationals",
        "format": "ODI",
    },
    "tests": {
        "url": "https://cricsheet.org/downloads/tests_json.zip",
        "description": "Men's Test Matches",
        "format": "Test",
    },
    "ipl": {
        "url": "https://cricsheet.org/downloads/ipl_json.zip",
        "description": "Indian Premier League",
        "format": "T20",
    },
}

# Kept under the old name: every archive the script knows, primary first.
ARCHIVES = {ALL_ARCHIVE_KEY: ALL_ARCHIVE, **LEGACY_ARCHIVES}

# Cricsheet info.match_type to the family used by the ratings and the training dataset.
FAMILY_BY_MATCH_TYPE = {
    "T20": "t20",
    "IT20": "t20",
    "ODI": "odi",
    "ODM": "odi",
    "TEST": "test",
    "MDM": "test",
}


def classify_info(info: Mapping[str, Any]) -> tuple[str, str]:
    """``(family, gender)`` of one Cricsheet match from its ``info`` block. Unknown values give ``"unknown"``."""
    family = FAMILY_BY_MATCH_TYPE.get(str(info.get("match_type") or "").strip().upper(), "unknown")
    gender = str(info.get("gender") or "").strip().lower()
    return family, gender if gender in ("male", "female") else "unknown"


def classify_archive(content: bytes) -> Counter:
    """Count ``(family, gender)`` over the match JSON files of a zip, without extracting anything."""
    counts: Counter = Counter()
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        for member in zf.infolist():
            if not member.filename.endswith(".json"):
                continue
            try:
                info = json.loads(zf.read(member)).get("info") or {}
            except (ValueError, OSError):
                counts[("unreadable", "unknown")] += 1
                continue
            counts[classify_info(info)] += 1
    return counts


def _request(url: str, method: str = "GET") -> urllib.request.Request:
    return urllib.request.Request(url, headers={"User-Agent": USER_AGENT}, method=method)


def head_archive(key: str, info: Mapping[str, Any]) -> dict[str, Any]:
    """HEAD request only: the archive's URL and advertised size. Never downloads the body."""
    with urllib.request.urlopen(_request(info["url"], method="HEAD"), timeout=60) as resp:
        length = resp.headers.get("Content-Length")
    size_mb = round(int(length) / (1024 * 1024), 2) if length and length.isdigit() else None
    return {"key": key, "url": info["url"], "size_mb": size_mb}


def download_and_extract_archive(key: str, info: Mapping[str, Any], data_root: Optional[Path] = None) -> int:
    target_dir = (data_root or DATA_RAW_DIR) / key
    target_dir.mkdir(parents=True, exist_ok=True)

    url = info["url"]
    desc = info["description"]
    print(f"\n[DOWNLOAD] Fetching {desc} from {url}...")

    with urllib.request.urlopen(_request(url), timeout=120) as resp:
        content = resp.read()
    print(f"           Downloaded {len(content) / (1024*1024):.2f} MB. Classifying and extracting...")
    counts = classify_archive(content)
    _print_counts(counts)
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        json_members = [m for m in zf.infolist() if m.filename.endswith(".json")]
        zf.extractall(path=target_dir, members=json_members)
        print(f"           Extracted {len(json_members)} match JSON files to {target_dir}")
        return len(json_members)


def _print_counts(counts: Mapping[tuple[str, str], int]) -> None:
    for (family, gender), n in sorted(counts.items()):
        print(f"           {family:<10} {gender:<8} {n}")


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--legacy", action="store_true", help="also download the four per-format archives")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="print URLs and sizes using HEAD requests only; downloads and writes nothing",
    )
    args = parser.parse_args(argv)

    keys = [ALL_ARCHIVE_KEY] + (list(LEGACY_ARCHIVES) if args.legacy else [])
    selected = {key: ARCHIVES[key] for key in keys}

    print("=" * 65)
    print(" PHOENIX - Cricsheet Data Downloader")
    print("=" * 65)

    if args.dry_run:
        for key, info in selected.items():
            try:
                row = head_archive(key, info)
                size = "unknown size" if row["size_mb"] is None else f"{row['size_mb']:.2f} MB"
                print(f"[DRY RUN] {key:<6} {row['url']}  {size}")
            except Exception as exc:
                print(f"[DRY RUN] {key:<6} {info['url']}  HEAD failed: {exc}")
        print("\nDry run only: nothing downloaded, nothing written.")
        return

    DATA_RAW_DIR.mkdir(parents=True, exist_ok=True)
    total_files = 0
    for key, info in selected.items():
        try:
            total_files += download_and_extract_archive(key, info)
        except Exception as exc:
            print(f"[ERROR] Failed to download {key}: {exc}")

    print("\n" + "=" * 65)
    print(f" Download completed. Total match files extracted: {total_files}")
    print("=" * 65)


if __name__ == "__main__":
    main()
