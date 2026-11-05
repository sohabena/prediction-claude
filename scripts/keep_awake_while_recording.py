"""Keep Windows awake while the scraper container is running (Windows host only).

Uses the documented ``SetThreadExecutionState`` API, the same mechanism media players and PowerToys
Awake use: it asks Windows not to enter idle sleep while this process holds the request. It does
not change power settings, and it cannot stop sleep from a closed lid, the Start menu, or a power
button; those follow the user's own power plan.

Behaviour: every ``--interval`` seconds, check ``docker inspect phoenix-scraper``. While the
container is running, hold the "system required" request; when it is not (or Docker is down),
release it so the PC can sleep normally. Writes one JSON line per state change to
``logs/keep_awake.log``.

Usage::

    python scripts/keep_awake_while_recording.py            # run until stopped
    python scripts/keep_awake_while_recording.py --once     # print the current state and exit
"""

from __future__ import annotations

import argparse
import ctypes
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = ROOT / "logs" / "keep_awake.log"
CONTAINER = "phoenix-scraper"

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def container_running(name: str = CONTAINER) -> bool:
    """True when ``docker inspect`` reports the container as running."""
    try:
        out = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", name],
            capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return out.returncode == 0 and out.stdout.strip().lower() == "true"


def set_awake(hold: bool) -> bool:
    """Hold (or release) the Windows 'system required' request. Returns False off Windows."""
    windll = getattr(ctypes, "windll", None)
    if windll is None:
        return False
    flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED if hold else ES_CONTINUOUS
    return bool(windll.kernel32.SetThreadExecutionState(flags))


def _log(event: str, **fields: object) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = {"time": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")


def run(
    interval: float = 60.0,
    *,
    is_running: Callable[[], bool] = container_running,
    apply: Callable[[bool], bool] = set_awake,
    sleep: Callable[[float], None] = time.sleep,
    max_cycles: Optional[int] = None,
) -> None:
    """Poll the container and hold the awake request only while it runs."""
    holding: Optional[bool] = None
    cycles = 0
    try:
        while max_cycles is None or cycles < max_cycles:
            want = is_running()
            if want != holding:
                ok = apply(want)
                _log("keep_awake" if want else "allow_sleep", container=CONTAINER, api_ok=ok)
                holding = want
            elif want:
                apply(True)  # refresh; harmless and keeps the request alive
            cycles += 1
            sleep(interval)
    finally:
        apply(False)
        _log("released", reason="exit")


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--interval", type=float, default=60.0)
    p.add_argument("--once", action="store_true", help="print whether the scraper container is running and exit")
    args = p.parse_args(argv)
    if args.once:
        print(json.dumps({"container": CONTAINER, "running": container_running()}))
        return 0
    if sys.platform != "win32":
        print("keep_awake_while_recording: Windows only", file=sys.stderr)
        return 2
    run(args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
