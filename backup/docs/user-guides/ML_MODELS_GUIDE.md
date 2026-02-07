# TITAN ML Models Guide

## Overview

This guide documents the machine learning models used in TITAN for betting signal optimization, confidence prediction, and strategy enhancement.

## Model Architecture

### 1. Confidence Prediction Model

**Purpose**: Predict win probability for betting signals

**Model Type**: Gradient Boosting Classifier (scikit-learn)

**Features**:
- `odds`: Decimal odds (1.5 - 5.0)
- `strategy`: Strategy type (panic_rebound, mean_reversion, whale_shadow)
- `market`: Market type (match_odds, over_under, innings_runs)
- `hour_of_day`: Time component (0-23)

**Target**: Binary outcome (1 = won, 0 = lost)

**Hyperparameters**:
```python
GradientBoostingClassifier(
    n_estimators=100,
    max_depth=5,
    learning_rate=0.1,
    min_samples_split=20,
    random_state=42
)
```

**Performance Metrics**:
- ROC-AUC: Target >0.75
- Brier Score: Target <0.15
- Calibration: Predicted probability should match actual win rate

### 2. Signal Quality Analyzer

**Purpose**: Analyze historical signal performance

**Methods**:
- Confidence calibration analysis
- Strategy performance comparison
- Timing pattern identification
- Feature importance calculation

**Output**:
```json
{
  "confidence_accuracy": {
    "brier_score": 0.12,
    "accuracy": 0.78,
    "calibration": {...}
  },
  "strategy_performance": {...},
  "optimal_threshold": 0.82
}
```

## Data Requirements

### Training Data

**Minimum Requirements**:
- 1000+ historical bets
- 90+ days of data
- Mix of strategies and markets
- Both wins and losses (avoid class imbalance)

**Data Schema**:
```python
{
    'id': int,
    'strategy': str,
    'market': str,
    'odds': float,
    'confidence': float,
    'stake': float,
    'won': int,  # 0 or 1
    'profit_loss': float,
    'placed_at': datetime,
    'auto_placed': bool
}
```

### Feature Engineering

**Raw Features → ML Features**:
```python
# Odds features
odds_momentum = df.groupby('match_id')['odds'].pct_change()
odds_volatility = df.groupby('match_id')['odds'].rolling(5).std()

# Time features
hour_of_day = pd.to_datetime(df['timestamp']).dt.hour
day_of_week = pd.to_datetime(df['timestamp']).dt.dayofweek

# Categorical encoding
strategy_encoded = LabelEncoder().fit_transform(df['strategy'])
```

## Training Pipeline

### 1. Data Preparation

```python
from backend.ml.signal_analysis import SignalAnalyzer

analyzer = SignalAnalyzer(db)
df = analyzer.load_historical_data(days=90)

# Validate data quality
assert len(df) >= 1000, "Insufficient training data"
assert df['won'].mean() > 0.3, "Class imbalance detected"
```

### 2. Model Training

```python
from backend.ml.confidence_model import ConfidenceModel

model = ConfidenceModel()

# Prepare features
X = df[['odds', 'strategy', 'market', 'placed_at']]
y = df['won']

# Train with cross-validation
metrics = model.train(X, y)

# Save model
model.save_model()
```

### 3. Model Evaluation

```python
# Time-series cross-validation
from sklearn.model_selection import TimeSeriesSplit

tscv = TimeSeriesSplit(n_splits=5)
scores = []

for train_idx, val_idx in tscv.split(X):
    X_train, X_val = X.iloc[train_idx], X.iloc[val_idx]
    y_train, y_val = y.iloc[train_idx], y.iloc[val_idx]
    
    model.fit(X_train, y_train)
    score = roc_auc_score(y_val, model.predict_proba(X_val)[:, 1])
    scores.append(score)

print(f"Mean ROC-AUC: {np.mean(scores):.4f} ± {np.std(scores):.4f}")
```

## API Endpoints

### Predict Confidence

```http
POST /api/ml/predict-confidence
Content-Type: application/json

{
  "odds": 2.5,
  "strategy": "mean_reversion",
  "market": "match_odds",
  "hour_of_day": 14
}
```

**Response**:
```json
{
  "predicted_confidence": 0.82,
  "model_version": "1.0",
  "features_used": ["odds", "strategy_encoded", "market_encoded", "hour_of_day"]
}
```

### Signal Analysis

```http
GET /api/ml/signal-analysis?days=90
```

**Response**:
```json
{
  "status": "success",
  "analysis": {
    "total_bets": 1247,
    "confidence_accuracy": {...},
    "strategy_performance": {...},
    "optimal_threshold": 0.82,
    "overall_roi": 12.4
  }
}
```

### Train Model

```http
POST /api/ml/train-confidence-model?min_samples=100
```

**Response**:
```json
{
  "status": "success",
  "message": "Model trained successfully",
  "metrics": {
    "cv_roc_auc_mean": 0.78,
    "cv_roc_auc_std": 0.03,
    "n_samples": 1247
  }
}
```

## Model Deployment

### Loading Model in Production

```python
from backend.ml.confidence_model import get_confidence_model

# Singleton instance - loaded once at startup
model = get_confidence_model()

# Make predictions
confidence = model.predict_confidence(signal_data)[0]
```

### Model Versioning

**File Structure**:
```
backend/ml/models/
├── confidence_model_v1.pkl
├── confidence_model_v2.pkl
└── confidence_model.pkl  # Current version (symlink)
```

### Rollback Strategy

```python
# Rollback to previous version
import shutil
shutil.copy('models/confidence_model_v1.pkl', 'models/confidence_model.pkl')

# Restart service to reload model
```

## Monitoring & Maintenance

### Performance Monitoring

**Track Daily**:
- Prediction accuracy on new bets
- Calibration drift (predicted vs actual)
- Brier score
- ROI performance

**Alert Conditions**:
- ROC-AUC drops below 0.70
- Brier score exceeds 0.20
- Calibration error > 0.10
- Negative 7-day ROI

### Retraining Schedule

**Automatic Retraining**:
- Weekly: Incremental training on new data
- Monthly: Full retraining with hyperparameter tuning
- Quarterly: Feature engineering review

**Manual Retraining Triggers**:
- Concept drift detected
- Performance degradation
- New strategies added
- Major system changes

### Model Degradation Detection

```python
from backend.ml.signal_analysis import analyze_signals

# Compare recent vs historical performance
recent_analysis = analyze_signals(db, days=7)
historical_analysis = analyze_signals(db, days=90)

recent_roi = recent_analysis['overall_roi']
historical_roi = historical_analysis['overall_roi']

if recent_roi < historical_roi - 5:  # 5% drop
    logger.warning("Model performance degradation detected!")
    # Trigger retraining
```

## Best Practices

### 1. Data Quality
- Remove outliers (odds > 10.0, invalid confidence)
- Handle missing values
- Check for data leakage
- Validate temporal ordering

### 2. Model Training
- Always use time-series cross-validation
- Never use future data in features
- Regularization to prevent overfitting
- Ensemble methods for robustness

### 3. Feature Engineering
- Domain knowledge is key
- Avoid look-ahead bias
- Feature importance analysis
- Incremental feature addition

### 4. Probability Calibration
- Use Platt scaling or isotonic regression
- Verify calibration on holdout set
- Reliability diagrams
- Brier score decomposition

### 5. Production Safety
- Fallback to default confidence (0.75)
- Model timeout (500ms max)
- Graceful degradation
- A/B testing new models

## Troubleshooting

### Issue: Low ROC-AUC (<0.70)

**Possible Causes**:
- Insufficient training data
- Poor feature engineering
- Class imbalance
- Overfitting

**Solutions**:
```python
# Check sample size
assert len(training_data) >= 1000

# Check class balance
print(f"Win rate: {training_data['won'].mean():.2%}")
# Should be 30-70%

# Add regularization
model = GradientBoostingClassifier(
    max_depth=3,  # Reduce from 5
    min_samples_split=50  # Increase from 20
)
```

### Issue: Poor Calibration

**Symptom**: Predicted probabilities don't match actual win rates

**Solution**: Apply calibration
```python
from sklearn.calibration import CalibratedClassifierCV

calibrated_model = CalibratedClassifierCV(
    base_model, 
    method='isotonic',
    cv=5
)
calibrated_model.fit(X_train, y_train)
```

### Issue: Concept Drift

**Symptom**: Model performance degrades over time

**Solution**: Regular retraining
```python
# Automate weekly retraining
from apscheduler.schedulers.background import BackgroundScheduler

scheduler = BackgroundScheduler()
scheduler.add_job(
    retrain_model, 
    'cron', 
    day_of_week='mon', 
    hour=3
)
scheduler.start()
```

## Future Enhancements

- [ ] LSTM for odds movement prediction
- [ ] Reinforcement learning for Kelly optimization
- [ ] Ensemble methods (XGBoost + Neural Network)
- [ ] Real-time model updates (online learning)
- [ ] Multi-output models (confidence + ROI)
- [ ] Transfer learning from similar sports
- [ ] Explainable AI (SHAP values)

## References

- [Gradient Boosting Documentation](https://scikit-learn.org/stable/modules/ensemble.html#gradient-boosting)
- [Probability Calibration](https://scikit-learn.org/stable/modules/calibration.html)
- [Time Series Cross-Validation](https://scikit-learn.org/stable/modules/cross_validation.html#time-series-split)
- [Kelly Criterion](https://en.wikipedia.org/wiki/Kelly_criterion)

---

**Remember**: ML models are only as good as the data they're trained on. Garbage in, garbage out!

