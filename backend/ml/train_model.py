"""
ML Model Training Script
Trains the ConfidenceModel on signal data

Phase 2.2: Initial model training capability
"""

import os
import sys
import json
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
from loguru import logger

# Add parent directories to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from backend.ml.confidence_model import ConfidenceModel


def load_signal_data(data_dir: str = "data/ml_training") -> pd.DataFrame:
    """
    Load all signal data from JSON files
    
    Args:
        data_dir: Directory containing signal JSON files
        
    Returns:
        DataFrame with all signals
    """
    all_signals = []
    data_path = Path(data_dir)
    
    if not data_path.exists():
        logger.warning(f"Data directory {data_dir} does not exist")
        return pd.DataFrame()
    
    for json_file in data_path.glob("signals_*.json"):
        try:
            with open(json_file, 'r') as f:
                signals = json.load(f)
                logger.info(f"Loaded {len(signals)} signals from {json_file.name}")
                all_signals.extend(signals)
        except Exception as e:
            logger.error(f"Error loading {json_file}: {e}")
    
    if not all_signals:
        logger.warning("No signal data found")
        return pd.DataFrame()
    
    df = pd.DataFrame(all_signals)
    logger.info(f"Total signals loaded: {len(df)}")
    
    return df


def simulate_outcomes(df: pd.DataFrame) -> pd.DataFrame:
    """
    Simulate outcomes for signals that don't have actual outcomes yet
    
    Uses a heuristic based on confidence and odds to generate realistic
    win/loss distribution for initial training.
    
    This is a BOOTSTRAP method - real outcomes should be used when available.
    
    Args:
        df: DataFrame with signals
        
    Returns:
        DataFrame with simulated outcomes
    """
    logger.warning("⚠️ SIMULATING OUTCOMES - Use real outcomes when available!")
    
    # For each signal, simulate outcome based on:
    # - Higher confidence -> higher chance of win (but not 1:1)
    # - Reasonable variance to avoid overfitting
    
    outcomes = []
    
    for idx, row in df.iterrows():
        confidence = row.get('confidence', 0.5)
        odds = row.get('odds_at_signal', 2.0)
        strategy = row.get('strategy', 'unknown')
        
        # Base win probability from confidence (with some noise)
        # We add noise to prevent the model from just learning confidence=outcome
        noise = np.random.normal(0, 0.15)
        
        # Strategy-specific adjustments (based on theoretical win rates)
        strategy_adjustments = {
            'panic_rebound': 0.05,      # 64% theoretical
            'mean_reversion': 0.06,     # 66% theoretical
            'whale_shadow': 0.10,       # 77% theoretical
            'odds_velocity': 0.02,      # 58% theoretical
            'simple_odds_change': 0.0   # 55% theoretical
        }
        
        adjustment = strategy_adjustments.get(strategy, 0)
        
        # Calculate win probability
        win_prob = confidence * 0.7 + 0.15 + adjustment + noise
        win_prob = max(0.2, min(0.95, win_prob))  # Clamp between 20% and 95%
        
        # Simulate outcome
        won = np.random.random() < win_prob
        outcomes.append(1 if won else 0)
    
    df['outcome'] = outcomes
    
    # Calculate stats
    win_rate = df['outcome'].mean()
    logger.info(f"Simulated outcomes: {len(df)} signals, {win_rate:.1%} win rate")
    
    return df


def prepare_training_data(df: pd.DataFrame) -> tuple:
    """
    Prepare features and target for model training
    
    Args:
        df: DataFrame with signals and outcomes
        
    Returns:
        (X, y) tuple for training
    """
    # Required columns
    required = ['odds_at_signal', 'strategy', 'outcome']
    missing = [col for col in required if col not in df.columns]
    
    if missing:
        raise ValueError(f"Missing required columns: {missing}")
    
    # Prepare features
    X = df[['odds_at_signal', 'strategy']].copy()
    X = X.rename(columns={'odds_at_signal': 'odds'})
    
    # Add market column (default to match_winner)
    X['market'] = 'match_winner'
    
    # Add time features
    if 'hour_of_day' in df.columns:
        X['placed_at'] = pd.to_datetime(df['timestamp'])
    else:
        X['hour_of_day'] = 12  # Default
    
    # Target
    y = df['outcome'].astype(int)
    
    return X, y


def train_confidence_model(
    data_dir: str = "data/ml_training",
    model_path: str = "backend/ml/models/confidence_model.pkl",
    use_simulated: bool = True
) -> dict:
    """
    Train the confidence model
    
    Args:
        data_dir: Directory with signal data
        model_path: Path to save trained model
        use_simulated: Whether to simulate outcomes if none exist
        
    Returns:
        Training metrics
    """
    logger.info("🧠 Starting ML model training...")
    
    # Load data
    df = load_signal_data(data_dir)
    
    # Minimum samples needed for meaningful training
    MIN_SAMPLES = 50
    
    if df.empty or len(df) < MIN_SAMPLES:
        logger.warning(f"Insufficient real data ({len(df) if not df.empty else 0} samples, need {MIN_SAMPLES})")
        
        # Create synthetic training data for initial model
        logger.info("Creating synthetic training data for initial model...")
        synthetic_df = create_synthetic_data()
        
        if not df.empty:
            # Combine real and synthetic data, prioritizing real
            df = pd.concat([df, synthetic_df], ignore_index=True)
            logger.info(f"Combined {len(df)} samples (real + synthetic)")
        else:
            df = synthetic_df
    
    # Check if outcomes exist
    has_outcomes = 'outcome' in df.columns and df['outcome'].notna().any()
    
    if not has_outcomes:
        if use_simulated:
            df = simulate_outcomes(df)
        else:
            raise ValueError("No outcomes in data and use_simulated=False")
    
    # Filter to signals with outcomes
    df_with_outcomes = df[df['outcome'].notna()].copy()
    logger.info(f"Training on {len(df_with_outcomes)} signals with outcomes")
    
    if len(df_with_outcomes) < 10:
        logger.warning("Very limited training data - model may not generalize well")
    
    # Prepare features
    X, y = prepare_training_data(df_with_outcomes)
    
    # Initialize and train model
    model = ConfidenceModel(model_path=model_path)
    metrics = model.train(X, y)
    
    # Save model
    model.save_model()
    logger.success(f"✅ Model saved to {model_path}")
    
    # Log metrics
    logger.info(f"Training Results:")
    logger.info(f"  - ROC-AUC: {metrics['cv_roc_auc_mean']:.4f} ± {metrics['cv_roc_auc_std']:.4f}")
    logger.info(f"  - Samples: {metrics['n_samples']}")
    logger.info(f"  - Features: {metrics['n_features']}")
    
    return metrics


def create_synthetic_data() -> pd.DataFrame:
    """
    Create synthetic training data for initial model bootstrap
    
    This creates realistic-looking signal data for initial training
    when no real data is available.
    """
    logger.info("Creating synthetic training data...")
    
    strategies = [
        ('panic_rebound', 0.64),
        ('mean_reversion', 0.66),
        ('whale_shadow', 0.77),
        ('odds_velocity', 0.58),
        ('simple_odds_change', 0.55)
    ]
    
    signals = []
    
    for strategy, base_win_rate in strategies:
        # Generate 100 signals per strategy
        for i in range(100):
            odds = np.random.uniform(1.3, 4.0)
            confidence = np.random.uniform(0.55, 0.95)
            
            # Win probability adjusted by strategy
            win_prob = base_win_rate + np.random.normal(0, 0.08)
            win_prob = max(0.3, min(0.9, win_prob))
            
            outcome = 1 if np.random.random() < win_prob else 0
            
            signals.append({
                'signal_id': f"synthetic_{strategy}_{i}",
                'strategy': strategy,
                'timestamp': datetime.utcnow().isoformat(),
                'odds_at_signal': odds,
                'confidence': confidence,
                'outcome': outcome,
                'hour_of_day': np.random.randint(8, 22)
            })
    
    df = pd.DataFrame(signals)
    logger.info(f"Created {len(df)} synthetic signals")
    
    return df


def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Train ML Confidence Model')
    parser.add_argument('--data-dir', default='data/ml_training', help='Data directory')
    parser.add_argument('--model-path', default='backend/ml/models/confidence_model.pkl', help='Model save path')
    parser.add_argument('--no-simulate', action='store_true', help='Disable outcome simulation')
    
    args = parser.parse_args()
    
    try:
        metrics = train_confidence_model(
            data_dir=args.data_dir,
            model_path=args.model_path,
            use_simulated=not args.no_simulate
        )
        
        print("\n" + "="*50)
        print("TRAINING COMPLETE")
        print("="*50)
        print(f"ROC-AUC: {metrics['cv_roc_auc_mean']:.4f}")
        print(f"Model saved to: {args.model_path}")
        
    except Exception as e:
        logger.error(f"Training failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

