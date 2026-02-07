"""
TITAN Backtesting Framework
Validates strategies against historical data

Phase 4: Strategy validation via backtesting

Features:
- Load historical odds from TimescaleDB or JSON files
- Replay through strategy logic
- Calculate: win rate, ROI, max drawdown, Sharpe ratio
- Generate detailed performance reports
"""

import os
import sys
import json
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field, asdict
from loguru import logger

# Add parent directories to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from cortex.strategies.panic_rebound import PanicReboundStrategy
from cortex.strategies.mean_reversion import MeanReversionStrategy
from cortex.strategies.whale_shadow import WhaleShadowStrategy
from cortex.strategies.odds_velocity import OddsVelocityStrategy
from cortex.strategies.simple_odds_change import SimpleOddsChangeStrategy
from cortex.match_state import MatchState


@dataclass
class BacktestTrade:
    """Record of a single backtest trade"""
    signal_id: str
    strategy: str
    timestamp: datetime
    action: str  # BACK or LAY
    entry_odds: float
    exit_odds: float  # Closing odds
    stake: float
    confidence: float
    outcome: str  # WIN, LOSS, PUSH
    profit_loss: float
    clv: float  # Closing Line Value
    
    def to_dict(self) -> Dict:
        return {
            **asdict(self),
            'timestamp': self.timestamp.isoformat()
        }


@dataclass
class BacktestResult:
    """Results from a backtest run"""
    strategy_name: str
    start_date: datetime
    end_date: datetime
    
    # Trade statistics
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    pushes: int = 0
    
    # Financial metrics
    total_profit_loss: float = 0.0
    roi_percentage: float = 0.0
    total_staked: float = 0.0
    
    # Risk metrics
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    avg_clv: float = 0.0
    
    # Individual trades
    trades: List[BacktestTrade] = field(default_factory=list)
    
    # Equity curve
    equity_curve: List[float] = field(default_factory=list)
    
    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.wins / self.total_trades
    
    @property
    def avg_profit_per_trade(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.total_profit_loss / self.total_trades
    
    def to_dict(self) -> Dict:
        return {
            'strategy_name': self.strategy_name,
            'start_date': self.start_date.isoformat(),
            'end_date': self.end_date.isoformat(),
            'total_trades': self.total_trades,
            'wins': self.wins,
            'losses': self.losses,
            'pushes': self.pushes,
            'win_rate': round(self.win_rate, 4),
            'total_profit_loss': round(self.total_profit_loss, 2),
            'roi_percentage': round(self.roi_percentage, 2),
            'total_staked': round(self.total_staked, 2),
            'avg_profit_per_trade': round(self.avg_profit_per_trade, 2),
            'max_drawdown': round(self.max_drawdown, 2),
            'sharpe_ratio': round(self.sharpe_ratio, 4),
            'avg_clv': round(self.avg_clv, 2)
        }


class Backtester:
    """
    TITAN Backtesting Engine
    
    Replays historical data through strategies to validate performance.
    """
    
    def __init__(self, starting_bankroll: float = 100000):
        self.starting_bankroll = starting_bankroll
        self.current_bankroll = starting_bankroll
        
        # Initialize all strategies
        self.strategies = {
            'panic_rebound': PanicReboundStrategy(),
            'mean_reversion': MeanReversionStrategy(),
            'whale_shadow': WhaleShadowStrategy(),
            'odds_velocity': OddsVelocityStrategy(),
            'simple_odds_change': SimpleOddsChangeStrategy()
        }
        
        logger.info(f"Backtester initialized with ₹{starting_bankroll:,.2f} bankroll")
    
    def load_data_from_json(self, data_dir: str = "data/ml_training") -> pd.DataFrame:
        """
        Load historical signal data from JSON files
        
        Args:
            data_dir: Directory containing signal JSON files
            
        Returns:
            DataFrame with historical data
        """
        all_data = []
        data_path = Path(data_dir)
        
        if not data_path.exists():
            logger.warning(f"Data directory {data_dir} does not exist")
            return pd.DataFrame()
        
        for json_file in data_path.glob("signals_*.json"):
            try:
                with open(json_file, 'r') as f:
                    data = json.load(f)
                    all_data.extend(data)
                    logger.info(f"Loaded {len(data)} records from {json_file.name}")
            except Exception as e:
                logger.error(f"Error loading {json_file}: {e}")
        
        if not all_data:
            logger.warning("No historical data found")
            return pd.DataFrame()
        
        df = pd.DataFrame(all_data)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df.sort_values('timestamp')
        
        logger.info(f"Total records loaded: {len(df)}")
        return df
    
    def generate_synthetic_data(self, num_events: int = 1000) -> pd.DataFrame:
        """
        Generate synthetic historical data for backtesting
        
        Creates realistic-looking match events with odds movements
        """
        logger.info(f"Generating {num_events} synthetic events...")
        
        events = []
        base_time = datetime.utcnow() - timedelta(days=30)
        
        # Simulate multiple matches
        num_matches = num_events // 50  # ~50 events per match
        
        for match_idx in range(num_matches):
            match_id = f"backtest_match_{match_idx}"
            match_time = base_time + timedelta(hours=match_idx * 3)
            
            # Initialize match state
            base_odds = np.random.uniform(1.5, 3.0)
            overs = 0.0
            score = 0
            wickets = 0
            
            # Generate events for this match
            for event_idx in range(50):
                # Progress match
                overs += 0.1 if event_idx % 6 != 0 else 0.4
                if overs > 20:
                    break
                
                # Simulate runs
                runs = np.random.choice([0, 1, 1, 2, 2, 4, 6], p=[0.3, 0.25, 0.15, 0.15, 0.05, 0.07, 0.03])
                score += runs
                
                # Simulate wickets (5% chance)
                is_wicket = np.random.random() < 0.05
                if is_wicket:
                    wickets += 1
                    # Odds spike on wicket
                    odds_change = np.random.uniform(0.1, 0.3)
                else:
                    # Normal odds drift
                    odds_change = np.random.normal(0, 0.02)
                
                current_odds = max(1.01, base_odds + odds_change)
                
                events.append({
                    'timestamp': match_time + timedelta(minutes=event_idx * 2),
                    'match_id': match_id,
                    'back_price': current_odds,
                    'lay_price': current_odds * 1.02,
                    'score': score,
                    'wickets': wickets,
                    'overs': round(overs, 1),
                    'is_wicket': is_wicket,
                    'is_boundary': runs >= 4,
                    'run_rate': score / max(overs, 1),
                    'is_suspended': False,
                    'stake_limit': 50000,
                    'batting_team': f'Team_{match_idx % 2}',
                    'team1': f'Team_{match_idx % 2}',
                    'team2': f'Team_{(match_idx + 1) % 2}'
                })
                
                base_odds = current_odds
        
        df = pd.DataFrame(events)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        
        logger.info(f"Generated {len(df)} synthetic events across {num_matches} matches")
        return df
    
    def run_backtest(
        self,
        data: pd.DataFrame,
        strategy_name: str = None,
        edge_window_seconds: int = 45
    ) -> Dict[str, BacktestResult]:
        """
        Run backtest on historical data
        
        Args:
            data: DataFrame with historical events
            strategy_name: Specific strategy to test (or None for all)
            edge_window_seconds: Edge window for outcome simulation
            
        Returns:
            Dictionary of BacktestResult by strategy name
        """
        if data.empty:
            logger.warning("No data to backtest")
            return {}
        
        # Select strategies to test
        if strategy_name:
            strategies_to_test = {strategy_name: self.strategies[strategy_name]}
        else:
            strategies_to_test = self.strategies
        
        results = {}
        
        for name, strategy in strategies_to_test.items():
            logger.info(f"Running backtest for {name}...")
            result = self._backtest_strategy(data, name, strategy, edge_window_seconds)
            results[name] = result
            
            logger.info(
                f"  {name}: {result.total_trades} trades, "
                f"{result.win_rate:.1%} win rate, "
                f"₹{result.total_profit_loss:+,.2f} P&L"
            )
        
        return results
    
    def _backtest_strategy(
        self,
        data: pd.DataFrame,
        strategy_name: str,
        strategy,
        edge_window_seconds: int
    ) -> BacktestResult:
        """Backtest a single strategy"""
        
        result = BacktestResult(
            strategy_name=strategy_name,
            start_date=data['timestamp'].min(),
            end_date=data['timestamp'].max()
        )
        
        # Track state per match
        match_states: Dict[str, MatchState] = {}
        equity = [self.starting_bankroll]
        
        # Group events by match
        for match_id, match_events in data.groupby('match_id'):
            match_events = match_events.sort_values('timestamp')
            
            # Create match state
            if match_id not in match_states:
                match_states[match_id] = MatchState(match_id=str(match_id))
            
            state = match_states[match_id]
            
            # Process each event
            for idx, event in match_events.iterrows():
                # Update match state
                self._update_match_state(state, event)
                
                # Evaluate strategy
                event_dict = event.to_dict()
                signal = strategy.evaluate(state, event_dict)
                
                if signal:
                    # Simulate trade outcome
                    trade = self._simulate_trade(
                        signal, state, event_dict, match_events, idx, edge_window_seconds
                    )
                    
                    if trade:
                        result.trades.append(trade)
                        result.total_trades += 1
                        result.total_staked += trade.stake
                        result.total_profit_loss += trade.profit_loss
                        
                        if trade.outcome == 'WIN':
                            result.wins += 1
                        elif trade.outcome == 'LOSS':
                            result.losses += 1
                        else:
                            result.pushes += 1
                        
                        # Update equity curve
                        equity.append(equity[-1] + trade.profit_loss)
        
        # Calculate final metrics
        result.equity_curve = equity
        result.roi_percentage = (result.total_profit_loss / result.total_staked * 100) if result.total_staked > 0 else 0
        result.max_drawdown = self._calculate_max_drawdown(equity)
        result.sharpe_ratio = self._calculate_sharpe_ratio(result.trades)
        result.avg_clv = np.mean([t.clv for t in result.trades]) if result.trades else 0
        
        return result
    
    def _update_match_state(self, state: MatchState, event: pd.Series):
        """Update match state from event data"""
        
        if pd.notna(event.get('back_price')):
            state.update_odds(
                float(event['back_price']),
                back_price=float(event.get('back_price', 0)),
                lay_price=float(event.get('lay_price')) if pd.notna(event.get('lay_price')) else None
            )
        
        if pd.notna(event.get('score')):
            state.score = int(event['score'])
        
        if pd.notna(event.get('wickets')):
            state.wickets = int(event['wickets'])
        
        if pd.notna(event.get('overs')):
            state.update_overs(float(event['overs']))
        
        if pd.notna(event.get('batting_team')):
            state.batting_team = str(event['batting_team'])
        
        if pd.notna(event.get('is_suspended')):
            state.update_suspension(bool(event['is_suspended']))
        
        if pd.notna(event.get('stake_limit')):
            state.update_stake_limit(int(event['stake_limit']))
    
    def _simulate_trade(
        self,
        signal,
        state: MatchState,
        event: Dict,
        match_events: pd.DataFrame,
        current_idx,
        edge_window_seconds: int
    ) -> Optional[BacktestTrade]:
        """
        Simulate trade outcome based on signal and future data
        
        Uses data from edge_window_seconds later to determine closing odds
        """
        try:
            current_time = pd.Timestamp(event['timestamp'])
            entry_odds = signal.odds
            stake = signal.stake_recommended
            
            # Find closing odds (after edge window)
            future_time = current_time + timedelta(seconds=edge_window_seconds)
            future_events = match_events[match_events['timestamp'] > future_time]
            
            if future_events.empty:
                # No future data - use last available odds
                closing_odds = entry_odds
            else:
                closing_odds = future_events.iloc[0]['back_price']
            
            # Calculate CLV
            if signal.action == 'BACK':
                clv = ((entry_odds / closing_odds) - 1) * 100
            else:
                clv = ((closing_odds / entry_odds) - 1) * 100
            
            # Simulate outcome based on CLV and confidence
            # Positive CLV suggests we got a good price
            win_prob = signal.confidence * (1 + clv / 100)
            win_prob = max(0.3, min(0.9, win_prob))  # Clamp
            
            outcome = 'WIN' if np.random.random() < win_prob else 'LOSS'
            
            if outcome == 'WIN':
                profit_loss = stake * (entry_odds - 1)
            else:
                profit_loss = -stake
            
            return BacktestTrade(
                signal_id=signal.signal_id,
                strategy=signal.strategy,
                timestamp=current_time.to_pydatetime(),
                action=signal.action,
                entry_odds=entry_odds,
                exit_odds=closing_odds,
                stake=stake,
                confidence=signal.confidence,
                outcome=outcome,
                profit_loss=profit_loss,
                clv=clv
            )
            
        except Exception as e:
            logger.error(f"Error simulating trade: {e}")
            return None
    
    def _calculate_max_drawdown(self, equity: List[float]) -> float:
        """Calculate maximum drawdown from equity curve"""
        if len(equity) < 2:
            return 0.0
        
        peak = equity[0]
        max_dd = 0.0
        
        for value in equity:
            if value > peak:
                peak = value
            
            drawdown = (peak - value) / peak * 100
            if drawdown > max_dd:
                max_dd = drawdown
        
        return max_dd
    
    def _calculate_sharpe_ratio(self, trades: List[BacktestTrade], risk_free_rate: float = 0.05) -> float:
        """Calculate Sharpe ratio from trades"""
        if len(trades) < 2:
            return 0.0
        
        returns = [t.profit_loss / t.stake for t in trades]
        
        avg_return = np.mean(returns)
        std_return = np.std(returns)
        
        if std_return == 0:
            return 0.0
        
        # Annualize (assuming ~250 trading days)
        sharpe = (avg_return - risk_free_rate / 250) / std_return * np.sqrt(250)
        
        return sharpe
    
    def generate_report(self, results: Dict[str, BacktestResult]) -> Dict:
        """Generate comprehensive backtest report"""
        
        report = {
            'generated_at': datetime.utcnow().isoformat(),
            'starting_bankroll': self.starting_bankroll,
            'strategies': {},
            'summary': {}
        }
        
        all_trades = []
        total_pnl = 0
        
        for name, result in results.items():
            report['strategies'][name] = result.to_dict()
            all_trades.extend(result.trades)
            total_pnl += result.total_profit_loss
        
        # Overall summary
        total_trades = len(all_trades)
        wins = sum(1 for t in all_trades if t.outcome == 'WIN')
        
        report['summary'] = {
            'total_strategies_tested': len(results),
            'total_trades': total_trades,
            'total_wins': wins,
            'overall_win_rate': wins / total_trades if total_trades > 0 else 0,
            'total_profit_loss': round(total_pnl, 2),
            'best_strategy': max(results.items(), key=lambda x: x[1].total_profit_loss)[0] if results else None,
            'worst_strategy': min(results.items(), key=lambda x: x[1].total_profit_loss)[0] if results else None
        }
        
        return report


def main():
    """Run backtesting"""
    import argparse
    
    parser = argparse.ArgumentParser(description='TITAN Backtester')
    parser.add_argument('--data-dir', default='data/ml_training', help='Data directory')
    parser.add_argument('--strategy', default=None, help='Specific strategy to test')
    parser.add_argument('--synthetic', action='store_true', help='Use synthetic data')
    parser.add_argument('--events', type=int, default=1000, help='Number of synthetic events')
    
    args = parser.parse_args()
    
    backtester = Backtester()
    
    # Load or generate data
    if args.synthetic:
        data = backtester.generate_synthetic_data(num_events=args.events)
    else:
        data = backtester.load_data_from_json(args.data_dir)
        
        if data.empty:
            logger.warning("No historical data found, generating synthetic data...")
            data = backtester.generate_synthetic_data(num_events=args.events)
    
    # Run backtest
    results = backtester.run_backtest(data, strategy_name=args.strategy)
    
    # Generate report
    report = backtester.generate_report(results)
    
    # Print summary
    print("\n" + "="*60)
    print("BACKTEST RESULTS")
    print("="*60)
    
    for name, result in results.items():
        print(f"\n{name.upper()}")
        print(f"  Trades: {result.total_trades}")
        print(f"  Win Rate: {result.win_rate:.1%}")
        print(f"  Total P&L: {result.total_profit_loss:+,.2f}")
        print(f"  ROI: {result.roi_percentage:.2f}%")
        print(f"  Max Drawdown: {result.max_drawdown:.2f}%")
        print(f"  Sharpe Ratio: {result.sharpe_ratio:.2f}")
        print(f"  Avg CLV: {result.avg_clv:.2f}%")
    
    print("\n" + "="*60)
    print(f"Best Strategy: {report['summary']['best_strategy']}")
    print(f"Total P&L: {report['summary']['total_profit_loss']:+,.2f}")
    print("="*60)
    
    # Save report
    report_path = Path('data/backtest_reports')
    report_path.mkdir(parents=True, exist_ok=True)
    
    report_file = report_path / f"backtest_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
    with open(report_file, 'w') as f:
        json.dump(report, f, indent=2, default=str)
    
    logger.info(f"Report saved to {report_file}")


if __name__ == "__main__":
    main()

