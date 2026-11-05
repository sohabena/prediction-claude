"""Watchdog that keeps the LotusBook recorder running (S37).

Runs ``python -m scraper.manager`` (which calls ``scraper.manager.run_scraper``) in a child process.
It restarts the child with exponential backoff when the child exits, and also when the feed stalls:
a match is live (per the feed heartbeat the scraper writes) but no live event has been processed
for ``SCRAPER_STALL_MINUTES`` (default 10).

Files:
    logs/scraper_watchdog.log   one JSON line per watchdog event
    logs/scraper_heartbeat.json watchdog state (last tick time, matches live, restarts today)
    logs/scraper_child.log      stdout/stderr of the scraper child
    logs/scraper_feed.json      feed heartbeat written by scraper.manager (read only here)

Usage::

    python scripts/run_scraper_forever.py           # run until stopped (Ctrl+C)
    python scripts/run_scraper_forever.py --status  # print the current heartbeat and exit
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.logging import setup_logging  # noqa: E402

logger = setup_logging("scraper_watchdog")

LOG_DIR = ROOT / "logs"
WATCHDOG_LOG = LOG_DIR / "scraper_watchdog.log"
HEARTBEAT_PATH = LOG_DIR / "scraper_heartbeat.json"
CHILD_LOG = LOG_DIR / "scraper_child.log"
FEED_PATH = LOG_DIR / "scraper_feed.json"  # same path as scraper.manager.FEED_STATE_PATH
CHILD_CMD = [sys.executable, "-m", "scraper.manager"]
INSTANCE_FRESH_SECONDS = 120.0  # another live watchdog refreshes its heartbeat more often than this


class ChildProcess(Protocol):
    """The subset of ``subprocess.Popen`` the watchdog uses (fakes implement it in tests)."""

    pid: int

    def poll(self) -> Optional[int]: ...

    def kill(self) -> None: ...

    def wait(self, timeout: Optional[float] = None) -> Optional[int]: ...


@dataclass
class WatchdogConfig:
    stall_minutes: float = 10.0
    poll_seconds: float = 15.0
    backoff_base_seconds: float = 5.0
    backoff_max_seconds: float = 300.0
    healthy_reset_seconds: float = 300.0  # a child that ran this long resets the backoff


@dataclass
class _State:
    restarts_today: int = 0
    restart_day: Optional[date] = None
    consecutive_failures: int = 0
    next_start_at: Optional[datetime] = None
    child_started_at: Optional[datetime] = None
    last_stop_reason: str = ""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def read_feed_state(path: Path = FEED_PATH) -> Optional[dict[str, Any]]:
    """Read the scraper feed heartbeat; None when missing or unreadable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def another_instance_running(path: Path, now: datetime, own_pid: int) -> bool:
    """True when a different watchdog wrote its heartbeat recently."""
    prev = read_feed_state(path)
    if not prev or prev.get("watchdog_pid") in (None, own_pid):
        return False
    beat = _parse_ts(prev.get("updated_at"))
    return beat is not None and (now - beat).total_seconds() < INSTANCE_FRESH_SECONDS


class Watchdog:
    """Supervises one scraper child. All side effects are injected so tests can fake them."""

    def __init__(
        self,
        *,
        spawn: Callable[[], ChildProcess],
        read_feed: Callable[[], Optional[dict[str, Any]]],
        config: WatchdogConfig,
        heartbeat_path: Path = HEARTBEAT_PATH,
        log_path: Optional[Path] = None,
        clock: Callable[[], datetime] = _utcnow,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.spawn = spawn
        self.read_feed = read_feed
        self.config = config
        self.heartbeat_path = heartbeat_path
        self.log_path = log_path
        self.clock = clock
        self.sleep = sleep
        self.child: Optional[ChildProcess] = None
        self.state = _State()

    # ------------------------------------------------------------------ logging
    def _log(self, event: str, **fields: Any) -> None:
        logger.info(event, **fields)
        if self.log_path is None:
            return
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            line = {"ts": self.clock().isoformat(), "event": event, **fields}
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(line, default=str) + "\n")
        except OSError:
            pass

    # ------------------------------------------------------------------ core
    def _roll_day(self, now: datetime) -> None:
        if self.state.restart_day != now.date():
            self.state.restart_day = now.date()
            self.state.restarts_today = 0

    def _start_child(self, now: datetime) -> None:
        self.child = self.spawn()
        self.state.child_started_at = now
        self.state.next_start_at = None
        self._log("child_started", pid=self.child.pid, restarts_today=self.state.restarts_today)

    def _backoff_seconds(self) -> float:
        n = max(1, self.state.consecutive_failures)
        return min(self.config.backoff_base_seconds * (2 ** (n - 1)), self.config.backoff_max_seconds)

    def _after_failure(self, now: datetime, reason: str) -> None:
        uptime = (now - self.state.child_started_at).total_seconds() if self.state.child_started_at else 0.0
        if uptime >= self.config.healthy_reset_seconds:
            self.state.consecutive_failures = 0
        self.state.consecutive_failures += 1
        self.state.restarts_today += 1
        self.state.last_stop_reason = reason
        delay = self._backoff_seconds()
        self.state.next_start_at = now + timedelta(seconds=delay)
        self.child = None
        self._log("child_restart_scheduled", reason=reason, delay_s=delay,
                  restarts_today=self.state.restarts_today)

    def _kill_child(self) -> None:
        if self.child is None:
            return
        try:
            self.child.kill()
            self.child.wait(timeout=30)
        except Exception as e:  # noqa: BLE001 - a dead or wedged child must not stop the watchdog
            self._log("child_kill_error", error=str(e))

    def is_stalled(self, now: datetime, feed: Optional[dict[str, Any]]) -> bool:
        """A live match is known, and no live event has been processed for the stall window."""
        started = self.state.child_started_at or now
        if feed is None:
            return (now - started) > timedelta(minutes=self.config.stall_minutes)
        if int(feed.get("live_matches") or 0) <= 0:
            return False
        last_live = _parse_ts(feed.get("last_live_event_at"))
        baseline = max(last_live, started) if last_live else started
        return (now - baseline) > timedelta(minutes=self.config.stall_minutes)

    def _write_heartbeat(self, now: datetime, feed: Optional[dict[str, Any]]) -> None:
        payload = {
            "updated_at": now.isoformat(),
            "watchdog_pid": os.getpid(),
            "child_pid": getattr(self.child, "pid", None),
            "child_alive": self.child is not None,
            "child_started_at": self.state.child_started_at.isoformat() if self.state.child_started_at else None,
            "last_tick_at": (feed or {}).get("last_live_event_at"),
            "matches_live": int((feed or {}).get("live_matches") or 0),
            "restarts_today": self.state.restarts_today,
            "consecutive_failures": self.state.consecutive_failures,
            "last_stop_reason": self.state.last_stop_reason,
        }
        try:
            self.heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.heartbeat_path.with_name(self.heartbeat_path.name + ".tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(tmp, self.heartbeat_path)
        except OSError as e:
            self._log("heartbeat_write_error", error=str(e))

    def step(self) -> None:
        """One supervision cycle: start, watch, or restart the child."""
        now = self.clock()
        self._roll_day(now)

        if self.child is None:
            if self.state.next_start_at is None or now >= self.state.next_start_at:
                self._start_child(now)
            self._write_heartbeat(now, self.read_feed())
            return

        returncode = self.child.poll()
        if returncode is not None:
            self._after_failure(now, reason=f"exited:{returncode}")
            self._write_heartbeat(now, self.read_feed())
            return

        feed = self.read_feed()
        if self.is_stalled(now, feed):
            self._log("child_stalled", pid=self.child.pid, stall_minutes=self.config.stall_minutes)
            self._kill_child()
            self._after_failure(now, reason="stalled")
            self._write_heartbeat(now, feed)
            return

        self._write_heartbeat(now, feed)

    def run(self, max_iterations: Optional[int] = None) -> None:
        """Loop ``step`` until stopped, or for ``max_iterations`` cycles (tests)."""
        i = 0
        try:
            while max_iterations is None or i < max_iterations:
                self.step()
                i += 1
                if max_iterations is None or i < max_iterations:
                    self.sleep(self.config.poll_seconds)
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        if self.child is not None:
            self._log("watchdog_stopping", pid=self.child.pid)
            self._kill_child()
            self.child = None


def _spawn_child() -> subprocess.Popen:
    CHILD_LOG.parent.mkdir(parents=True, exist_ok=True)
    out = CHILD_LOG.open("a", encoding="utf-8")
    return subprocess.Popen(
        CHILD_CMD, cwd=str(ROOT), stdout=out, stderr=subprocess.STDOUT, env=os.environ.copy(),
    )


def _config_from_env() -> WatchdogConfig:
    def _float(name: str, default: float) -> float:
        try:
            return float(os.getenv(name, default))
        except ValueError:
            return default

    return WatchdogConfig(stall_minutes=_float("SCRAPER_STALL_MINUTES", 10.0))


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--status", action="store_true", help="print the watchdog heartbeat and exit")
    args = p.parse_args(argv)

    if args.status:
        print(json.dumps(read_feed_state(HEARTBEAT_PATH) or {"status": "no heartbeat"}, indent=2))
        return 0

    now = _utcnow()
    if another_instance_running(HEARTBEAT_PATH, now, os.getpid()):
        logger.info("watchdog_already_running", heartbeat=str(HEARTBEAT_PATH))
        return 0

    dog = Watchdog(
        spawn=_spawn_child,
        read_feed=read_feed_state,
        config=_config_from_env(),
        heartbeat_path=HEARTBEAT_PATH,
        log_path=WATCHDOG_LOG,
    )
    try:
        dog.run()
    except KeyboardInterrupt:
        dog.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
