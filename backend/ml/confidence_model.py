"""
ML Confidence Prediction Model
Predicts win probability using machine learning
"""

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.model_selection import TimeSeriesSplit, cross_val_score
from sklearn.preprocessing import LabelEncoder
import joblib
from pathlib import Path
from loguru import logger
from typing import Dict, Optional


class ConfidenceModel:
    """
    ML model to predict win probability for betting signals
    
    Features:
    - odds: Decimal odds offered
    - strategy: Type of strategy (panic_rebound, mean_reversion, whale_shadow)
    - market: Market type
    - hour_of_day: Time component
    """
    
    def __init__(self, model_path: Optional[str] = None):
        self.model = None
        self.label_encoders = {}
        self.feature_names = ['odds', 'strategy_encoded', 'market_encoded', 'hour_of_day']
        self.model_path = model_path or 'backend/ml/models/confidence_model.pkl'
        
        # Try to load existing model
        if Path(self.model_path).exists():
            self.load_model()
    
    def prepare_features(self, data: pd.DataFrame, fit_encoders: bool = False) -> pd.DataFrame:
        """
        Prepare features for model training/prediction
        
        Args:
            data: Raw data with strategy, market, odds
            fit_encoders: Whether to fit label encoders (training) or use existing (prediction)
        """
        features = data.copy()
        
        # Encode categorical variables
        for col in ['strategy', 'market']:
            if col not in features.columns:
                continue
                
            if fit_encoders:
                le = LabelEncoder()
                features[f'{col}_encoded'] = le.fit_transform(features[col])
                self.label_encoders[col] = le
            else:
                if col in self.label_encoders:
                    le = self.label_encoders[col]
                    # Handle unseen categories
                    features[f'{col}_encoded'] = features[col].apply(
                        lambda x: le.transform([x])[0] if x in le.classes_ else -1
                    )
                else:
                    features[f'{col}_encoded'] = 0
        
        # Time features
        if 'placed_at' in features.columns:
            features['hour_of_day'] = pd.to_datetime(features['placed_at']).dt.hour
        elif 'hour_of_day' not in features.columns:
            features['hour_of_day'] = 12  # Default midday
        
        return features[self.feature_names]
    
    def train(self, X: pd.DataFrame, y: pd.Series) -> Dict[str, float]:
        """
        Train the confidence prediction model
        
        Args:
            X: Features (odds, strategy, market, etc.)
            y: Target (1 = won, 0 = lost)
        
        Returns:
            Training metrics
        """
        logger.info(f"Training confidence model on {len(X)} samples...")
        
        # Prepare features
        X_prepared = self.prepare_features(X, fit_encoders=True)
        
        # Initialize model with parameters suitable for small datasets
        min_samples = max(2, len(X) // 10)
        self.model = GradientBoostingClassifier(
            n_estimators=min(100, len(X) * 2),  # Scale with data size
            max_depth=min(5, len(X) // 10 + 1),
            learning_rate=0.1,
            min_samples_split=min(min_samples, 20),
            random_state=42
        )
        
        # Adjust cross-validation folds based on data size
        n_samples = len(X)
        if n_samples < 10:
            # Too few samples - just train without CV
            logger.warning("Very small dataset - training without cross-validation")
            self.model.fit(X_prepared, y)
            metrics = {
                'cv_roc_auc_mean': 0.5,  # Unknown
                'cv_roc_auc_std': 0.0,
                'n_samples': n_samples,
                'n_features': len(self.feature_names),
                'warning': 'No cross-validation due to small dataset'
            }
        else:
            # Time-series cross-validation with appropriate folds
            n_splits = min(5, n_samples // 2)
            tscv = TimeSeriesSplit(n_splits=n_splits)
            cv_scores = cross_val_score(self.model, X_prepared, y, cv=tscv, scoring='roc_auc')
            
            # Train final model on all data
            self.model.fit(X_prepared, y)
            
            metrics = {
                'cv_roc_auc_mean': float(np.mean(cv_scores)),
                'cv_roc_auc_std': float(np.std(cv_scores)),
                'n_samples': n_samples,
                'n_features': len(self.feature_names)
            }
        
        logger.info(f"Model trained: ROC-AUC = {metrics['cv_roc_auc_mean']:.4f} ± {metrics.get('cv_roc_auc_std', 0):.4f}")
        return metrics
    
    def predict_confidence(self, data: pd.DataFrame) -> np.ndarray:
        """
        Predict win probability for new signals
        
        Args:
            data: Signal data with odds, strategy, market
        
        Returns:
            Array of predicted win probabilities
        """
        if self.model is None:
            logger.warning("Model not trained, returning default confidence")
            return np.full(len(data), 0.75)
        
        X = self.prepare_features(data, fit_encoders=False)
        probabilities = self.model.predict_proba(X)[:, 1]
        
        return probabilities
    
    def save_model(self):
        """Save model to disk"""
        Path(self.model_path).parent.mkdir(parents=True, exist_ok=True)
        
        model_data = {
            'model': self.model,
            'label_encoders': self.label_encoders,
            'feature_names': self.feature_names
        }
        
        joblib.dump(model_data, self.model_path)
        logger.info(f"Model saved to {self.model_path}")
    
    def load_model(self):
        """Load model from disk"""
        try:
            model_data = joblib.load(self.model_path)
            self.model = model_data['model']
            self.label_encoders = model_data['label_encoders']
            self.feature_names = model_data['feature_names']
            logger.info(f"Model loaded from {self.model_path}")
        except Exception as e:
            logger.warning(f"Failed to load model: {e}")


# Global model instance
_confidence_model = None

def get_confidence_model() -> ConfidenceModel:
    """Get or create global confidence model"""
    global _confidence_model
    if _confidence_model is None:
        _confidence_model = ConfidenceModel()
    return _confidence_model

