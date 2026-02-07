"""
Test Signal Generator for TITAN HUD
Generates mock betting signals to test the frontend UI
"""

import redis
import json
import time
import random
from datetime import datetime
import os
from dotenv import load_dotenv

load_dotenv()

# Connect to Redis
redis_client = redis.Redis(
    host=os.getenv('REDIS_HOST', 'localhost'),
    port=int(os.getenv('REDIS_PORT', 6379)),
    decode_responses=True
)

# Test signal templates
STRATEGIES = ['panic_rebound', 'mean_reversion', 'whale_shadow']
ACTIONS = ['BACK', 'LAY']
TEAMS = ['India', 'Australia', 'England', 'Pakistan', 'South Africa', 'New Zealand']

REASONING_TEMPLATES = {
    'panic_rebound': [
        "Market overreacted to wicket. RRR still manageable at 7.2. Historical data shows 68% recovery rate in similar situations.",
        "Panic selling after boundary sequence. Bookmaker dropped odds by 25% but match state unchanged. Classic overreaction pattern.",
        "Key batsman dismissed but next partnership historically strong. Market pricing in collapse that statistics don't support."
    ],
    'mean_reversion': [
        "Current over run rate 14.2, significantly above match average of 8.1. Next over 72% likely to revert to mean based on 100+ match analysis.",
        "Session line adjusted up by 12 runs after 2 big overs. Mean reversion model shows 65% probability of under-performance next 3 overs.",
        "Extreme scoring phase (18 runs in 2 overs). Field restrictions lifted, bowler will adjust. Expect reversion to 6-8 RPO range."
    ],
    'whale_shadow': [
        "Sharp money detected: Stake limits dropped from ₹50k to ₹5k. Following the smart money direction with 82% historical accuracy.",
        "Unexplained odds movement against public sentiment. Syndicate activity likely. Bookmaker behavior suggests insider information.",
        "Volume imbalance detected: 85% of bets on one side but odds moving opposite direction. Classic whale shadow pattern."
    ]
}

def generate_signal(strategy=None, confidence=None):
    """Generate a realistic test signal"""
    
    if strategy is None:
        strategy = random.choice(STRATEGIES)
    
    if confidence is None:
        # Generate confidence based on strategy typical ranges
        if strategy == 'whale_shadow':
            confidence = random.uniform(0.77, 0.92)  # Highest confidence
        elif strategy == 'mean_reversion':
            confidence = random.uniform(0.66, 0.85)
        else:  # panic_rebound
            confidence = random.uniform(0.64, 0.82)
    
    action = random.choice(ACTIONS)
    team = random.choice(TEAMS)
    
    # Generate realistic odds
    if action == 'BACK':
        odds = round(random.uniform(1.65, 2.80), 2)
    else:  # LAY
        odds = round(random.uniform(1.50, 2.50), 2)
    
    # Calculate stake based on Kelly Criterion (simplified)
    edge = (confidence - (1/odds)) * 100
    base_bankroll = 100000
    stake = int(base_bankroll * (edge / 100) * 0.25)  # Quarter Kelly
    stake = max(1000, min(stake, 15000))  # Clamp between 1k and 15k
    
    # Edge window based on confidence
    edge_window = 45 if confidence > 0.80 else 35 if confidence > 0.70 else 30
    
    # Match context
    overs = round(random.uniform(7.0, 15.5), 1)
    score = random.randint(45, 120)
    wickets = random.randint(0, 4)
    run_rate = round(score / overs, 2)
    
    signal = {
        "signal_id": f"test_{int(time.time() * 1000)}_{random.randint(1000, 9999)}",
        "strategy": strategy,
        "action": action,
        "team": team,
        "odds": odds,
        "confidence": round(confidence, 3),
        "stake_recommended": stake,
        "edge_window_seconds": edge_window,
        "reasoning": random.choice(REASONING_TEMPLATES[strategy]),
        "match_context": {
            "overs": overs,
            "score": f"{score}/{wickets}",
            "run_rate": run_rate,
            "phase": "middle_overs" if overs < 15 else "death_overs"
        },
        "created_at": datetime.utcnow().isoformat() + "Z"
    }
    
    return signal

def publish_signal(signal):
    """Publish signal to Redis"""
    try:
        redis_client.publish('signals', json.dumps(signal))
        print(f"✅ Published: {signal['strategy']} | {signal['action']} {signal['team']} @ {signal['odds']} | Confidence: {int(signal['confidence']*100)}%")
        return True
    except Exception as e:
        print(f"❌ Error publishing signal: {e}")
        return False

def test_scenario_high_confidence():
    """Test scenario: High confidence signals (85%+)"""
    print("\n🎯 TEST SCENARIO 1: High Confidence Signals (85%+)")
    print("=" * 60)
    
    for i in range(3):
        signal = generate_signal(confidence=random.uniform(0.85, 0.92))
        publish_signal(signal)
        time.sleep(2)

def test_scenario_all_strategies():
    """Test scenario: One signal from each strategy"""
    print("\n🎯 TEST SCENARIO 2: All Three Strategies")
    print("=" * 60)
    
    for strategy in STRATEGIES:
        signal = generate_signal(strategy=strategy)
        publish_signal(signal)
        time.sleep(2)

def test_scenario_rapid_signals():
    """Test scenario: Multiple signals in quick succession"""
    print("\n🎯 TEST SCENARIO 3: Rapid Signal Generation")
    print("=" * 60)
    
    for i in range(5):
        signal = generate_signal()
        publish_signal(signal)
        time.sleep(0.5)  # Very fast

def test_scenario_varied_confidence():
    """Test scenario: Signals with different confidence levels"""
    print("\n🎯 TEST SCENARIO 4: Varied Confidence Levels")
    print("=" * 60)
    
    confidence_levels = [0.65, 0.75, 0.85, 0.90]
    for conf in confidence_levels:
        signal = generate_signal(confidence=conf)
        publish_signal(signal)
        time.sleep(2)

def test_scenario_continuous_stream():
    """Test scenario: Continuous signal stream (like live match)"""
    print("\n🎯 TEST SCENARIO 5: Continuous Stream (Press Ctrl+C to stop)")
    print("=" * 60)
    
    try:
        while True:
            signal = generate_signal()
            publish_signal(signal)
            # Random delay between 5-15 seconds (realistic match timing)
            delay = random.uniform(5, 15)
            print(f"⏳ Next signal in {delay:.1f} seconds...")
            time.sleep(delay)
    except KeyboardInterrupt:
        print("\n\n✋ Stopped continuous stream")

def main():
    """Main test menu"""
    print("\n" + "=" * 60)
    print("🏏 TITAN TEST SIGNAL GENERATOR")
    print("=" * 60)
    
    # Test Redis connection
    try:
        redis_client.ping()
        print("✅ Redis connection: OK")
    except Exception as e:
        print(f"❌ Redis connection failed: {e}")
        print("Make sure Redis is running (docker-compose up)")
        return
    
    print("\nSelect test scenario:")
    print("1. High Confidence Signals (85%+) - 3 signals")
    print("2. All Three Strategies - 3 signals")
    print("3. Rapid Signal Generation - 5 signals quickly")
    print("4. Varied Confidence Levels - 4 signals")
    print("5. Continuous Stream - Until stopped")
    print("6. Single Random Signal")
    print("7. Run All Scenarios")
    print("0. Exit")
    
    choice = input("\nEnter choice (0-7): ").strip()
    
    if choice == '1':
        test_scenario_high_confidence()
    elif choice == '2':
        test_scenario_all_strategies()
    elif choice == '3':
        test_scenario_rapid_signals()
    elif choice == '4':
        test_scenario_varied_confidence()
    elif choice == '5':
        test_scenario_continuous_stream()
    elif choice == '6':
        signal = generate_signal()
        publish_signal(signal)
    elif choice == '7':
        test_scenario_high_confidence()
        time.sleep(3)
        test_scenario_all_strategies()
        time.sleep(3)
        test_scenario_rapid_signals()
        time.sleep(3)
        test_scenario_varied_confidence()
    elif choice == '0':
        print("👋 Goodbye!")
    else:
        print("❌ Invalid choice")
    
    print("\n✅ Test complete!")

if __name__ == "__main__":
    main()

