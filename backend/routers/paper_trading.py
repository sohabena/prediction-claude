"""
Paper Trading API Endpoints
Manages paper trading simulation for testing strategies without real money
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List, Dict
from datetime import datetime
from loguru import logger
import json

# Import the paper trading simulator
from paper_trading.simulator import PaperTradingSimulator

router = APIRouter(prefix="/api/paper-trading", tags=["paper-trading"])

# Global paper trading simulator instance
_simulator: Optional[PaperTradingSimulator] = None


def get_simulator() -> PaperTradingSimulator:
    """Get or create the paper trading simulator"""
    global _simulator
    if _simulator is None:
        _simulator = PaperTradingSimulator(
            initial_balance=50000.0,
            stake_per_trade=500.0
        )
    return _simulator


class InitializeRequest(BaseModel):
    initial_balance: float = 50000.0
    stake_per_trade: float = 500.0


class OpenPositionRequest(BaseModel):
    signal_id: str
    match_id: str
    match_name: Optional[str] = None
    strategy: str
    side: str  # BACK or LAY
    odds: float
    confidence: float
    market: Optional[str] = "match_odds"


class ClosePositionRequest(BaseModel):
    outcome: str  # win, loss, void
    exit_odds: float


@router.post("/initialize")
async def initialize_paper_trading(request: InitializeRequest):
    """
    Initialize or reset the paper trading simulator
    
    This will reset all positions and start fresh with the specified balance.
    """
    global _simulator
    
    _simulator = PaperTradingSimulator(
        initial_balance=request.initial_balance,
        stake_per_trade=request.stake_per_trade
    )
    
    logger.info(f"Paper trading initialized: Balance={request.initial_balance}, Stake={request.stake_per_trade}")
    
    return {
        "status": "success",
        "message": "Paper trading simulator initialized",
        "config": {
            "initial_balance": request.initial_balance,
            "stake_per_trade": request.stake_per_trade,
            "current_balance": _simulator.balance
        }
    }


@router.get("/status")
async def get_paper_trading_status():
    """Get current paper trading status and statistics"""
    simulator = get_simulator()
    stats = simulator.get_stats()
    
    return {
        "status": "success",
        "stats": stats,
        "open_positions": [
            {
                "signal_id": pos.signal_id,
                "match_id": pos.match_id,
                "strategy": pos.strategy,
                "side": pos.side,
                "entry_odds": pos.entry_odds,
                "stake": pos.stake,
                "opened_at": pos.opened_at.isoformat()
            }
            for pos in simulator.positions.values()
        ]
    }


@router.post("/positions/open")
async def open_paper_position(request: OpenPositionRequest):
    """
    Open a new paper trading position
    
    Typically called when a signal is generated and user wants to track it.
    """
    simulator = get_simulator()
    
    signal_dict = {
        "id": request.signal_id,
        "signal_id": request.signal_id,
        "match_id": request.match_id,
        "match_name": request.match_name or request.match_id,
        "strategy": request.strategy,
        "side": request.side,
        "odds": request.odds,
        "confidence": request.confidence,
        "market": request.market
    }
    
    success = simulator.open_position(signal_dict)
    
    if not success:
        raise HTTPException(
            status_code=400,
            detail="Failed to open position. Check balance or if position already exists."
        )
    
    return {
        "status": "success",
        "message": "Paper position opened",
        "position": {
            "signal_id": request.signal_id,
            "match_id": request.match_id,
            "strategy": request.strategy,
            "side": request.side,
            "odds": request.odds,
            "stake": simulator.stake_per_trade
        },
        "balance": simulator.balance
    }


@router.post("/positions/{signal_id}/close")
async def close_paper_position(signal_id: str, request: ClosePositionRequest):
    """
    Close a paper trading position with outcome
    
    Call this when match ends or user wants to manually close a position.
    """
    simulator = get_simulator()
    
    success = simulator.close_position(
        signal_id=signal_id,
        outcome=request.outcome,
        exit_odds=request.exit_odds
    )
    
    if not success:
        raise HTTPException(
            status_code=404,
            detail=f"Position not found for signal {signal_id}"
        )
    
    # Find the closed position
    closed_position = None
    for pos in simulator.closed_positions:
        if pos.signal_id == signal_id:
            closed_position = pos
            break
    
    return {
        "status": "success",
        "message": f"Position closed with {request.outcome}",
        "result": {
            "signal_id": signal_id,
            "outcome": request.outcome,
            "profit_loss": closed_position.profit if closed_position else 0,
            "entry_odds": closed_position.entry_odds if closed_position else 0,
            "exit_odds": request.exit_odds
        },
        "balance": simulator.balance,
        "stats": simulator.get_stats()
    }


@router.get("/positions/open")
async def list_open_positions():
    """List all currently open paper trading positions"""
    simulator = get_simulator()
    
    positions = [
        {
            "signal_id": pos.signal_id,
            "match_id": pos.match_id,
            "match_name": pos.match_name,
            "strategy": pos.strategy,
            "side": pos.side,
            "entry_odds": pos.entry_odds,
            "stake": pos.stake,
            "confidence": pos.confidence,
            "opened_at": pos.opened_at.isoformat()
        }
        for pos in simulator.positions.values()
    ]
    
    return {
        "status": "success",
        "count": len(positions),
        "positions": positions,
        "total_at_risk": sum(pos.stake for pos in simulator.positions.values())
    }


@router.get("/positions/history")
async def get_position_history(
    limit: int = Query(50, ge=1, le=500),
    strategy: Optional[str] = None
):
    """Get closed position history"""
    simulator = get_simulator()
    
    positions = simulator.closed_positions
    
    # Filter by strategy if specified
    if strategy:
        positions = [p for p in positions if p.strategy == strategy]
    
    # Limit results
    positions = positions[-limit:]
    
    return {
        "status": "success",
        "count": len(positions),
        "positions": [p.to_dict() for p in positions]
    }


@router.get("/stats")
async def get_paper_trading_stats():
    """Get comprehensive paper trading statistics"""
    simulator = get_simulator()
    stats = simulator.get_stats()
    
    return {
        "status": "success",
        "stats": stats
    }


@router.post("/reset")
async def reset_paper_trading():
    """Reset paper trading to initial state"""
    global _simulator
    
    old_stats = None
    if _simulator:
        old_stats = _simulator.get_stats()
    
    _simulator = PaperTradingSimulator(
        initial_balance=50000.0,
        stake_per_trade=500.0
    )
    
    return {
        "status": "success",
        "message": "Paper trading reset to initial state",
        "previous_stats": old_stats,
        "new_balance": _simulator.balance
    }


@router.post("/auto-open-from-signal")
async def auto_open_from_signal(signal: Dict):
    """
    Automatically open a paper position from a signal
    
    This endpoint is called internally when signals are generated
    and auto-paper-trading is enabled.
    """
    simulator = get_simulator()
    
    # Map signal format to position format
    signal_dict = {
        "id": signal.get("signal_id") or signal.get("id"),
        "signal_id": signal.get("signal_id") or signal.get("id"),
        "match_id": signal.get("match_id", "unknown"),
        "match_name": signal.get("match_context", {}).get("match_id", "Unknown Match"),
        "strategy": signal.get("strategy"),
        "side": signal.get("action", "BACK"),
        "odds": signal.get("odds"),
        "confidence": signal.get("confidence"),
        "market": "match_odds"
    }
    
    success = simulator.open_position(signal_dict)
    
    return {
        "auto_opened": success,
        "signal_id": signal_dict["id"],
        "balance": simulator.balance
    }
