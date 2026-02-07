"""
Budget Management API Endpoints
Handles user budget operations
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from backend.db.connection import get_db
from backend.models.betting import BettingBudget
from backend.services.pnl_calculator import get_budget_statistics
from pydantic import BaseModel
from loguru import logger
from datetime import datetime

router = APIRouter(prefix="/api/budget", tags=["budget"])


class BudgetInitRequest(BaseModel):
    initial_amount: float = 50000.0
    user_id: str = "default_user"


class BudgetResetRequest(BaseModel):
    reset_amount: float = 50000.0


@router.post("/initialize")
async def initialize_budget(request: BudgetInitRequest, db: Session = Depends(get_db)):
    """
    Initialize or update budget for a user
    
    Default budget is ₹50,000 if not specified
    """
    try:
        # Check if budget already exists
        existing_budget = db.query(BettingBudget).filter_by(user_id=request.user_id).first()
        
        if existing_budget:
            return {
                "message": "Budget already exists",
                "budget": get_budget_statistics(existing_budget),
                "already_exists": True
            }
        
        # Create new budget
        new_budget = BettingBudget(
            user_id=request.user_id,
            current_balance=request.initial_amount,
            initial_amount=request.initial_amount,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        
        db.add(new_budget)
        db.commit()
        db.refresh(new_budget)
        
        logger.info(f"Budget initialized for user {request.user_id}: ₹{request.initial_amount}")
        
        return {
            "message": "Budget initialized successfully",
            "budget": get_budget_statistics(new_budget),
            "already_exists": False
        }
        
    except Exception as e:
        logger.error(f"Error initializing budget: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/current")
async def get_current_budget(user_id: str = "default_user", db: Session = Depends(get_db)):
    """
    Get current budget and statistics
    """
    try:
        budget = db.query(BettingBudget).filter_by(user_id=user_id).first()
        
        if not budget:
            raise HTTPException(status_code=404, detail="Budget not found. Please initialize budget first.")
        
        stats = get_budget_statistics(budget)
        
        return {
            "status": "success",
            "budget": stats
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching budget: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/reset")
async def reset_budget(request: BudgetResetRequest, user_id: str = "default_user", db: Session = Depends(get_db)):
    """
    Reset budget to a new amount
    
    This will:
    - Set current balance to reset_amount
    - Update initial_amount to reset_amount
    - Reset all statistics to zero
    - Keep bet history intact
    """
    try:
        budget = db.query(BettingBudget).filter_by(user_id=user_id).first()
        
        if not budget:
            raise HTTPException(status_code=404, detail="Budget not found")
        
        # Store old values for logging
        old_balance = budget.current_balance
        old_initial = budget.initial_amount
        
        # Reset budget
        budget.current_balance = request.reset_amount
        budget.initial_amount = request.reset_amount
        budget.total_profit_loss = 0.0
        budget.total_bets_placed = 0
        budget.total_bets_won = 0
        budget.total_bets_lost = 0
        budget.updated_at = datetime.utcnow()
        
        db.commit()
        db.refresh(budget)
        
        logger.info(f"Budget reset for user {user_id}: "
                   f"₹{old_balance} → ₹{request.reset_amount}")
        
        return {
            "message": "Budget reset successfully",
            "previous_balance": old_balance,
            "previous_initial": old_initial,
            "new_budget": get_budget_statistics(budget)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error resetting budget: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history")
async def get_budget_history(
    user_id: str = "default_user",
    days: int = 30,
    db: Session = Depends(get_db)
):
    """
    Get budget history over time
    
    Returns daily balance snapshots
    """
    try:
        from backend.models.betting import BettingHistory
        from datetime import timedelta
        
        budget = db.query(BettingBudget).filter_by(user_id=user_id).first()
        
        if not budget:
            raise HTTPException(status_code=404, detail="Budget not found")
        
        # Get historical data
        start_date = datetime.utcnow() - timedelta(days=days)
        
        history = db.query(BettingHistory).filter(
            BettingHistory.budget_id == budget.id,
            BettingHistory.date >= start_date,
            BettingHistory.period_type == 'daily'
        ).order_by(BettingHistory.date.asc()).all()
        
        history_data = [
            {
                'date': h.date.isoformat(),
                'ending_balance': h.ending_balance,
                'total_profit_loss': h.total_profit_loss,
                'total_bets': h.total_bets,
                'win_rate': h.win_rate,
                'roi': h.roi
            }
            for h in history
        ]
        
        return {
            "status": "success",
            "user_id": user_id,
            "days": days,
            "current_balance": budget.current_balance,
            "history": history_data,
            "total_records": len(history_data)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching budget history: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/summary")
async def get_budget_summary(user_id: str = "default_user", db: Session = Depends(get_db)):
    """
    Get comprehensive budget summary with key metrics
    """
    try:
        budget = db.query(BettingBudget).filter_by(user_id=user_id).first()
        
        if not budget:
            raise HTTPException(status_code=404, detail="Budget not found")
        
        # Calculate additional metrics
        days_active = (datetime.utcnow() - budget.created_at).days
        days_active = max(days_active, 1)  # Avoid division by zero
        
        avg_daily_pnl = budget.total_profit_loss / days_active
        
        return {
            "status": "success",
            "summary": {
                **get_budget_statistics(budget),
                "days_active": days_active,
                "avg_daily_pnl": round(avg_daily_pnl, 2),
                "balance_change": round(budget.current_balance - budget.initial_amount, 2),
                "balance_change_percent": budget.roi
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching budget summary: {e}")
        raise HTTPException(status_code=500, detail=str(e))

