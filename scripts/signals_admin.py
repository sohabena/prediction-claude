"""S44 operator CLI for signal trust and kill state. Read-only except ``reset``.

    python -m scripts.signals_admin status
    python -m scripts.signals_admin show --last 20
    python -m scripts.signals_admin reset --scope <strategy:version|all> --operator <name>

``status`` prints the trust label and reason per signal version, plus every active kill. ``show`` prints the most
recent signals from the signal log with their trust labels. ``reset`` clears one kill through ``KillSwitch.reset``
and records the operator's name; ``--operator`` is required. Nothing here places a bet.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Optional, Sequence

from shared.constants import KILL_ALL_SCOPE, SIGNAL_LOG_PATH
from shared.logging import setup_logging
from shared.redis_client import get_redis
from strategy.runner import OUTCOME_LOG_PATH, outcome_versions
from strategy.trust import TrustRegistry
from virtual_trading.kill_switch import KillSwitch

logger = setup_logging("signals_admin")


def _read_jsonl(path: Path | str) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            rows.append(rec)
    return rows


def known_versions(signals_path: Path | str = SIGNAL_LOG_PATH, outcomes_path: Path | str = OUTCOME_LOG_PATH) -> list[str]:
    """Signal versions seen in the signal log or the outcome log, sorted."""
    versions = set(outcome_versions(None, outcomes_path))
    versions |= {str(r["strategy_version"]) for r in _read_jsonl(signals_path) if r.get("strategy_version")}
    return sorted(versions)


async def status(redis: Any, versions: Sequence[str]) -> dict[str, Any]:
    """Trust label, reason and kill state per version. Reads only; the kill switch is loaded, not changed."""
    kill = KillSwitch(redis)
    await kill.load(versions)
    registry = TrustRegistry(redis=redis, kill_switch=kill)
    await registry.load(versions)
    trust = [
        {"version": v, "label": registry.trust_label(v), "reason": registry.trust_reason(v)}
        for v in versions
    ]
    kills: list[dict[str, str]] = []
    for scope in [KILL_ALL_SCOPE, *versions]:
        data = await redis.get_json(KillSwitch.key_for(scope))
        if data and data.get("active"):
            kills.append({"scope": scope, "reason": str(data.get("reason", ""))})
    return {"trust": trust, "kills": kills, "global_in_memory": kill.blocked()}


async def reset(redis: Any, scope: str, operator: str) -> str:
    """Clear one kill. ``scope`` is ``strategy:version`` or ``all`` (the global scope). Returns the cleared key."""
    if not scope.strip():
        raise ValueError("reset requires --scope")
    target = KILL_ALL_SCOPE if scope == "all" else scope
    await KillSwitch(redis).reset(target, operator=operator)
    return target


def recent_signals(last: int, path: Path | str = SIGNAL_LOG_PATH) -> list[dict[str, Any]]:
    """The last ``last`` signals in the signal log. Lines written before S44 have no trust label."""
    if last < 1:
        return []
    return _read_jsonl(path)[-last:]


def format_status(report: dict[str, Any]) -> str:
    lines = [f"Signal trust ({len(report['trust'])} versions)"]
    if not report["trust"]:
        lines.append("  (no signal versions recorded yet)")
    for row in report["trust"]:
        reason = f" ({row['reason']})" if row["reason"] else ""
        lines.append(f"  {row['version']:<28} {row['label']}{reason}")
    lines.append("Active kills")
    if not report["kills"]:
        lines.append("  (none)")
    for kill in report["kills"]:
        lines.append(f"  {kill['scope']:<28} {kill['reason']}")
    if report.get("global_in_memory"):
        lines.append(f"Global suspension in memory: {report['global_in_memory']}")
    return "\n".join(lines)


def format_signals(rows: Sequence[dict[str, Any]]) -> str:
    if not rows:
        return "(no signals logged)"
    out = []
    for r in rows:
        limit = "any" if r.get("price_limit") is None else f"{float(r['price_limit']):.2f}"
        trust = r.get("trust", "-")
        reason = f" ({r['trust_reason']})" if r.get("trust_reason") else ""
        out.append(
            f"{r.get('created_at', '?')}  {r.get('match', '?')}  {r.get('side', '?')} {r.get('selection', '?')}  "
            f"limit {limit}  stake {r.get('suggested_stake', '?')}  edge {r.get('edge', '?')}  "
            f"trust {trust}{reason}  {r.get('strategy_version', '?')}"
        )
    return "\n".join(out)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="signals_admin", description="Signal trust and kill-switch admin (S44).")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="trust label and reason per version, plus active kills")
    p_show = sub.add_parser("show", help="recent signals with their trust labels")
    p_show.add_argument("--last", type=int, default=20, help="how many recent signals (default 20)")
    p_reset = sub.add_parser("reset", help="clear one kill (writes the operator's name)")
    p_reset.add_argument("--scope", required=True, help="strategy:version, or 'all' for the global scope")
    p_reset.add_argument("--operator", required=True, help="your name; recorded with the reset")
    return parser


async def _run_redis(args: argparse.Namespace) -> int:
    redis = await get_redis()
    if args.command == "reset":
        target = await reset(redis, args.scope, args.operator)
        print(f"kill cleared: {target} (reset by {args.operator})")
        return 0
    report = await status(redis, known_versions())
    print(format_status(report))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "show":
        if args.last < 1:
            parser.error("--last must be >= 1")
        print(format_signals(recent_signals(args.last)))
        return 0
    try:
        return asyncio.run(_run_redis(args))
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # Redis unreachable or unreadable: report, never guess a state
        print(f"error: kill/trust state unavailable ({type(e).__name__})", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
