"""Restore a previous champion pointer for a specialist in ``metadata.json``.

Usage: python scripts/rollback_specialist.py --key ipl_men [--to-version N] [--models-dir DIR]

Only the pointer (``path`` / ``champion_path`` / ``version`` / ``metrics``) changes; model
files are never deleted or overwritten.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT_MODELS_DIR = Path("models/specialists")


def rollback_specialist(
    key: str, to_version: Optional[int] = None, models_dir: Path = DEFAULT_MODELS_DIR
) -> Dict[str, Any]:
    """Point ``metadata.json[key]`` at an earlier champion. Returns a description of the change."""
    models_dir = Path(models_dir)
    meta_path = models_dir / "metadata.json"
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    entry = metadata.get(key)
    if not entry:
        raise KeyError(f"specialist '{key}' not found in {meta_path}")
    history = entry.get("history", [])
    if not history:
        raise ValueError(f"no previous version recorded for '{key}'")

    if to_version is None:
        target = history[-1]
    else:
        matches = [h for h in history if int(h.get("version", -1)) == int(to_version)]
        if not matches:
            known = sorted(int(h.get("version", 0)) for h in history)
            raise ValueError(f"version {to_version} not in history for '{key}' (known: {known})")
        target = matches[-1]

    path = Path(target["path"])
    if not path.exists() and not (models_dir / path.name).exists():
        raise FileNotFoundError(f"model file for version {target.get('version')} is missing: {path}")

    change = {
        "key": key,
        "from_version": entry.get("version"),
        "from_path": entry.get("champion_path") or entry.get("path"),
        "to_version": target.get("version"),
        "to_path": target["path"],
    }
    entry["path"] = target["path"]
    entry["champion_path"] = target["path"]
    entry["version"] = target.get("version")
    entry["metrics"] = target.get("metrics", {})
    entry.setdefault("rollback_log", []).append({**change, "at": datetime.now(timezone.utc).isoformat()})
    tmp = meta_path.with_name(meta_path.name + ".tmp")
    tmp.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    os.replace(tmp, meta_path)
    return change


def main() -> int:
    parser = argparse.ArgumentParser(description="Roll a specialist back to an earlier champion")
    parser.add_argument("--key", required=True)
    parser.add_argument("--to-version", type=int, default=None)
    parser.add_argument("--models-dir", type=Path, default=DEFAULT_MODELS_DIR)
    args = parser.parse_args()
    try:
        change = rollback_specialist(args.key, args.to_version, args.models_dir)
    except (KeyError, ValueError, FileNotFoundError, OSError) as e:
        print(f"[ROLLBACK] failed: {e}", file=sys.stderr)
        return 1
    print(
        f"[ROLLBACK] {change['key']}: v{change['from_version']} ({change['from_path']}) "
        f"-> v{change['to_version']} ({change['to_path']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
