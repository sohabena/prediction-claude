"""
Rebound Window Predictor
Phase 4.2: Predict optimal timing for panic rebound signals

This model predicts:
1. Will a rebound occur after an odds spike?
2. How long until the rebound happens?

The Brain (AI Scientist): "Timing is everything in mean reversion.
This model learns the optimal edge_window_seconds for each signal."
"""

import os
import json
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from loguru import logger

# ML libraries
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.model_selection import TimeSeriesSplit, cross_val_score
from sklearn.preprocessing import LabelEncoder, StandardScaler
from sklearn.metrics import roc_auc_score, mean_absolute_error, r2_score
import joblib
import redis
from dotenv import load_dotenv

load_dotenv()


class ReboundPredictor:
    """
    ML model that predicts rebound timing and probability
    
    Model 1 - Rebound Classifier:
        Predicts: Will odds rebound within 2 minutes? (binary)
        Features: spike magnitude, match phase, momentum, historical patterns
    
    Model 2 - Window Regressor:
        Predicts: How many seconds until rebound? (continuous)
        Features: same as classifier
    
    Use case: Dynamically set edge_window_seconds for signals
    """
    
    MIN_SAMPLES = 500
    MODEL_DIR = Path("models/trained")
    
    def __init__(self):
        self.classifier = None  # Predicts if rebound will occur
        self.regressor = None   # Predicts time to rebound
        self.label_encoders: Dict[str, LabelEncoder] = {}
        self.scaler = StandardScaler()
        self.is_trained = False
        self.training_metrics = {}
        
        # Redis for data collection
        try:
            self.redis_client = redis.Redis(
                host=os.getenv('REDIS_HOST', 'localhost'),
                port=int(os.getenv('REDIS_PORT', 6379)),
                decode_responses=True
            )
        except Exception as e:
            logger.warning(f"Could not connect to Redis: {e}")
            self.redis_client = None
        
        # Feature configuration
        self.categorical_features = ['strategy', 'match_phase']
        self.numeric_features = [
            'spike_magnitude',      # How much odds spiked (%)
            'overs',
            'wickets',
            'run_rate',
            'odds_at_spike',
            'momentum_score',       # Momentum before spike
            'minutes_since_match_start'
        ]
        
        # Create model directory
        self.MODEL_DIR.mkdir(parents=True, exist_ok=True)
        
        # Try to load existing models
        self._load_models()
    
    def collect_rebound_data(self, odds_history: List[Dict]) -> List[Dict]:
        """
        Analyze odds history to find spike-rebound patterns
        
        Args:
            odds_history: List of {odds, timestamp, ...} records
        
        Returns:
            List of spike events with rebound info
        """
        if len(odds_history) < 10:
            return []
        
        spike_events = []
        
        for i in range(2, len(odds_history) - 2):
            current = odds_history[i]
            prev = odds_history[i-1]
            
            # Calculate spike magnitude
            if prev['odds'] and current['odds']:
                spike_pct = (current['odds'] - prev['odds']) / prev['odds']
                
                # Detect significant spike (> 5%)
                if abs(spike_pct) > 0.05:
                    # Look for rebound in next 2 minutes
                    rebound_found = False
                    rebound_time_seconds = None
                    
                    spike_time = current.get('timestamp')
                    if isinstance(spike_time, str):
                        spike_time = datetime.fromisoformat(spike_time.replace('Z', '+00:00'))
                    
                    for j in range(i+1, min(i+30, len(odds_history))):
                        future = odds_history[j]
                        future_time = future.get('timestamp')
                        
                        if isinstance(future_time, str):
                            future_time = datetime.fromisoformat(future_time.replace('Z', '+00:00'))
                        
                        time_diff = (future_time - spike_time).total_seconds()
                        
                        if time_diff > 120:  # 2 minute window
                            break
                        
                        # Check for rebound (odds moved back 50%+ toward original)
                        if future['odds'] and prev['odds'] and current['odds']:
                            recovery = (future['odds'] - current['odds']) / (prev['odds'] - current['odds'])
                            
                            if recovery >= 0.5:  # 50% recovery
                                rebound_found = True
                                rebound_time_seconds = time_diff
                                break
                    
                    spike_events.append({
                        'spike_magnitude': abs(spike_pct),
                        'spike_direction': 'up' if spike_pct > 0 else 'down',
                        'odds_at_spike': current['odds'],
                        'rebound_occurred': rebound_found,
                        'rebound_time_seconds': rebound_time_seconds or 120,
                        'timestamp': current.get('timestamp')
                    })
        
        return spike_events
    
    def get_training_data(self, days: int = 30) -> pd.DataFrame:
        """
        Fetch training data from Redis
        
        This requires historical odds data with timestamps
        """
        if not self.redis_client:
            logger.warning("No Redis connection")
            return pd.DataFrame()
        
        try:
            # Get signal IDs from sorted set
            cutoff = (datetime.utcnow() - timedelta(days=days)).timestamp()
            signal_ids = self.redis_client.zrangebyscore('ml_signals_by_time', cutoff, '+inf')
            
            records = []
            for signal_id in signal_ids:
                key = f"ml_signal:{signal_id}"
                data_json = self.redis_client.get(key)
                
                if data_json:
                    data = json.loads(data_json)
                    # We need signals that had rebounds
                    if data.get('outcome') is not None:
                        # Calculate spike magnitude from odds_velocity
                        spike_magnitude = abs(data.get('odds_velocity', 0))
                        
                        # Determine if rebound occurred (win means rebound happened)
                        rebound_occurred = data.get('outcome') == 'win'
                        
                        records.append({
                            'spike_magnitude': spike_magnitude,
                            'overs': data.get('overs', 10),
                            'wickets': data.get('wickets', 3),
                            'run_rate': data.get('run_rate', 7),
                            'odds_at_spike': data.get('odds_at_signal', 2.0),
                            'momentum_score': 50,  # Would come from enriched data
                            'minutes_since_match_start': data.get('overs', 10) * 4,
                            'strategy': data.get('strategy', 'unknown'),
                            'match_phase': data.get('match_phase', 'middle'),
                            'rebound_occurred': rebound_occurred,
                            'rebound_time_seconds': 30 if rebound_occurred else 120
                        })
            
            df = pd.DataFrame(records)
            logger.info(f"Retrieved {len(df)} training records for rebound prediction")
            return df
            
        except Exception as e:
            logger.error(f"Error fetching training data: {e}")
            return pd.DataFrame()
    
    def prepare_features(self, df: pd.DataFrame, fit: bool = False) -> np.ndarray:
        """Prepare feature matrix for training/prediction"""
        feature_df = df.copy()
        
        # Fill missing values
        for col in self.numeric_features:
            if col in feature_df.columns:
                feature_df[col] = feature_df[col].fillna(0)
            else:
                feature_df[col] = 0
        
        for col in self.categorical_features:
            if col in feature_df.columns:
                feature_df[col] = feature_df[col].fillna('unknown')
            else:
                feature_df[col] = 'unknown'
        
        # Encode categorical features
        encoded_cats = []
        for col in self.categorical_features:
            if fit:
                le = LabelEncoder()
                encoded = le.fit_transform(feature_df[col].astype(str))
                self.label_encoders[col] = le
            else:
                if col in self.label_encoders:
                    le = self.label_encoders[col]
                    encoded = []
                    for val in feature_df[col].astype(str):
                        if val in le.classes_:
                            encoded.append(le.transform([val])[0])
                        else:
                            encoded.append(-1)
                    encoded = np.array(encoded)
                else:
                    encoded = np.zeros(len(feature_df))
            encoded_cats.append(encoded.reshape(-1, 1))
        
        # Get numeric features
        numeric_data = feature_df[self.numeric_features].values
        
        # Scale numeric features
        if fit:
            numeric_scaled = self.scaler.fit_transform(numeric_data)
        else:
            numeric_scaled = self.scaler.transform(numeric_data)
        
        # Combine all features
        X = np.hstack([numeric_scaled] + encoded_cats)
        
        return X
    
    def train(self, force: bool = False) -> Dict:
        """Train the rebound prediction models"""
        logger.info("🧠 Starting rebound predictor training...")
        
        # Get training data
        df = self.get_training_data(days=60)
        
        if len(df) < self.MIN_SAMPLES and not force:
            logger.warning(
                f"Insufficient training data: {len(df)} samples "
                f"(need {self.MIN_SAMPLES}). Use force=True to train anyway."
            )
            return {
                'status': 'insufficient_data',
                'samples': len(df),
                'required': self.MIN_SAMPLES
            }
        
        if len(df) == 0:
            logger.error("No training data available")
            return {'status': 'no_data'}
        
        # Prepare features
        X = self.prepare_features(df, fit=True)
        y_class = df['rebound_occurred'].astype(int).values
        y_reg = df['rebound_time_seconds'].values
        
        logger.info(f"Training on {len(X)} samples")
        
        # Train classifier
        self.classifier = RandomForestClassifier(
            n_estimators=100,
            max_depth=8,
            min_samples_split=10,
            random_state=42
        )
        
        # Time-series cross-validation for classifier
        tscv = TimeSeriesSplit(n_splits=5)
        try:
            cv_roc_scores = cross_val_score(self.classifier, X, y_class, cv=tscv, scoring='roc_auc')
        except Exception as e:
            logger.warning(f"CV failed: {e}")
            cv_roc_scores = [0.5]
        
        self.classifier.fit(X, y_class)
        
        # Train regressor (only on rebounds that occurred)
        rebound_mask = y_class == 1
        if rebound_mask.sum() >= 20:
            self.regressor = RandomForestRegressor(
                n_estimators=100,
                max_depth=8,
                min_samples_split=10,
                random_state=42
            )
            self.regressor.fit(X[rebound_mask], y_reg[rebound_mask])
        else:
            logger.warning("Not enough rebound samples for regressor training")
            self.regressor = None
        
        self.is_trained = True
        
        # Calculate metrics
        train_proba = self.classifier.predict_proba(X)[:, 1]
        
        self.training_metrics = {
            'status': 'trained',
            'samples': len(df),
            'rebound_samples': int(rebound_mask.sum()),
            'cv_roc_auc_mean': float(np.mean(cv_roc_scores)),
            'cv_roc_auc_std': float(np.std(cv_roc_scores)),
            'train_roc_auc': float(roc_auc_score(y_class, train_proba)),
            'regressor_trained': self.regressor is not None,
            'trained_at': datetime.utcnow().isoformat()
        }
        
        if self.regressor is not None:
            pred_time = self.regressor.predict(X[rebound_mask])
            self.training_metrics['regressor_mae'] = float(mean_absolute_error(y_reg[rebound_mask], pred_time))
        
        logger.success(f"✅ Rebound predictor trained! ROC-AUC: {self.training_metrics['cv_roc_auc_mean']:.4f}")
        
        self._save_models()
        
        return self.training_metrics
    
    def predict_rebound(self, signal_data: Dict) -> Tuple[float, int]:
        """
        Predict rebound probability and optimal window
        
        Args:
            signal_data: Dictionary with signal features
        
        Returns:
            (rebound_probability, optimal_window_seconds)
        """
        if not self.is_trained:
            return 0.5, 45  # Default values
        
        df = pd.DataFrame([signal_data])
        X = self.prepare_features(df, fit=False)
        
        # Predict probability of rebound
        rebound_prob = float(self.classifier.predict_proba(X)[0, 1])
        
        # Predict optimal window (if regressor trained)
        if self.regressor is not None:
            optimal_window = max(15, min(120, int(self.regressor.predict(X)[0])))
        else:
            # Use heuristic based on probability
            optimal_window = int(30 + (1 - rebound_prob) * 60)
        
        return rebound_prob, optimal_window
    
    def _save_models(self):
        """Save models to disk"""
        model_path = self.MODEL_DIR / "rebound_predictor_v1.pkl"
        
        model_data = {
            'classifier': self.classifier,
            'regressor': self.regressor,
            'label_encoders': self.label_encoders,
            'scaler': self.scaler,
            'training_metrics': self.training_metrics
        }
        
        joblib.dump(model_data, model_path)
        logger.info(f"Models saved to {model_path}")
    
    def _load_models(self):
        """Load models from disk"""
        model_path = self.MODEL_DIR / "rebound_predictor_v1.pkl"
        
        if not model_path.exists():
            return
        
        try:
            model_data = joblib.load(model_path)
            self.classifier = model_data['classifier']
            self.regressor = model_data.get('regressor')
            self.label_encoders = model_data['label_encoders']
            self.scaler = model_data['scaler']
            self.training_metrics = model_data.get('training_metrics', {})
            self.is_trained = True
            
            logger.info(f"Loaded trained models from {model_path}")
            
        except Exception as e:
            logger.warning(f"Could not load models: {e}")
    
    def get_stats(self) -> Dict:
        """Get model statistics"""
        return {
            'is_trained': self.is_trained,
            'training_metrics': self.training_metrics,
            'min_samples_required': self.MIN_SAMPLES
        }


# Singleton instance
_predictor: Optional[ReboundPredictor] = None


def get_predictor() -> ReboundPredictor:
    """Get or create the singleton predictor instance"""
    global _predictor
    if _predictor is None:
        _predictor = ReboundPredictor()
    return _predictor


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Train Rebound Predictor')
    parser.add_argument('--force', action='store_true', help='Train even with insufficient data')
    parser.add_argument('--stats', action='store_true', help='Show model statistics')
    args = parser.parse_args()
    
    predictor = ReboundPredictor()
    
    if args.stats:
        print(json.dumps(predictor.get_stats(), indent=2))
    else:
        result = predictor.train(force=args.force)
        print(json.dumps(result, indent=2, default=str))
