"""
Check if is_live metadata is being set correctly in captured records
"""
import redis
import json

def check_metadata():
    try:
        r = redis.Redis(host='localhost', port=6379, decode_responses=True)
        
        print("\n" + "="*100)
        print("🔍 Checking is_live Metadata in Records")
        print("="*100)
        print("\n⏳ Capturing 3 records to check metadata...\n")
        
        pubsub = r.pubsub()
        pubsub.subscribe('match_events')
        
        events = []
        for message in pubsub.listen():
            if message['type'] == 'message':
                try:
                    event = json.loads(message['data'])
                    events.append(event)
                    print(f"✅ Captured event {len(events)}/3...")
                    
                    if len(events) >= 3:
                        break
                except json.JSONDecodeError:
                    continue
        
        pubsub.close()
        
        print("\n" + "="*100)
        print("📋 Metadata Analysis")
        print("="*100)
        
        for i, event in enumerate(events, 1):
            print(f"\n{'─'*100}")
            print(f"🔷 RECORD {i}")
            print(f"{'─'*100}")
            print(f"  Match ID: {event.get('match_id', 'N/A')}")
            print(f"  Team: {event.get('team', 'N/A')}")
            
            metadata = event.get('metadata', {})
            if metadata:
                print(f"\n  📦 Metadata:")
                print(f"     - is_live: {metadata.get('is_live', 'NOT SET')}")
                print(f"     - live_indicator: {metadata.get('live_indicator', 'NOT SET')}")
                print(f"     - match_name: {metadata.get('match_name', 'NOT SET')}")
                print(f"     - parser_version: {metadata.get('parser_version', 'NOT SET')}")
                print(f"     - source: {metadata.get('source', 'NOT SET')}")
            else:
                print(f"  ⚠️  NO METADATA FOUND")
        
        print(f"\n{'='*100}\n")
        
    except Exception as e:
        print(f"\n❌ Error: {e}\n")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    check_metadata()

