"""
Analytics API Endpoints
Pattern analysis and performance metrics

Phase 3.1: Added CLV (Closing Line Value) tracking endpoints
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from backend.db.connection import get_db
from backend.db.queries import (
    get_strategy_performance,
    get_confidence_accuracy,
    get_timing_patterns,
    get_market_efficiency,
    get_daily_pnl_series
)
from cortex.clv_tracker import get_clv_tracker
from loguru import logger

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/strategy-performance")
async def strategy_performance_analysis(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db)
):
    """
    Get profit/loss performance by strategy
    
    Analyzes which strategies (panic_rebound, mean_reversion, whale_shadow) are most profitable
    """
    try:
        data = get_strategy_performance(db, days)
        
        # Sort by total profit/loss descending
        data.sort(key=lambda x: x['total_profit_loss'], reverse=True)
        
        return {
            "status": "success",
            "days_analyzed": days,
            "strategies": data,
            "best_strategy": data[0] if data else None
        }
        
    except Exception as e:
        logger.error(f"Error in strategy performance analysis: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/confidence-accuracy")
async def confidence_accuracy_analysis(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db)
):
    """
    Analyze confidence accuracy
    
    Compares expected win rate (based on confidence) vs actual win rate
    """
    try:
        data = get_confidence_accuracy(db, days)
        
        return {
            "status": "success",
            "days_analyzed": days,
            "confidence_ranges": data,
            "summary": {
                "total_ranges": len(data),
                "most_accurate": max(data, key=lambda x: -abs(x['accuracy_delta'])) if data else None
            }
        }
        
    except Exception as e:
        logger.error(f"Error in confidence accuracy analysis: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/timing-patterns")
async def timing_patterns_analysis(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db)
):
    """
    Analyze best betting times
    
    Identifies which hours of the day are most profitable
    """
    try:
        data = get_timing_patterns(db, days)
        
        # Find best and worst hours
        best_hour = max(data, key=lambda x: x['total_profit_loss']) if data else None
        worst_hour = min(data, key=lambda x: x['total_profit_loss']) if data else None
        
        return {
            "status": "success",
            "days_analyzed": days,
            "hourly_performance": data,
            "best_hour": best_hour,
            "worst_hour": worst_hour
        }
        
    except Exception as e:
        logger.error(f"Error in timing patterns analysis: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/market-efficiency")
async def market_efficiency_analysis(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db)
):
    """
    Analyze market efficiency
    
    Identifies which markets (match_odds, over_under, innings_runs) are most profitable
    """
    try:
        data = get_market_efficiency(db, days)
        
        # Sort by profit/loss
        data.sort(key=lambda x: x['total_profit_loss'], reverse=True)
        
        return {
            "status": "success",
            "days_analyzed": days,
            "markets": data,
            "most_profitable_market": data[0] if data else None
        }
        
    except Exception as e:
        logger.error(f"Error in market efficiency analysis: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/daily-pnl")
async def daily_pnl_series(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db)
):
    """
    Get daily P&L time series
    
    Returns day-by-day profit/loss for charting
    """
    try:
        data = get_daily_pnl_series(db, days)
        
        # Calculate cumulative P&L
        cumulative = 0
        for item in data:
            cumulative += item['profit_loss']
            item['cumulative_pnl'] = round(cumulative, 2)
        
        return {
            "status": "success",
            "days_analyzed": days,
            "daily_series": data,
            "summary": {
                "total_days": len(data),
                "final_pnl": cumulative if data else 0,
                "avg_daily_pnl": round(cumulative / len(data), 2) if data else 0
            }
        }
        
    except Exception as e:
        logger.error(f"Error fetching daily P&L series: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/comprehensive")
async def comprehensive_analysis(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db)
):
    """
    Get comprehensive analytics report
    
    Combines all analysis endpoints into one comprehensive response
    """
    try:
        return {
            "status": "success",
            "days_analyzed": days,
            "strategy_performance": get_strategy_performance(db, days),
            "confidence_accuracy": get_confidence_accuracy(db, days),
            "timing_patterns": get_timing_patterns(db, days),
            "market_efficiency": get_market_efficiency(db, days),
            "daily_pnl": get_daily_pnl_series(db, days)
        }
        
    except Exception as e:
        logger.error(f"Error in comprehensive analysis: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Phase 3.1: CLV Tracking Endpoints ====================

@router.get("/clv/stats")
async def get_clv_stats():
    """
    Get overall CLV (Closing Line Value) statistics
    
    CLV measures if we're beating the closing line - the best indicator of edge.
    Positive CLV = sustainable edge, regardless of short-term variance.
    """
    try:
        tracker = get_clv_tracker()
        stats = tracker.get_clv_stats()
        
        return {
            "status": "success",
            "clv_stats": stats,
            "interpretation": {
                "average_clv": f"{stats['average_clv']:.2f}%" if stats['average_clv'] else "N/A",
                "positive_rate": f"{stats['positive_clv_rate']:.1%}" if stats['positive_clv_rate'] else "N/A",
                "edge_confirmed": stats['average_clv'] > 0 if stats['average_clv'] else False,
                "explanation": (
                    "Positive average CLV indicates we consistently get better odds than the closing line. "
                    "This is the gold standard for proving betting edge."
                )
            }
        }
        
    except Exception as e:
        logger.error(f"Error getting CLV stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/clv/by-strategy")
async def get_clv_by_strategy():
    """
    Get CLV breakdown by strategy
    
    Shows which strategies consistently beat the closing line
    """
    try:
        tracker = get_clv_tracker()
        strategy_clv = tracker.get_clv_by_strategy()
        
        # Rank strategies by average CLV
        ranked = sorted(
            [(k, v) for k, v in strategy_clv.items() if v['count'] > 0],
            key=lambda x: x[1]['average_clv'],
            reverse=True
        )
        
        return {
            "status": "success",
            "strategies": strategy_clv,
            "ranking": [{"strategy": k, **v} for k, v in ranked],
            "best_strategy": ranked[0][0] if ranked else None
        }
        
    except Exception as e:
        logger.error(f"Error getting CLV by strategy: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/clv/recent")
async def get_recent_clv(
    limit: int = Query(20, ge=1, le=100)
):
    """
    Get recent CLV records
    
    Shows the most recent signals with their CLV calculations
    """
    try:
        tracker = get_clv_tracker()
        recent = tracker.get_recent_clv(limit=limit)
        
        return {
            "status": "success",
            "count": len(recent),
            "records": recent
        }
        
    except Exception as e:
        logger.error(f"Error getting recent CLV: {e}")
        raise HTTPException(status_code=500, detail=str(e))

