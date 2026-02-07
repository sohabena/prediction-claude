"""
Signal Quality Analysis Module
Analyzes historical signal performance and identifies patterns
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Tuple
from loguru import logger
from sqlalchemy.orm import Session
from backend.models.betting import VirtualBet, BetStatus
from datetime import datetime, timedelta


class SignalAnalyzer:
    """Analyze betting signal performance and accuracy"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def load_historical_data(self, days: int = 90) -> pd.DataFrame:
        """
        Load historical bet data for analysis
        
        Args:
            days: Number of days of history to load
        
        Returns:
            DataFrame with bet history
        """
        start_date = datetime.utcnow() - timedelta(days=days)
        
        bets = self.db.query(VirtualBet).filter(
            VirtualBet.placed_at >= start_date,
            VirtualBet.status.in_([BetStatus.WON, BetStatus.LOST])
        ).all()
        
        data = []
        for bet in bets:
            data.append({
                'id': bet.id,
                'strategy': bet.strategy,
                'market': bet.market,
                'odds': bet.odds,
                'confidence': bet.confidence,
                'stake': bet.stake,
                'won': 1 if bet.status == BetStatus.WON else 0,
                'profit_loss': bet.profit_loss,
                'placed_at': bet.placed_at,
                'auto_placed': bet.auto_placed
            })
        
        df = pd.DataFrame(data)
        logger.info(f"Loaded {len(df)} historical bets for analysis")
        return df
    
    def calculate_confidence_accuracy(self, df: pd.DataFrame) -> Dict[str, float]:
        """
        Calculate how well confidence scores predict actual outcomes
        
        Uses Brier score and calibration analysis
        """
        if len(df) == 0:
            return {'brier_score': 0.0, 'accuracy': 0.0}
        
        # Brier score: mean squared difference between predicted and actual
        brier_score = np.mean((df['confidence'] - df['won']) ** 2)
        
        # Overall accuracy
        accuracy = df['won'].mean()
        
        # Calibration by confidence bins
        bins = [0.6, 0.7, 0.8, 0.9, 1.0]
        calibration = {}
        
        for i in range(len(bins) - 1):
            mask = (df['confidence'] >= bins[i]) & (df['confidence'] < bins[i + 1])
            if mask.sum() > 0:
                predicted_prob = df[mask]['confidence'].mean()
                actual_rate = df[mask]['won'].mean()
                calibration[f"{int(bins[i]*100)}-{int(bins[i+1]*100)}%"] = {
                    'predicted': predicted_prob,
                    'actual': actual_rate,
                    'delta': abs(predicted_prob - actual_rate),
                    'count': mask.sum()
                }
        
        return {
            'brier_score': float(brier_score),
            'accuracy': float(accuracy),
            'calibration': calibration
        }
    
    def analyze_strategy_performance(self, df: pd.DataFrame) -> Dict[str, Dict]:
        """
        Analyze performance by strategy type
        """
        if len(df) == 0:
            return {}
        
        results = {}
        for strategy in df['strategy'].unique():
            strategy_df = df[df['strategy'] == strategy]
            
            results[strategy] = {
                'total_bets': len(strategy_df),
                'win_rate': strategy_df['won'].mean(),
                'avg_odds': strategy_df['odds'].mean(),
                'avg_confidence': strategy_df['confidence'].mean(),
                'total_pnl': strategy_df['profit_loss'].sum(),
                'roi': (strategy_df['profit_loss'].sum() / strategy_df['stake'].sum()) * 100 if strategy_df['stake'].sum() > 0 else 0
            }
        
        return results
    
    def identify_optimal_confidence_threshold(self, df: pd.DataFrame) -> float:
        """
        Find confidence threshold that maximizes ROI
        """
        if len(df) == 0:
            return 0.75
        
        thresholds = np.arange(0.65, 0.95, 0.05)
        best_roi = -np.inf
        best_threshold = 0.75
        
        for threshold in thresholds:
            filtered_df = df[df['confidence'] >= threshold]
            if len(filtered_df) > 10:  # Minimum sample size
                roi = (filtered_df['profit_loss'].sum() / filtered_df['stake'].sum()) * 100
                if roi > best_roi:
                    best_roi = roi
                    best_threshold = threshold
        
        logger.info(f"Optimal confidence threshold: {best_threshold:.2f} (ROI: {best_roi:.2f}%)")
        return float(best_threshold)
    
    def analyze_timing_patterns(self, df: pd.DataFrame) -> Dict[int, Dict]:
        """
        Analyze profitability by hour of day
        """
        if len(df) == 0:
            return {}
        
        df['hour'] = pd.to_datetime(df['placed_at']).dt.hour
        
        results = {}
        for hour in range(24):
            hour_df = df[df['hour'] == hour]
            if len(hour_df) > 0:
                results[hour] = {
                    'total_bets': len(hour_df),
                    'win_rate': hour_df['won'].mean(),
                    'total_pnl': hour_df['profit_loss'].sum(),
                    'avg_confidence': hour_df['confidence'].mean()
                }
        
        return results
    
    def feature_importance_analysis(self, df: pd.DataFrame) -> Dict[str, float]:
        """
        Calculate correlation of features with winning
        """
        if len(df) == 0:
            return {}
        
        features = ['odds', 'confidence', 'stake']
        importance = {}
        
        for feature in features:
            if feature in df.columns:
                correlation = df[feature].corr(df['won'])
                importance[feature] = abs(correlation)
        
        return importance
    
    def generate_analysis_report(self, days: int = 90) -> Dict:
        """
        Generate comprehensive analysis report
        """
        df = self.load_historical_data(days)
        
        if len(df) == 0:
            return {
                'status': 'no_data',
                'message': 'No historical bet data available for analysis'
            }
        
        report = {
            'status': 'success',
            'data_period': f'{days} days',
            'total_bets': len(df),
            'confidence_accuracy': self.calculate_confidence_accuracy(df),
            'strategy_performance': self.analyze_strategy_performance(df),
            'optimal_threshold': self.identify_optimal_confidence_threshold(df),
            'timing_patterns': self.analyze_timing_patterns(df),
            'feature_importance': self.feature_importance_analysis(df),
            'overall_roi': (df['profit_loss'].sum() / df['stake'].sum() * 100) if df['stake'].sum() > 0 else 0
        }
        
        logger.info(f"Analysis complete: {len(df)} bets, ROI: {report['overall_roi']:.2f}%")
        return report


# Helper function for easy access
def analyze_signals(db: Session, days: int = 90) -> Dict:
    """Run signal analysis"""
    analyzer = SignalAnalyzer(db)
    return analyzer.generate_analysis_report(days)

