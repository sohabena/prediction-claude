"""Data access layer for agent/virtual-bet related DB operations."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from shared.db import get_session


class AgentRepository:
    """Repository for agent and virtual bet data access."""

    @staticmethod
    async def get_virtual_bets(
        limit: int,
        match_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch recent virtual bets, optionally filtered by match."""
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
            return [
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
                for row in result.fetchall()
            ]

    @staticmethod
    async def get_performance_summary() -> dict[str, Any]:
        """Fetch aggregated performance stats from virtual_bets."""
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
        return {
            "total_bets": 0, "settled_bets": 0, "pending_bets": 0, "wins": 0,
            "losses": 0, "win_rate": 0.0, "total_pnl": 0.0, "avg_odds": 0.0,
            "avg_stake": 0.0,
        }

    @staticmethod
    async def get_pending_bets_with_results() -> list[Any]:
        """Fetch unsettled virtual bets that have match results."""
        async with get_session() as session:
            result = await session.execute(
                text("""
                    SELECT v.id, v.placed_at, v.match_id, v.action, v.team, v.odds, v.stake,
                           r.winner, r.result_type, r.team_home, r.team_away
                    FROM virtual_bets v
                    INNER JOIN match_results r ON r.match_id = v.match_id
                    WHERE v.settled_at IS NULL
                """)
            )
            return result.fetchall()

    @staticmethod
    async def get_closing_odds_for_bet(
        session: Any, match_id: str,
    ) -> tuple[Any, Any] | None:
        """Get back_home, back_away from the last odds tick for a match."""
        result = await session.execute(
            text("""
                SELECT back_home, back_away FROM odds_ticks
                WHERE match_id = :match_id
                ORDER BY time DESC LIMIT 1
            """),
            {"match_id": match_id},
        )
        return result.fetchone()

    @staticmethod
    async def settle_bet(
        session: Any,
        bet_id: int,
        placed_at: Any,
        outcome: str,
        pnl: float,
        closing_odds: float | None,
        clv: float | None,
    ) -> None:
        """Update a virtual bet with settlement data."""
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
