"""Advisor mode handler — generates bet signals from graduated agent.

Extracted from orchestrator._handle_advisor_mode (RGV #2).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from shared.schemas import MatchContext, OddsEvent, PortfolioState

from shared.constants import (
    CHANNEL_ADVISOR_SIGNALS, KEY_ACTIVE_MATCHES,
    KEY_ADVISOR_SIGNALS, OBSERVATION_SIZE,
)
from shared.db import get_session
from shared.logging import setup_logging
from shared.redis_client import get_redis
from virtual_trading.shadow_trader import ShadowTrader

logger = setup_logging("lifecycle_advisor")


class AdvisorHandler:
    """Generates bet signals and shadow-trades them post-graduation."""

    def __init__(
        self, shadow_trader: ShadowTrader,
    ) -> None:
        self._shadow_trader = shadow_trader

    async def run(
        self, trainer: Any, model_version: int,
    ) -> None:
        """Generate signals for all active matches and shadow-trade."""
        if trainer is None or trainer._agent is None:
            return

        try:
            redis = await get_redis()
            active_matches_raw = await redis.get_json(KEY_ACTIVE_MATCHES)
            if not active_matches_raw:
                return

            matches = active_matches_raw.get("matches", [])
            signals: list[dict[str, Any]] = []

            for match in matches:
                signal = await self._generate_signal(match, trainer)
                if signal:
                    signals.append(signal)
                    self._shadow_trader.place_shadow_bet(signal)

            if signals:
                await redis.set_json(KEY_ADVISOR_SIGNALS, {
                    "signals": signals,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "model_version": model_version,
                })
                await redis.publish_event(CHANNEL_ADVISOR_SIGNALS, {
                    "signals": signals,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                })

            # Settle shadow bets for completed matches
            await self._settle_shadow_bets()
            await self._shadow_trader.publish_performance()

        except Exception as e:
            logger.error("advisor_mode_error", error=str(e))

    async def _generate_signal(
        self, match: dict[str, Any], trainer: Any,
    ) -> Optional[dict[str, Any]]:
        """Generate a bet signal for a single match."""
        if trainer is None or trainer._agent is None:
            return None

        try:
            from features.pipeline import FeaturePipeline
            from features.store import FeatureStore

            match_id = match.get("match_id", "")
            if not match_id:
                return None

            store = FeatureStore()
            obs = await store.get(match_id)
            event = None

            if obs is None:
                from features.context_resolver import get_match_context
                event = await self._fetch_latest_odds(match_id, match)
                if event is None:
                    return None
                context = await get_match_context(match_id) or await self._fetch_latest_context(match_id)
                pipeline = FeaturePipeline()
                portfolio = self._build_portfolio_state()
                obs = pipeline.compute(
                    match_id=match_id, event=event,
                    context=context, portfolio=portfolio,
                )

            if obs.shape[0] != OBSERVATION_SIZE:
                logger.warning("observation_shape_mismatch", match_id=match_id, got=obs.shape[0])
                return None

            action, _ = trainer._agent.predict(obs, deterministic=True)
            probs = trainer._agent.get_action_distribution(obs)

            action_names = [
                "HOLD", "BACK_HOME_SM", "BACK_HOME_LG",
                "BACK_AWAY_SM", "BACK_AWAY_LG",
                "LAY_HOME_SM", "LAY_AWAY_SM",
                "LAY_HOME_LG", "LAY_AWAY_LG",
            ]

            confidence = float(probs[action]) if action < len(probs) else 0.0
            if action == 0 or confidence < 0.3:
                return None

            odds = 2.0
            if event:
                action_name = action_names[action] if action < len(action_names) else ""
                if "HOME" in action_name:
                    odds = event.back_home or event.lay_home or 2.0
                else:
                    odds = event.back_away or event.lay_away or 2.0

            return {
                "match_id": match_id,
                "team_home": match.get("team_home", ""),
                "team_away": match.get("team_away", ""),
                "competition": match.get("competition", ""),
                "recommended_action": action_names[action] if action < len(action_names) else "UNKNOWN",
                "confidence": round(confidence, 4),
                "odds": float(odds),
                "action_probabilities": {
                    name: round(float(p), 4) for name, p in zip(action_names, probs)
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            logger.error("signal_generation_error", match_id=match.get("match_id"), error=str(e))
            return None

    async def _fetch_latest_odds(
        self, match_id: str, match: dict[str, Any],
    ) -> Optional["OddsEvent"]:
        from shared.schemas import OddsEvent
        try:
            async with get_session() as session:
                from sqlalchemy import text
                result = await session.execute(
                    text("""
                        SELECT time, match_id, team_home, team_away, competition,
                               back_home, lay_home, back_draw, lay_draw, back_away, lay_away, is_live
                        FROM odds_ticks WHERE match_id = :match_id
                        ORDER BY time DESC LIMIT 1
                    """),
                    {"match_id": match_id},
                )
                row = result.fetchone()
            if not row:
                return None
            return OddsEvent(
                match_id=row[1], timestamp=row[0],
                team_home=row[2] or match.get("team_home", ""),
                team_away=row[3] or match.get("team_away", ""),
                competition=row[4] or match.get("competition", ""),
                back_home=float(row[5]) if row[5] is not None else None,
                lay_home=float(row[6]) if row[6] is not None else None,
                back_draw=float(row[7]) if row[7] is not None else None,
                lay_draw=float(row[8]) if row[8] is not None else None,
                back_away=float(row[9]) if row[9] is not None else None,
                lay_away=float(row[10]) if row[10] is not None else None,
                is_live=bool(row[11]) if row[11] is not None else False,
            )
        except Exception as e:
            logger.warning("fetch_odds_failed", match_id=match_id, error=str(e))
            return None

    async def _fetch_latest_context(self, match_id: str) -> Optional["MatchContext"]:
        try:
            from shared.schemas import MatchContext
            async with get_session() as session:
                from sqlalchemy import text
                result = await session.execute(
                    text("""
                        SELECT time, match_id, is_live, score, wickets, overs,
                               run_rate, req_run_rate, innings, balls_remaining,
                               COALESCE(batting_team, ''), COALESCE(bowling_team, ''),
                               COALESCE(status, 'scheduled'), COALESCE(match_format, 'T20')
                        FROM match_context WHERE match_id = :match_id
                        ORDER BY time DESC LIMIT 1
                    """),
                    {"match_id": match_id},
                )
                row = result.fetchone()
            if not row:
                return None
            fmt = (row[13] or "T20").upper()
            if fmt == "ODI":
                max_overs, max_balls = 50.0, 300
            elif fmt == "TEST":
                max_overs, max_balls = 90.0, 540
            else:
                max_overs, max_balls = 20.0, 120
            return MatchContext(
                match_id=row[1], timestamp=row[0], is_live=bool(row[2]),
                score=int(row[3] or 0), wickets=int(row[4] or 0),
                overs=float(row[5] or 0), run_rate=float(row[6] or 0),
                required_run_rate=float(row[7] or 0), innings=int(row[8] or 1),
                balls_remaining=int(row[9] or 0), batting_team=row[10] or "",
                bowling_team=row[11] or "", status=row[12] or "scheduled",
                match_format=fmt, max_overs=max_overs, max_balls=max_balls,
            )
        except Exception as e:
            logger.debug("fetch_context_failed", match_id=match_id, error=str(e))
            return None

    async def _settle_shadow_bets(self) -> None:
        try:
            open_match_ids = list(self._shadow_trader._open_bets.keys())
            if not open_match_ids:
                return
            async with get_session() as session:
                from sqlalchemy import text
                for match_id in open_match_ids:
                    result = await session.execute(
                        text("""
                            SELECT winner, loser, result_type, team_home, team_away
                            FROM match_results WHERE match_id = :match_id
                        """),
                        {"match_id": match_id},
                    )
                    row = result.fetchone()
                    if not row:
                        continue
                    winner, result_type, team_home = row[0], row[2], row[3]
                    bet = self._shadow_trader._open_bets.get(match_id)
                    if not bet:
                        continue
                    if result_type in ("tie", "no_result", "draw", "abandoned"):
                        self._shadow_trader.void_shadow_bet(match_id)
                        continue
                    action = bet.action
                    home_won = (winner == team_home)
                    if "HOME" in action:
                        is_back = "BACK" in action
                        won = (is_back and home_won) or (not is_back and not home_won)
                    else:
                        is_back = "BACK" in action
                        won = (is_back and not home_won) or (not is_back and home_won)
                    self._shadow_trader.settle_shadow_bet(match_id, won)
        except Exception as e:
            logger.error("settle_shadow_bets_error", error=str(e))

    def _build_portfolio_state(self) -> "PortfolioState":
        from shared.schemas import PortfolioState
        st = self._shadow_trader
        return PortfolioState(
            initial_balance=st._initial_balance,
            current_balance=st._balance,
            open_positions=len(st._open_bets),
            total_exposure=sum(b.stake for b in st._open_bets.values()),
            session_pnl=st._total_pnl, daily_pnl=0.0,
            total_bets=st._total_bets, total_wins=st._total_wins,
            consecutive_streak=0, time_since_last_bet=0.0,
        )
