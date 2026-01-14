"""
Match-Specific Budget Management API
Handles allocation and tracking of budgets per live match
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from backend.db.connection import get_db
from backend.models.betting import BettingBudget, VirtualBet, BetStatus
from backend.services.pnl_calculator import get_budget_statistics
from pydantic import BaseModel
from typing import List, Optional
from loguru import logger
from datetime import datetime

router = APIRouter(prefix="/api/match-budgets", tags=["match-budgets"])


class AllocateBudgetRequest(BaseModel):
    match_id: str
    match_name: str
    amount: float


class LiveMatchBudget(BaseModel):
    match_id: str
    match_name: str
    budget: dict
    active_bets_count: int
    total_staked: float


@router.post("/allocate")
async def allocate_match_budget(
    request: AllocateBudgetRequest,
    user_id: str = "default_user",
    db: Session = Depends(get_db)
):
    """
    Allocate budget for a specific match from master budget
    
    Creates a new match-specific budget by transferring funds from master
    """
    try:
        # Get master budget
        master_budget = db.query(BettingBudget).filter_by(
            user_id=user_id,
            match_id=None
        ).first()
        
        if not master_budget:
            raise HTTPException(status_code=404, detail="Master budget not found")
        
        # Check if allocation amount is valid
        if request.amount <= 0:
            raise HTTPException(status_code=400, detail="Allocation amount must be positive")
        
        if request.amount > master_budget.current_balance:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient master budget. Available: ₹{master_budget.current_balance}"
            )
        
        # Check if match budget already exists
        existing_match_budget = db.query(BettingBudget).filter_by(
            user_id=user_id,
            match_id=request.match_id
        ).first()
        
        if existing_match_budget:
            raise HTTPException(
                status_code=400,
                detail=f"Budget for match {request.match_name} already exists"
            )
        
        # Deduct from master budget
        master_budget.current_balance -= request.amount
        master_budget.updated_at = datetime.utcnow()
        
        # Create match-specific budget
        match_budget = BettingBudget(
            user_id=user_id,
            match_id=request.match_id,
            match_name=request.match_name,
            current_balance=request.amount,
            initial_amount=request.amount,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        
        db.add(match_budget)
        db.commit()
        db.refresh(match_budget)
        db.refresh(master_budget)
        
        logger.info(f"Allocated ₹{request.amount} to match {request.match_name}")
        
        return {
            "status": "success",
            "message": f"Allocated ₹{request.amount} to {request.match_name}",
            "match_budget": get_budget_statistics(match_budget),
            "master_budget_remaining": master_budget.current_balance
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error allocating match budget: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/live-matches")
async def get_live_match_budgets(
    user_id: str = "default_user",
    db: Session = Depends(get_db)
):
    """
    Get all active match budgets (representing live matches user is betting on)
    """
    try:
        match_budgets = db.query(BettingBudget).filter_by(
            user_id=user_id,
            is_active=True
        ).filter(
            BettingBudget.match_id.isnot(None)
        ).all()
        
        result = []
        for budget in match_budgets:
            # Count active bets for this match
            active_bets_count = db.query(VirtualBet).filter(
                VirtualBet.budget_id == budget.id,
                VirtualBet.status == BetStatus.PENDING
            ).count()
            
            # Sum total staked
            total_staked = db.query(VirtualBet).filter(
                VirtualBet.budget_id == budget.id,
                VirtualBet.status == BetStatus.PENDING
            ).with_entities(
                db.func.sum(VirtualBet.stake)
            ).scalar() or 0.0
            
            result.append({
                "match_id": budget.match_id,
                "match_name": budget.match_name,
                "budget": get_budget_statistics(budget),
                "active_bets_count": active_bets_count,
                "total_staked": round(total_staked, 2)
            })
        
        return {
            "status": "success",
            "live_matches": result,
            "total_matches": len(result)
        }
        
    except Exception as e:
        logger.error(f"Error fetching live match budgets: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/match/{match_id}")
async def get_match_budget(
    match_id: str,
    user_id: str = "default_user",
    db: Session = Depends(get_db)
):
    """
    Get budget details for a specific match
    """
    try:
        budget = db.query(BettingBudget).filter_by(
            user_id=user_id,
            match_id=match_id
        ).first()
        
        if not budget:
            raise HTTPException(status_code=404, detail="Match budget not found")
        
        # Get active bets for this match
        active_bets = db.query(VirtualBet).filter(
            VirtualBet.budget_id == budget.id,
            VirtualBet.status == BetStatus.PENDING
        ).all()
        
        # Get closed bets
        closed_bets = db.query(VirtualBet).filter(
            VirtualBet.budget_id == budget.id,
            VirtualBet.status.in_([BetStatus.WON, BetStatus.LOST])
        ).order_by(VirtualBet.closed_at.desc()).limit(10).all()
        
        return {
            "status": "success",
            "budget": get_budget_statistics(budget),
            "active_bets_count": len(active_bets),
            "recent_closed_bets": len(closed_bets)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching match budget: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/match/{match_id}/close")
async def close_match_budget(
    match_id: str,
    user_id: str = "default_user",
    db: Session = Depends(get_db)
):
    """
    Close match budget and return remaining funds to master budget
    
    Can only close if no pending bets exist
    """
    try:
        # Get match budget
        match_budget = db.query(BettingBudget).filter_by(
            user_id=user_id,
            match_id=match_id
        ).first()
        
        if not match_budget:
            raise HTTPException(status_code=404, detail="Match budget not found")
        
        # Check for pending bets
        pending_bets = db.query(VirtualBet).filter(
            VirtualBet.budget_id == match_budget.id,
            VirtualBet.status == BetStatus.PENDING
        ).count()
        
        if pending_bets > 0:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot close budget: {pending_bets} pending bets remain"
            )
        
        # Get master budget
        master_budget = db.query(BettingBudget).filter_by(
            user_id=user_id,
            match_id=None
        ).first()
        
        if not master_budget:
            raise HTTPException(status_code=404, detail="Master budget not found")
        
        # Transfer remaining balance back to master
        remaining = match_budget.current_balance
        master_budget.current_balance += remaining
        master_budget.updated_at = datetime.utcnow()
        
        # Mark match budget as inactive
        match_budget.is_active = False
        match_budget.updated_at = datetime.utcnow()
        
        db.commit()
        
        logger.info(f"Closed match budget {match_id}, returned ₹{remaining} to master")
        
        return {
            "status": "success",
            "message": f"Match budget closed. ₹{remaining} returned to master budget",
            "amount_returned": remaining,
            "final_pnl": match_budget.total_profit_loss,
            "master_budget_new_balance": master_budget.current_balance
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error closing match budget: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/master")
async def get_master_budget(
    user_id: str = "default_user",
    db: Session = Depends(get_db)
):
    """
    Get master budget (overall funds not allocated to specific matches)
    """
    try:
        master = db.query(BettingBudget).filter_by(
            user_id=user_id,
            match_id=None
        ).first()
        
        if not master:
            raise HTTPException(status_code=404, detail="Master budget not found")
        
        # Calculate total allocated to matches
        allocated = db.query(BettingBudget).filter(
            BettingBudget.user_id == user_id,
            BettingBudget.match_id.isnot(None),
            BettingBudget.is_active == True
        ).with_entities(
            db.func.sum(BettingBudget.current_balance)
        ).scalar() or 0.0
        
        return {
            "status": "success",
            "master_budget": get_budget_statistics(master),
            "total_allocated_to_matches": round(allocated, 2),
            "total_funds": round(master.current_balance + allocated, 2)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching master budget: {e}")
        raise HTTPException(status_code=500, detail=str(e))

