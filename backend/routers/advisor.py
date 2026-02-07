"""
Advisor endpoints: bet signals, orchestrator state, shadow trading performance,
drift detection, and manual demotion control.

Post-graduation, the RL agent suggests bets while continuing to shadow-trade
virtually for ongoing performance validation and drift detection.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException

from shared.constants import (
    KEY_ADVISOR_SIGNALS,
    KEY_AGENT_STATE,
    KEY_DRIFT_STATUS,
    KEY_ORCHESTRATOR_STATE,
    KEY_ORCHESTRATOR_STATS,
    KEY_SHADOW_PERFORMANCE,
    ORCHESTRATOR_STATE_VIRTUAL_TRADING,
)
from shared.logging import setup_logging
from shared.redis_client import get_redis

router = APIRouter()
logger = setup_logging("router_advisor")


@router.get("/signals")
async def get_advisor_signals() -> dict[str, Any]:
    """
    Get current bet signals from the graduated RL agent.

    Returns suggested bets with confidence scores and action probability
    distributions so the user can make informed manual decisions.
    """
    try:
        redis = await get_redis()
        data = await redis.get_json(KEY_ADVISOR_SIGNALS)
        if data:
            return data
    except Exception as e:
        logger.error("advisor_signals_error", error=str(e))

    return {
        "signals": [],
        "generated_at": None,
        "model_version": 0,
        "message": "No signals available. Agent may not have graduated yet.",
    }


@router.get("/state")
async def get_orchestrator_state() -> dict[str, Any]:
    """
    Get current orchestrator lifecycle state.

    States: accumulating -> offline_training -> online_training -> virtual_trading -> graduated
    """
    try:
        redis = await get_redis()
        data = await redis.get_json(KEY_ORCHESTRATOR_STATE)
        if data:
            return data
    except Exception as e:
        logger.error("orchestrator_state_error", error=str(e))

    return {
        "state": "accumulating",
        "model_version": 0,
        "curriculum_stage": "PATTERN_RECOGNITION",
        "updated_at": None,
    }


@router.get("/stats")
async def get_orchestrator_stats() -> dict[str, Any]:
    """
    Get orchestrator statistics: data accumulation, training runs, etc.
    """
    try:
        redis = await get_redis()
        data = await redis.get_json(KEY_ORCHESTRATOR_STATS)
        if data:
            return data
    except Exception as e:
        logger.error("orchestrator_stats_error", error=str(e))

    return {
        "started_at": None,
        "state_transitions": [],
        "training_runs": 0,
        "eval_runs": 0,
        "matches_trained_on": 0,
    }


@router.get("/shadow/performance")
async def get_shadow_performance() -> dict[str, Any]:
    """
    Get shadow trading performance metrics.

    After graduation, the agent continues placing virtual bets to validate
    its real-world performance. This endpoint returns:
    - Balance and P&L
    - Win rate and ROI (overall and recent window)
    - Sharpe ratio and drawdown
    - Confidence calibration data
    - Daily P&L history
    """
    try:
        redis = await get_redis()
        data = await redis.get_json(KEY_SHADOW_PERFORMANCE)
        if data:
            return data
    except Exception as e:
        logger.error("shadow_performance_error", error=str(e))

    return {
        "balance": 0,
        "total_bets": 0,
        "win_rate": 0,
        "total_pnl": 0,
        "roi": 0,
        "message": "Shadow trading not active. Agent may not have graduated yet.",
    }


@router.get("/shadow/drift")
async def get_drift_status() -> dict[str, Any]:
    """
    Get performance drift detection status.

    Drift is detected when the agent's rolling performance falls below
    acceptable thresholds. If drift persists for multiple consecutive days,
    the agent is automatically demoted back to virtual trading.
    """
    try:
        redis = await get_redis()
        data = await redis.get_json(KEY_DRIFT_STATUS)
        if data:
            return data
    except Exception as e:
        logger.error("drift_status_error", error=str(e))

    return {
        "is_drifting": False,
        "consecutive_drift_days": 0,
        "should_demote": False,
        "violations": [],
        "metrics": {},
    }


@router.post("/demote")
async def manual_demote() -> dict[str, Any]:
    """
    Manually demote the agent from graduated back to virtual trading.

    Use this if you've lost confidence in the agent's recommendations
    and want it to re-prove itself before generating more signals.
    This requires the agent to meet all graduation criteria again for
    14 consecutive days before it can return to advisor mode.
    """
    try:
        redis = await get_redis()
        state_data = await redis.get_json(KEY_ORCHESTRATOR_STATE)

        if not state_data or state_data.get("state") != "graduated":
            raise HTTPException(
                status_code=400,
                detail="Agent is not graduated. Cannot demote.",
            )

        # Update orchestrator state to virtual_trading
        state_data["state"] = ORCHESTRATOR_STATE_VIRTUAL_TRADING
        state_data["updated_at"] = datetime.now(timezone.utc).isoformat()
        state_data["demoted_manually"] = True
        await redis.set_json(KEY_ORCHESTRATOR_STATE, state_data)

        # Update agent state
        await redis.set_json(KEY_AGENT_STATE, {
            "mode": "virtual_trading",
            "demoted_at": datetime.now(timezone.utc).isoformat(),
            "reason": "manual_demotion",
            "model_version": state_data.get("model_version", 0),
        })

        # Clear advisor signals
        await redis.set_json(KEY_ADVISOR_SIGNALS, {
            "signals": [],
            "generated_at": None,
            "model_version": state_data.get("model_version", 0),
            "message": "Agent was manually demoted. Signals cleared.",
        })

        logger.info("manual_demotion_triggered")

        return {
            "success": True,
            "message": "Agent demoted to virtual trading. It must re-graduate before generating new signals.",
            "demoted_at": datetime.now(timezone.utc).isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("manual_demote_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))
