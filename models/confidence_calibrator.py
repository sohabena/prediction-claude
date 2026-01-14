"""
ML Confidence Calibrator
Phase 4.1: Train ML confidence calibration model

This model predicts the actual win probability based on signal features.
Requires 1000+ signal outcomes before training.

The Brain (AI Scientist): "Confidence calibration is crucial.
Our strategies claim 70% confidence, but actual win rates may differ.
This model learns the true relationship."
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
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.model_selection import TimeSeriesSplit, cross_val_score
from sklearn.preprocessing import LabelEncoder, StandardScaler
from sklearn.metrics import roc_auc_score, brier_score_loss, log_loss
from sklearn.calibration import calibration_curve
import joblib
import redis
from dotenv import load_dotenv

load_dotenv()


class ConfidenceCalibrator:
    """
    ML model that calibrates confidence predictions
    
    Features:
    - odds_velocity: How fast odds moved before signal
    - match_phase: powerplay, middle, death
    - strategy_type: panic_rebound, odds_velocity, etc.
    - hour_of_day: Time of day (IST)
    - overs: Current overs
    - wickets: Current wickets
    - claimed_confidence: What the strategy claimed
    
    Target:
    - Actual outcome (1 = win, 0 = loss)
    
    Output:
    - Calibrated probability that the signal will win
    """
    
    MIN_SAMPLES_FOR_TRAINING = 1000
    MODEL_DIR = Path("models/trained")
    
    def __init__(self):
        self.model = None
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
        self.categorical_features = ['strategy', 'match_phase', 'action']
        self.numeric_features = [
            'odds_at_signal', 'claimed_confidence', 'odds_velocity',
            'overs', 'wickets', 'run_rate', 'hour_of_day', 'day_of_week'
        ]
        
        # Create model directory
        self.MODEL_DIR.mkdir(parents=True, exist_ok=True)
        
        # Try to load existing model
        self._load_model()
    
    def get_training_data(self, days: int = 30) -> pd.DataFrame:
        """
        Fetch training data from Redis
        
        Returns DataFrame with signal features and outcomes
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
                    # Only include records with outcomes
                    if data.get('outcome') is not None:
                        records.append(data)
            
            df = pd.DataFrame(records)
            logger.info(f"Retrieved {len(df)} training records from Redis")
            return df
            
        except Exception as e:
            logger.error(f"Error fetching training data: {e}")
            return pd.DataFrame()
    
    def prepare_features(self, df: pd.DataFrame, fit: bool = False) -> np.ndarray:
        """
        Prepare feature matrix for training/prediction
        
        Args:
            df: Raw data DataFrame
            fit: Whether to fit encoders/scaler (True for training)
        
        Returns:
            NumPy array of features
        """
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
        
        # Rename 'confidence' to 'claimed_confidence' if needed
        if 'confidence' in feature_df.columns and 'claimed_confidence' not in feature_df.columns:
            feature_df['claimed_confidence'] = feature_df['confidence']
        
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
                    # Handle unseen categories
                    encoded = []
                    for val in feature_df[col].astype(str):
                        if val in le.classes_:
                            encoded.append(le.transform([val])[0])
                        else:
                            encoded.append(-1)  # Unknown category
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
        """
        Train the confidence calibration model
        
        Args:
            force: If True, train even with fewer than MIN_SAMPLES
        
        Returns:
            Training metrics dictionary
        """
        logger.info("🧠 Starting confidence calibrator training...")
        
        # Get training data
        df = self.get_training_data(days=60)
        
        if len(df) < self.MIN_SAMPLES_FOR_TRAINING and not force:
            logger.warning(
                f"Insufficient training data: {len(df)} samples "
                f"(need {self.MIN_SAMPLES_FOR_TRAINING}). Use force=True to train anyway."
            )
            return {
                'status': 'insufficient_data',
                'samples': len(df),
                'required': self.MIN_SAMPLES_FOR_TRAINING
            }
        
        if len(df) == 0:
            logger.error("No training data available")
            return {'status': 'no_data'}
        
        # Prepare target variable
        df['target'] = df['outcome'].apply(lambda x: 1 if x == 'win' else 0)
        
        # Prepare features
        X = self.prepare_features(df, fit=True)
        y = df['target'].values
        
        logger.info(f"Training on {len(X)} samples with {X.shape[1]} features")
        
        # Initialize model
        self.model = GradientBoostingClassifier(
            n_estimators=100,
            max_depth=5,
            learning_rate=0.1,
            min_samples_split=20,
            subsample=0.8,
            random_state=42
        )
        
        # Time-series cross-validation
        tscv = TimeSeriesSplit(n_splits=5)
        
        try:
            cv_roc_scores = cross_val_score(self.model, X, y, cv=tscv, scoring='roc_auc')
            cv_brier_scores = cross_val_score(self.model, X, y, cv=tscv, scoring='neg_brier_score')
        except Exception as e:
            logger.warning(f"Cross-validation failed: {e}. Training on full data.")
            cv_roc_scores = [0.5]
            cv_brier_scores = [0.25]
        
        # Train final model on all data
        self.model.fit(X, y)
        self.is_trained = True
        
        # Calculate training metrics
        train_proba = self.model.predict_proba(X)[:, 1]
        
        self.training_metrics = {
            'status': 'trained',
            'samples': len(df),
            'cv_roc_auc_mean': float(np.mean(cv_roc_scores)),
            'cv_roc_auc_std': float(np.std(cv_roc_scores)),
            'cv_brier_mean': float(-np.mean(cv_brier_scores)),
            'train_roc_auc': float(roc_auc_score(y, train_proba)),
            'train_brier': float(brier_score_loss(y, train_proba)),
            'trained_at': datetime.utcnow().isoformat(),
            'feature_importances': dict(zip(
                self.numeric_features + self.categorical_features,
                self.model.feature_importances_.tolist()
            ))
        }
        
        logger.success(
            f"✅ Model trained! "
            f"ROC-AUC: {self.training_metrics['cv_roc_auc_mean']:.4f} ± {self.training_metrics['cv_roc_auc_std']:.4f}, "
            f"Brier: {self.training_metrics['cv_brier_mean']:.4f}"
        )
        
        # Save model
        self._save_model()
        
        return self.training_metrics
    
    def predict(self, df: pd.DataFrame) -> np.ndarray:
        """
        Predict calibrated confidence for new signals
        
        Args:
            df: DataFrame with signal features
        
        Returns:
            Array of calibrated probabilities
        """
        if not self.is_trained:
            logger.warning("Model not trained, returning original confidence")
            if 'confidence' in df.columns:
                return df['confidence'].values
            return np.full(len(df), 0.75)
        
        X = self.prepare_features(df, fit=False)
        probabilities = self.model.predict_proba(X)[:, 1]
        
        return probabilities
    
    def calibrate_signal(self, signal_data: Dict) -> float:
        """
        Calibrate a single signal's confidence
        
        Args:
            signal_data: Dictionary with signal features
        
        Returns:
            Calibrated probability
        """
        df = pd.DataFrame([signal_data])
        proba = self.predict(df)
        return float(proba[0])
    
    def _save_model(self):
        """Save model and encoders to disk"""
        model_path = self.MODEL_DIR / "confidence_calibrator_v1.pkl"
        
        model_data = {
            'model': self.model,
            'label_encoders': self.label_encoders,
            'scaler': self.scaler,
            'training_metrics': self.training_metrics,
            'categorical_features': self.categorical_features,
            'numeric_features': self.numeric_features
        }
        
        joblib.dump(model_data, model_path)
        logger.info(f"Model saved to {model_path}")
    
    def _load_model(self):
        """Load model from disk if exists"""
        model_path = self.MODEL_DIR / "confidence_calibrator_v1.pkl"
        
        if not model_path.exists():
            return
        
        try:
            model_data = joblib.load(model_path)
            self.model = model_data['model']
            self.label_encoders = model_data['label_encoders']
            self.scaler = model_data['scaler']
            self.training_metrics = model_data.get('training_metrics', {})
            self.is_trained = True
            
            logger.info(f"Loaded trained model from {model_path}")
            
        except Exception as e:
            logger.warning(f"Could not load model: {e}")
    
    def get_stats(self) -> Dict:
        """Get model statistics"""
        return {
            'is_trained': self.is_trained,
            'training_metrics': self.training_metrics,
            'min_samples_required': self.MIN_SAMPLES_FOR_TRAINING,
            'model_path': str(self.MODEL_DIR / "confidence_calibrator_v1.pkl")
        }


# Singleton instance
_calibrator: Optional[ConfidenceCalibrator] = None


def get_calibrator() -> ConfidenceCalibrator:
    """Get or create the singleton calibrator instance"""
    global _calibrator
    if _calibrator is None:
        _calibrator = ConfidenceCalibrator()
    return _calibrator


if __name__ == "__main__":
    """Command-line training interface"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Train Confidence Calibrator')
    parser.add_argument('--force', action='store_true', help='Train even with insufficient data')
    parser.add_argument('--stats', action='store_true', help='Show model statistics')
    args = parser.parse_args()
    
    calibrator = ConfidenceCalibrator()
    
    if args.stats:
        print(json.dumps(calibrator.get_stats(), indent=2))
    else:
        result = calibrator.train(force=args.force)
        print(json.dumps(result, indent=2, default=str))
