"""
Capture 5 fresh records from the live match_events stream
"""
import redis
import json
from datetime import datetime

def capture_live():
    try:
        # Connect to Redis
        r = redis.Redis(host='localhost', port=6379, decode_responses=True)
        
        print("\n" + "="*100)
        print("🔴 LIVE - Capturing 5 Fresh Records from match_events Stream...")
        print("="*100)
        print("\n⏳ Listening for events... (this will take a few seconds)\n")
        
        pubsub = r.pubsub()
        pubsub.subscribe('match_events')
        
        events = []
        for message in pubsub.listen():
            if message['type'] == 'message':
                try:
                    event = json.loads(message['data'])
                    events.append(event)
                    print(f"✅ Captured event {len(events)}/5...")
                    
                    if len(events) >= 5:
                        break
                except json.JSONDecodeError:
                    continue
        
        pubsub.close()
        
        print("\n" + "="*100)
        print("📊 5 FRESH LIVE RECORDS")
        print("="*100)
        
        for i, event in enumerate(events, 1):
            print(f"\n{'─'*100}")
            print(f"🔷 RECORD {i}")
            print(f"{'─'*100}")
            print(f"  🕐 Timestamp:      {event.get('timestamp', 'N/A')}")
            print(f"  🏏 Match ID:       {event.get('match_id', 'N/A')}")
            print(f"  📈 Market Type:    {event.get('market_type', 'N/A')}")
            print(f"  👥 Team:           {event.get('team', 'N/A')}")
            print(f"  💰 Back Price:     {event.get('back_price', 'N/A')}")
            print(f"  💸 Lay Price:      {event.get('lay_price', 'N/A')}")
            print(f"  📊 Volume:         {event.get('volume', 'N/A')}")
            print(f"  🎯 Score:          {event.get('score', 'N/A')}")
            print(f"  🎳 Wickets:        {event.get('wickets', 'N/A')}")
            print(f"  ⏱️  Overs:          {event.get('overs', 'N/A')}")
            print(f"  🔄 Is Suspended:   {event.get('is_suspended', 'N/A')}")
            print(f"  💵 Stake Limit:    {event.get('stake_limit', 'N/A')}")
        
        print(f"\n{'='*100}")
        print("✅ Live capture complete!")
        print("="*100 + "\n")
        
    except KeyboardInterrupt:
        print("\n\n⚠️  Capture interrupted by user\n")
    except Exception as e:
        print(f"\n❌ Error: {e}\n")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    capture_live()

