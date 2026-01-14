"""
Signal Outcome Tracking API Endpoints
Records and queries signal outcomes for performance analysis
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, Dict
from loguru import logger

from backend.services.outcome_tracker import outcome_tracker

router = APIRouter(prefix="/api/outcomes", tags=["outcomes"])


class RecordOutcomeRequest(BaseModel):
    signal_id: str
    strategy: str
    action: str  # BACK or LAY
    odds: float
    stake: float
    won: bool
    match_id: Optional[str] = None
    match_context: Optional[Dict] = None


@router.post("/record")
async def record_outcome(request: RecordOutcomeRequest):
    """
    Record the outcome of a signal
    
    This should be called when:
    1. A bet is manually marked as won/lost
    2. Match result is determined
    3. Paper trading position is closed
    """
    try:
        result = outcome_tracker.record_outcome(
            signal_id=request.signal_id,
            strategy=request.strategy,
            action=request.action,
            odds=request.odds,
            stake=request.stake,
            won=request.won,
            match_id=request.match_id,
            match_context=request.match_context
        )
        
        return {
            "status": "success",
            "message": f"Outcome recorded: {'WIN' if request.won else 'LOSS'}",
            "result": result
        }
        
    except Exception as e:
        logger.error(f"Error recording outcome: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats")
async def get_running_stats():
    """Get current running statistics for the session"""
    try:
        stats = outcome_tracker.get_running_stats()
        
        return {
            "status": "success",
            "stats": stats
        }
        
    except Exception as e:
        logger.error(f"Error fetching stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/strategy/{strategy}")
async def get_strategy_performance(strategy: str):
    """Get detailed performance for a specific strategy"""
    try:
        performance = outcome_tracker.get_strategy_performance(strategy)
        
        return {
            "status": "success",
            "performance": performance
        }
        
    except Exception as e:
        logger.error(f"Error fetching strategy performance: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/recent")
async def get_recent_outcomes(limit: int = Query(20, ge=1, le=100)):
    """Get recent signal outcomes"""
    try:
        outcomes = outcome_tracker.get_recent_outcomes(limit)
        
        return {
            "status": "success",
            "count": len(outcomes),
            "outcomes": outcomes
        }
        
    except Exception as e:
        logger.error(f"Error fetching recent outcomes: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/reset")
async def reset_session():
    """Reset session statistics"""
    try:
        result = outcome_tracker.reset_session()
        
        return {
            "status": "success",
            "message": "Session reset successfully",
            "result": result
        }
        
    except Exception as e:
        logger.error(f"Error resetting session: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/circuit-breaker")
async def get_circuit_breaker_status():
    """Get current circuit breaker status"""
    try:
        stats = outcome_tracker.get_running_stats()
        strategy_breakdown = stats.get('strategy_breakdown', {})
        
        # Check each strategy for circuit breaker conditions
        warnings = []
        for strategy, s_stats in strategy_breakdown.items():
            if s_stats['total'] >= 20:
                win_rate = s_stats['wins'] / s_stats['total']
                if win_rate < 0.45:
                    warnings.append({
                        'strategy': strategy,
                        'win_rate': win_rate,
                        'message': f"Win rate below 45%"
                    })
            
            # Check for losing streak
            if len(s_stats.get('last_10', [])) >= 5:
                recent = s_stats['last_10'][-5:]
                if all(not won for won in recent):
                    warnings.append({
                        'strategy': strategy,
                        'message': "5 consecutive losses"
                    })
        
        # Check session drawdown
        drawdown = (stats['starting_balance'] - stats['current_balance']) / stats['starting_balance']
        
        return {
            "status": "success",
            "circuit_breaker": {
                "triggered": drawdown > 0.05 or len(warnings) > 0,
                "session_drawdown": drawdown,
                "session_drawdown_pct": f"{drawdown * 100:.1f}%",
                "max_allowed_drawdown": "5%",
                "warnings": warnings
            }
        }
        
    except Exception as e:
        logger.error(f"Error fetching circuit breaker status: {e}")
        raise HTTPException(status_code=500, detail=str(e))
