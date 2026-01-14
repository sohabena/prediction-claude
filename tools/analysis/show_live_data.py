"""
Show 5 sample records from live Redis data stream
"""
import redis
import json
from datetime import datetime

def show_live_samples():
    try:
        # Connect to Redis
        r = redis.Redis(host='localhost', port=6379, decode_responses=True)
        
        print("\n" + "="*100)
        print("📊 5 SAMPLE RECORDS FROM LIVE DATA STREAM")
        print("="*100)
        
        # Get from timescale queue
        queue_length = r.llen('timescale_queue')
        print(f"\n📈 Queue Length: {queue_length} events waiting to be processed")
        
        if queue_length == 0:
            print("\n⚠️  No events in queue. System is processing in real-time!")
            print("   Let me show you the latest events from Redis...")
        
        # Get last 5 events from queue
        events = []
        for i in range(min(5, queue_length)):
            event_json = r.lindex('timescale_queue', -1 - i)
            if event_json:
                events.append(json.loads(event_json))
        
        if not events:
            # Try to get from the stream directly by subscribing briefly
            print("\n🔴 LIVE - Capturing next 5 events from match_events channel...\n")
            pubsub = r.pubsub()
            pubsub.subscribe('match_events')
            
            count = 0
            for message in pubsub.listen():
                if message['type'] == 'message':
                    event = json.loads(message['data'])
                    events.append(event)
                    count += 1
                    if count >= 5:
                        break
            
            pubsub.close()
        
        for i, event in enumerate(events, 1):
            print(f"\n{'─'*100}")
            print(f"🔷 RECORD {i}")
            print(f"{'─'*100}")
            
            # Handle nested data structure
            data = event.get('data', event)
            
            print(f"  🕐 Timestamp:    {event.get('timestamp', 'N/A')}")
            print(f"  🏏 Match ID:     {event.get('match_id', data.get('match_id', 'N/A'))}")
            print(f"  📈 Market Type:  {event.get('market_type', data.get('market_type', 'N/A'))}")
            print(f"  👥 Team:         {event.get('team', data.get('team', 'N/A'))}")
            print(f"  💰 Back Price:   {event.get('back_price', data.get('back_price', event.get('odds', 'N/A')))}")
            print(f"  💸 Lay Price:    {event.get('lay_price', data.get('lay_price', 'N/A'))}")
            print(f"  📊 Volume:       {event.get('volume', data.get('volume', 'N/A'))}")
            print(f"  🎯 Score:        {event.get('score', data.get('score', 'N/A'))}")
            print(f"  🎳 Wickets:      {event.get('wickets', data.get('wickets', 'N/A'))}")
            print(f"  ⏱️  Overs:        {event.get('overs', data.get('overs', 'N/A'))}")
        
        print(f"\n{'='*100}")
        print("✅ Sample data shown successfully!")
        print("="*100 + "\n")
        
    except Exception as e:
        print(f"\n❌ Error: {e}\n")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    show_live_samples()

