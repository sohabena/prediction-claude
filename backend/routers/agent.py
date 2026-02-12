"""Agent state and virtual betting endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import text

from shared.constants import KEY_AGENT_STATE, KEY_AGENT_VERSION, KEY_ORCHESTRATOR_STATE
from shared.db import get_session
from shared.logging import setup_logging
from shared.redis_client import get_redis
from shared.schemas import AgentState

router = APIRouter()
logger = setup_logging("router_agent")


@router.get("/state")
async def get_agent_state() -> dict[str, Any]:
    """Get current agent state."""
    try:
        redis = await get_redis()
        # Primary: orchestrator state (always up-to-date)
        orch_data = await redis.get_json(KEY_ORCHESTRATOR_STATE)
        if isinstance(orch_data, dict):
            state_map = {
                "accumulating": AgentState.PAUSED.value,
                "offline_training": AgentState.TRAINING.value,
                "online_training": AgentState.TRAINING.value,
                "virtual_trading": AgentState.EVALUATING.value,
                "graduated": AgentState.LIVE.value,
            }
            return {
                "state": state_map.get(orch_data.get("state", ""), AgentState.PAUSED.value),
                "version": str(orch_data.get("model_version", "none")),
                "orchestrator_state": orch_data.get("state", "unknown"),
            }
        # Fallback: agent:state for backward compat
        state_data = await redis.get_json(KEY_AGENT_STATE)
        version_data = await redis.get_json(KEY_AGENT_VERSION)
        return {
            "state": state_data.get("mode", AgentState.PAUSED.value) if isinstance(state_data, dict) else (state_data or AgentState.PAUSED.value),
            "version": version_data.get("version", "none") if isinstance(version_data, dict) else (version_data or "none"),
        }
    except Exception as e:
        logger.error("agent_state_error", error=str(e))
        return {"state": AgentState.PAUSED.value, "version": "none"}


@router.get("/bets")
async def get_virtual_bets(
    limit: int = Query(50, ge=1, le=500),
    match_id: str | None = None,
) -> dict[str, Any]:
    """Get recent virtual bets."""
    try:
        async with get_session() as session:
            query = """
                SELECT id, placed_at, match_id, action, team, odds, stake,
                       settled_at, outcome, profit_loss, agent_version
                FROM virtual_bets
            """
            params: dict[str, Any] = {"limit": limit}

            if match_id:
                query += " WHERE match_id = :match_id"
                params["match_id"] = match_id

            query += " ORDER BY placed_at DESC LIMIT :limit"

            result = await session.execute(text(query), params)
            rows = result.fetchall()

            return {
                "count": len(rows),
                "bets": [
                    {
                        "id": row[0],
                        "placed_at": row[1].isoformat() if row[1] else None,
                        "match_id": row[2],
                        "action": row[3],
                        "team": row[4],
                        "odds": row[5],
                        "stake": row[6],
                        "settled_at": row[7].isoformat() if row[7] else None,
                        "outcome": row[8],
                        "profit_loss": row[9],
                        "agent_version": row[10],
                    }
                    for row in rows
                ],
            }
    except Exception as e:
        logger.error("bets_query_error", error=str(e))
        return {"count": 0, "bets": [], "error": str(e)}


@router.get("/performance")
async def get_agent_performance() -> dict[str, Any]:
    """Get agent performance summary."""
    try:
        async with get_session() as session:
            result = await session.execute(
                text("""
                    SELECT
                        COUNT(*) FILTER (WHERE outcome IN ('win', 'loss')) as settled_bets,
                        COUNT(*) FILTER (WHERE outcome = 'win') as wins,
                        COUNT(*) FILTER (WHERE outcome = 'loss') as losses,
                        COUNT(*) FILTER (WHERE outcome NOT IN ('win', 'loss') OR outcome IS NULL) as pending_bets,
                        SUM(profit_loss) FILTER (WHERE outcome IN ('win', 'loss')) as total_pnl,
                        AVG(odds) as avg_odds,
                        AVG(stake) as avg_stake,
                        COUNT(*) as total_bets
                    FROM virtual_bets
                """)
            )
            row = result.fetchone()
            if row:
                settled = row[0] or 0
                wins = row[1] or 0
                return {
                    "total_bets": row[7] or 0,
                    "settled_bets": settled,
                    "pending_bets": row[3] or 0,
                    "wins": wins,
                    "losses": row[2] or 0,
                    "win_rate": wins / max(settled, 1) if settled > 0 else 0.0,
                    "total_pnl": float(row[4] or 0),
                    "avg_odds": float(row[5] or 0),
                    "avg_stake": float(row[6] or 0),
                }
    except Exception as e:
        logger.error("performance_error", error=str(e))

    return {
        "total_bets": 0, "settled_bets": 0, "pending_bets": 0, "wins": 0, "losses": 0,
        "win_rate": 0.0, "total_pnl": 0.0, "avg_odds": 0.0, "avg_stake": 0.0,
    }


@router.post("/settle-pending")
async def settle_pending_bets() -> dict[str, Any]:
    """Retroactively settle all pending virtual bets that have match results.

    This is a DB-only operation that doesn't require the live trading loop.
    It joins virtual_bets (outcome='pending') with match_results and computes
    P&L based on BACK/LAY logic and the match winner.

    Needed because the live_trading_loop tracks open bets in memory only —
    if it restarts, all references are lost and bets stay pending forever.
    """
    settled_count = 0
    total_pnl = 0.0
    errors = 0

    try:
        async with get_session() as session:
            # Find pending bets that have match results
            result = await session.execute(
                text("""
                    SELECT v.id, v.placed_at, v.match_id, v.action, v.team, v.odds, v.stake,
                           r.winner, r.result_type, r.team_home, r.team_away
                    FROM virtual_bets v
                    INNER JOIN match_results r ON r.match_id = v.match_id
                    WHERE v.outcome = 'pending'
                """)
            )
            rows = result.fetchall()

            for row in rows:
                bet_id = row[0]
                placed_at = row[1]
                match_id = row[2]
                action = row[3]
                team = row[4]
                odds = float(row[5])
                stake = float(row[6])
                winner = row[7]
                result_type = row[8]

                # Determine outcome
                if result_type in ("tie", "no_result", "draw", "abandoned"):
                    outcome = "void"
                    pnl = 0.0
                else:
                    is_back = "BACK" in action
                    team_won = (team == winner)

                    if is_back:
                        if team_won:
                            outcome = "win"
                            pnl = stake * (odds - 1.0)
                        else:
                            outcome = "loss"
                            pnl = -stake
                    else:  # LAY
                        if team_won:
                            outcome = "loss"
                            pnl = -stake * (odds - 1.0)
                        else:
                            outcome = "win"
                            pnl = stake

                # Fetch closing odds for CLV
                closing_row = await session.execute(
                    text("""
                        SELECT back_home, back_away FROM odds_ticks
                        WHERE match_id = :match_id
                        ORDER BY time DESC LIMIT 1
                    """),
                    {"match_id": match_id},
                )
                closing = closing_row.fetchone()
                closing_odds = None
                clv = None
                if closing:
                    team_home = row[9]
                    bet_on_home = (team == team_home)
                    closing_odds = closing[0] if bet_on_home else closing[1]
                    if closing_odds and closing_odds > 1.0 and odds > 1.0:
                        clv = (1.0 / closing_odds) / (1.0 / odds) - 1.0

                try:
                    await session.execute(
                        text("""
                            UPDATE virtual_bets
                            SET outcome = :outcome,
                                profit_loss = :pnl,
                                settled_at = NOW(),
                                closing_odds = :closing_odds,
                                clv = :clv
                            WHERE id = :bet_id AND placed_at = :placed_at
                        """),
                        {
                            "outcome": outcome,
                            "pnl": round(pnl, 2),
                            "closing_odds": closing_odds,
                            "clv": round(clv, 6) if clv is not None else None,
                            "bet_id": bet_id,
                            "placed_at": placed_at,
                        },
                    )
                    settled_count += 1
                    total_pnl += pnl
                except Exception as e:
                    errors += 1
                    logger.warning("settle_bet_error", bet_id=bet_id, error=str(e))

        logger.info("pending_bets_settled", count=settled_count, pnl=round(total_pnl, 2))
        return {
            "settled": settled_count,
            "total_pnl": round(total_pnl, 2),
            "errors": errors,
        }
    except Exception as e:
        logger.error("settle_pending_error", error=str(e))
        return {"settled": 0, "total_pnl": 0.0, "errors": 1, "error": str(e)}
