"""
Bet Placement API Endpoints
Handles virtual bet creation and management
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from backend.db.connection import get_db
from backend.models.betting import BettingBudget, VirtualBet, BetStatus
from backend.utils.kelly_criterion import calculate_kelly_stake, validate_bet_sizing
from backend.services.pnl_calculator import calculate_bet_outcome, calculate_potential_profit, validate_sufficient_balance
from pydantic import BaseModel
from typing import Optional
from loguru import logger
from datetime import datetime
import json

router = APIRouter(prefix="/api/bets", tags=["bets"])


class PlaceBetRequest(BaseModel):
    signal_id: str
    match_id: str
    match_info: dict  # {team1, team2, format}
    strategy: str  # panic_rebound, mean_reversion, whale_shadow
    market: str  # match_odds, over_under, innings_runs
    action: str  # BACK or LAY
    team: str
    odds: float
    confidence: float  # 0.0 to 1.0
    stake: Optional[float] = None  # If None, use Kelly Criterion
    reasoning: Optional[str] = ""
    user_id: str = "default_user"


class CloseBetRequest(BaseModel):
    won: bool


@router.post("/place")
async def place_bet(request: PlaceBetRequest, db: Session = Depends(get_db)):
    """
    Place a manual virtual bet
    
    If stake is not provided, Kelly Criterion will calculate optimal amount
    """
    try:
        # Get budget
        budget = db.query(BettingBudget).filter_by(user_id=request.user_id).first()
        
        if not budget:
            raise HTTPException(
                status_code=404,
                detail="Budget not found. Please initialize budget first."
            )
        
        # Calculate stake if not provided (using Kelly Criterion)
        if request.stake is None or request.stake <= 0:
            kelly_result = calculate_kelly_stake(
                confidence=request.confidence,
                odds=request.odds,
                bankroll=budget.current_balance
            )
            
            stake = kelly_result['recommended_stake']
            logger.info(f"Kelly Criterion calculated stake: ₹{stake}")
        else:
            stake = request.stake
        
        # Validate bet sizing
        is_valid, message = validate_bet_sizing(stake, budget.current_balance)
        if not is_valid:
            raise HTTPException(status_code=400, detail=message)
        
        # Validate sufficient balance
        is_sufficient, balance_message = validate_sufficient_balance(budget, stake)
        if not is_sufficient:
            raise HTTPException(status_code=400, detail=balance_message)
        
        # Calculate potential profit
        potential_profit = calculate_potential_profit(stake, request.odds)
        
        # Create bet
        new_bet = VirtualBet(
            budget_id=budget.id,
            signal_id=request.signal_id,
            match_id=request.match_id,
            match_info=json.dumps(request.match_info),
            strategy=request.strategy,
            market=request.market,
            action=request.action,
            team=request.team,
            stake=stake,
            odds=request.odds,
            confidence=request.confidence,
            potential_profit=potential_profit,
            status=BetStatus.PENDING,
            placed_at=datetime.utcnow(),
            reasoning=request.reasoning,
            auto_placed=False
        )
        
        db.add(new_bet)
        
        # Update budget stats
        budget.total_bets_placed += 1
        budget.updated_at = datetime.utcnow()
        
        db.commit()
        db.refresh(new_bet)
        db.refresh(budget)
        
        logger.info(f"Bet placed: ID={new_bet.id}, Stake=₹{stake}, Odds={request.odds}, "
                   f"Confidence={request.confidence*100}%")
        
        return {
            "status": "success",
            "message": "Bet placed successfully",
            "bet": {
                "id": new_bet.id,
                "signal_id": new_bet.signal_id,
                "match_id": new_bet.match_id,
                "strategy": new_bet.strategy,
                "market": new_bet.market,
                "action": new_bet.action,
                "team": new_bet.team,
                "stake": new_bet.stake,
                "odds": new_bet.odds,
                "confidence": new_bet.confidence,
                "potential_profit": new_bet.potential_profit,
                "placed_at": new_bet.placed_at.isoformat(),
                "status": new_bet.status.value
            },
            "budget": {
                "current_balance": budget.current_balance,
                "total_bets_placed": budget.total_bets_placed
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error placing bet: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/active")
async def get_active_bets(user_id: str = "default_user", db: Session = Depends(get_db)):
    """
    Get all open/pending bets
    """
    try:
        budget = db.query(BettingBudget).filter_by(user_id=user_id).first()
        
        if not budget:
            raise HTTPException(status_code=404, detail="Budget not found")
        
        active_bets = db.query(VirtualBet).filter(
            VirtualBet.budget_id == budget.id,
            VirtualBet.status == BetStatus.PENDING
        ).order_by(VirtualBet.placed_at.desc()).all()
        
        bets_data = [
            {
                "id": bet.id,
                "signal_id": bet.signal_id,
                "match_id": bet.match_id,
                "match_info": json.loads(bet.match_info) if bet.match_info else {},
                "strategy": bet.strategy,
                "market": bet.market,
                "action": bet.action,
                "team": bet.team,
                "stake": bet.stake,
                "odds": bet.odds,
                "confidence": bet.confidence,
                "potential_profit": bet.potential_profit,
                "placed_at": bet.placed_at.isoformat(),
                "reasoning": bet.reasoning
            }
            for bet in active_bets
        ]
        
        total_staked = sum(bet.stake for bet in active_bets)
        total_potential = sum(bet.potential_profit for bet in active_bets)
        
        return {
            "status": "success",
            "active_bets": bets_data,
            "summary": {
                "total_bets": len(active_bets),
                "total_staked": round(total_staked, 2),
                "total_potential_profit": round(total_potential, 2)
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching active bets: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history")
async def get_bet_history(
    user_id: str = "default_user",
    limit: int = Query(50, ge=1, le=500),
    status: Optional[str] = None,
    strategy: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    Get bet history with optional filters
    """
    try:
        budget = db.query(BettingBudget).filter_by(user_id=user_id).first()
        
        if not budget:
            raise HTTPException(status_code=404, detail="Budget not found")
        
        query = db.query(VirtualBet).filter(VirtualBet.budget_id == budget.id)
        
        # Apply filters
        if status:
            try:
                status_enum = BetStatus[status.upper()]
                query = query.filter(VirtualBet.status == status_enum)
            except KeyError:
                raise HTTPException(status_code=400, detail=f"Invalid status: {status}")
        
        if strategy:
            query = query.filter(VirtualBet.strategy == strategy)
        
        bets = query.order_by(VirtualBet.placed_at.desc()).limit(limit).all()
        
        bets_data = [
            {
                "id": bet.id,
                "signal_id": bet.signal_id,
                "match_id": bet.match_id,
                "match_info": json.loads(bet.match_info) if bet.match_info else {},
                "strategy": bet.strategy,
                "market": bet.market,
                "action": bet.action,
                "team": bet.team,
                "stake": bet.stake,
                "odds": bet.odds,
                "confidence": bet.confidence,
                "status": bet.status.value,
                "profit_loss": bet.profit_loss,
                "placed_at": bet.placed_at.isoformat(),
                "closed_at": bet.closed_at.isoformat() if bet.closed_at else None
            }
            for bet in bets
        ]
        
        return {
            "status": "success",
            "bets": bets_data,
            "total_records": len(bets_data),
            "filters_applied": {
                "status": status,
                "strategy": strategy,
                "limit": limit
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching bet history: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/{bet_id}/close")
async def close_bet(bet_id: int, request: CloseBetRequest, db: Session = Depends(get_db)):
    """
    Manually close a bet with outcome (won/lost)
    """
    try:
        bet = db.query(VirtualBet).filter_by(id=bet_id).first()
        
        if not bet:
            raise HTTPException(status_code=404, detail="Bet not found")
        
        if bet.status != BetStatus.PENDING:
            raise HTTPException(
                status_code=400,
                detail=f"Bet already closed with status: {bet.status.value}"
            )
        
        # Calculate outcome and update budget
        result = calculate_bet_outcome(bet, request.won, db)
        
        return {
            "status": "success",
            "message": f"Bet {'won' if request.won else 'lost'}",
            "bet": {
                "id": bet.id,
                "status": bet.status.value,
                "profit_loss": bet.profit_loss,
                "closed_at": bet.closed_at.isoformat()
            },
            "budget_update": {
                "new_balance": result['new_balance'],
                "total_profit_loss": result['profit_loss'],
                "win_rate": result['win_rate'],
                "roi": result['roi']
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error closing bet: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/kelly-recommendation")
async def get_kelly_recommendation(
    confidence: float = Query(..., ge=0.0, le=1.0),
    odds: float = Query(..., gt=1.0),
    user_id: str = "default_user",
    db: Session = Depends(get_db)
):
    """
    Get Kelly Criterion stake recommendation for given parameters
    """
    try:
        budget = db.query(BettingBudget).filter_by(user_id=user_id).first()
        
        if not budget:
            raise HTTPException(status_code=404, detail="Budget not found")
        
        kelly_result = calculate_kelly_stake(
            confidence=confidence,
            odds=odds,
            bankroll=budget.current_balance
        )
        
        return {
            "status": "success",
            "recommendation": kelly_result,
            "bankroll": budget.current_balance
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error calculating Kelly recommendation: {e}")
        raise HTTPException(status_code=500, detail=str(e))

