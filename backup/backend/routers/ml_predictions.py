"""
ML Prediction API Endpoints
Expose machine learning models via REST API
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from backend.db.connection import get_db
from backend.ml.signal_analysis import analyze_signals
from backend.ml.confidence_model import get_confidence_model
from pydantic import BaseModel
from typing import List, Optional
from loguru import logger
import pandas as pd

router = APIRouter(prefix="/api/ml", tags=["ml-predictions"])


class ConfidencePredictionRequest(BaseModel):
    odds: float
    strategy: str
    market: str = "match_odds"
    hour_of_day: Optional[int] = 12


class ConfidencePredictionResponse(BaseModel):
    predicted_confidence: float
    model_version: str
    features_used: List[str]


@router.post("/predict-confidence", response_model=ConfidencePredictionResponse)
async def predict_confidence(request: ConfidencePredictionRequest):
    """
    Get ML-predicted confidence score for a signal
    
    Uses trained Gradient Boosting model to predict win probability
    """
    try:
        model = get_confidence_model()
        
        # Prepare input data
        input_data = pd.DataFrame([{
            'odds': request.odds,
            'strategy': request.strategy,
            'market': request.market,
            'hour_of_day': request.hour_of_day
        }])
        
        # Get prediction
        confidence = model.predict_confidence(input_data)[0]
        
        return ConfidencePredictionResponse(
            predicted_confidence=float(confidence),
            model_version="1.0",
            features_used=model.feature_names
        )
        
    except Exception as e:
        logger.error(f"Error predicting confidence: {e}")
        # Fallback to default
        return ConfidencePredictionResponse(
            predicted_confidence=0.75,
            model_version="fallback",
            features_used=[]
        )


@router.get("/signal-analysis")
async def get_signal_analysis(
    days: int = 90,
    db: Session = Depends(get_db)
):
    """
    Get comprehensive signal quality analysis
    
    Analyzes historical performance to identify patterns
    """
    try:
        analysis = analyze_signals(db, days=days)
        return {
            "status": "success",
            "analysis": analysis
        }
    except Exception as e:
        logger.error(f"Error in signal analysis: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/train-confidence-model")
async def train_confidence_model(
    db: Session = Depends(get_db),
    min_samples: int = 100
):
    """
    Train/retrain the confidence prediction model
    
    Requires sufficient historical data (min 100 samples)
    """
    try:
        from backend.ml.signal_analysis import SignalAnalyzer
        
        analyzer = SignalAnalyzer(db)
        df = analyzer.load_historical_data(days=90)
        
        if len(df) < min_samples:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient data: {len(df)} samples (min: {min_samples})"
            )
        
        # Train model
        model = get_confidence_model()
        X = df[['odds', 'strategy', 'market', 'placed_at']]
        y = df['won']
        
        metrics = model.train(X, y)
        model.save_model()
        
        return {
            "status": "success",
            "message": "Model trained successfully",
            "metrics": metrics
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error training model: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/model-performance")
async def get_model_performance(db: Session = Depends(get_db)):
    """
    Get current ML model performance metrics
    """
    try:
        analysis = analyze_signals(db, days=30)
        
        if analysis['status'] == 'no_data':
            return {
                "status": "no_data",
                "message": "No performance data available"
            }
        
        return {
            "status": "success",
            "model_version": "1.0",
            "performance": {
                "brier_score": analysis['confidence_accuracy']['brier_score'],
                "overall_accuracy": analysis['confidence_accuracy']['accuracy'],
                "calibration": analysis['confidence_accuracy'].get('calibration', {}),
                "roi": analysis['overall_roi']
            },
            "data_period": "30 days"
        }
        
    except Exception as e:
        logger.error(f"Error getting model performance: {e}")
        raise HTTPException(status_code=500, detail=str(e))

