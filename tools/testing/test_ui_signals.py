"""Quick UI test - generates 10 test signals automatically"""
import redis
import json
import time
import random
from datetime import datetime
import os
from dotenv import load_dotenv

load_dotenv()

redis_client = redis.Redis(
    host=os.getenv('REDIS_HOST', 'localhost'),
    port=int(os.getenv('REDIS_PORT', 6379)),
    decode_responses=True
)

STRATEGIES = ['panic_rebound', 'mean_reversion', 'whale_shadow']
ACTIONS = ['BACK', 'LAY']
TEAMS = ['India', 'Australia', 'England', 'Pakistan']

REASONING = {
    'panic_rebound': "Market overreacted to wicket. RRR manageable. 68% recovery rate in similar situations.",
    'mean_reversion': "Over run rate 14.2, above match avg 8.1. 72% likely to revert to mean.",
    'whale_shadow': "Sharp money detected: Stake limits dropped 90%. Following smart money with 82% accuracy."
}

def generate_and_publish():
    strategy = random.choice(STRATEGIES)
    confidence = random.uniform(0.75, 0.92)
    
    signal = {
        "signal_id": f"test_{int(time.time() * 1000)}",
        "strategy": strategy,
        "action": random.choice(ACTIONS),
        "team": random.choice(TEAMS),
        "odds": round(random.uniform(1.70, 2.50), 2),
        "confidence": round(confidence, 3),
        "stake_recommended": random.randint(3000, 12000),
        "edge_window_seconds": 45,
        "reasoning": REASONING[strategy],
        "match_context": {
            "overs": round(random.uniform(8.0, 14.5), 1),
            "score": f"{random.randint(60, 110)}/{random.randint(1, 3)}",
            "run_rate": round(random.uniform(7.0, 9.5), 2)
        },
        "created_at": datetime.utcnow().isoformat() + "Z"
    }
    
    redis_client.publish('signals', json.dumps(signal))
    print(f"✅ {signal['strategy'][:4].upper()} | {signal['action']} {signal['team']} @ {signal['odds']} | {int(confidence*100)}%")

print("🏏 Generating 10 test signals...")
for i in range(10):
    generate_and_publish()
    time.sleep(1.5)
print("✅ Done!")

