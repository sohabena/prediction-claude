"""
Cricsheet Data Downloader for PHOENIX.
Downloads official international and franchise cricket match data archives:
- Men's T20 Internationals (T20 format)
- Men's One-Day Internationals (ODI format)
- Men's Test Matches (Test format)
- Indian Premier League (T20 format / Franchise)
"""

from __future__ import annotations

import io
import os
import sys
import zipfile
import urllib.request
from pathlib import Path

DATA_RAW_DIR = Path("data/raw")

ARCHIVES = {
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


def download_and_extract_archive(key: str, info: dict) -> int:
    target_dir = DATA_RAW_DIR / key
    target_dir.mkdir(parents=True, exist_ok=True)
    
    url = info["url"]
    desc = info["description"]
    print(f"\n[DOWNLOAD] Fetching {desc} from {url}...")
    
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PHOENIX/2.0"}
    req = urllib.request.Request(url, headers=headers)
    
    with urllib.request.urlopen(req, timeout=60) as resp:
        content = resp.read()
        print(f"           Downloaded {len(content) / (1024*1024):.2f} MB. Extracting...")
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            json_members = [m for m in zf.infolist() if m.filename.endswith(".json")]
            zf.extractall(path=target_dir, members=json_members)
            print(f"           Extracted {len(json_members)} match JSON files to {target_dir}")
            return len(json_members)


def main():
    DATA_RAW_DIR.mkdir(parents=True, exist_ok=True)
    total_files = 0
    print("=" * 65)
    print(" PHOENIX - Official International Cricket Data Downloader")
    print("=" * 65)
    
    for key, info in ARCHIVES.items():
        try:
            count = download_and_extract_archive(key, info)
            total_files += count
        except Exception as e:
            print(f"[ERROR] Failed to download {key}: {e}")
            
    print("\n" + "=" * 65)
    print(f" Download completed successfully! Total official match files: {total_files}")
    print("=" * 65)


if __name__ == "__main__":
    main()

