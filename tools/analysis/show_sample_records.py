"""
Show 5 sample records from the database
"""
import psycopg2
from datetime import datetime

def show_samples():
    try:
        conn = psycopg2.connect(
            host='localhost',
            port=5432,
            database='titan_betting',
            user='titan',
            password='titan_secure_2025'
        )
        cursor = conn.cursor()
        
        # Get latest 5 records
        cursor.execute("""
            SELECT 
                timestamp, 
                match_id, 
                market_type, 
                team, 
                back_price, 
                lay_price, 
                volume,
                score,
                wickets,
                overs
            FROM market_ticks 
            ORDER BY timestamp DESC 
            LIMIT 5
        """)
        
        rows = cursor.fetchall()
        
        if not rows:
            print("\n⚠️  No records found in database yet. Scraper is still collecting data...")
            print("   Please wait a few seconds and try again.\n")
            return
        
        print("\n" + "="*100)
        print("📊 5 LATEST MARKET TICK RECORDS FROM DATABASE")
        print("="*100)
        
        for i, row in enumerate(rows, 1):
            print(f"\n{'─'*100}")
            print(f"🔷 RECORD {i}")
            print(f"{'─'*100}")
            print(f"  🕐 Timestamp:    {row[0]}")
            print(f"  🏏 Match ID:     {row[1]}")
            print(f"  📈 Market Type:  {row[2]}")
            print(f"  👥 Team:         {row[3]}")
            print(f"  💰 Back Price:   {row[4]}")
            print(f"  💸 Lay Price:    {row[5] if row[5] else 'N/A'}")
            print(f"  📊 Volume:       {row[6] if row[6] else 'N/A'}")
            print(f"  🎯 Score:        {row[7] if row[7] else 'N/A'}")
            print(f"  🎳 Wickets:      {row[8] if row[8] else 'N/A'}")
            print(f"  ⏱️  Overs:        {row[9] if row[9] else 'N/A'}")
        
        print(f"\n{'='*100}")
        
        # Get total count
        cursor.execute("SELECT COUNT(*) FROM market_ticks")
        total = cursor.fetchone()[0]
        print(f"📈 Total Records in Database: {total:,}")
        print("="*100 + "\n")
        
        conn.close()
        
    except Exception as e:
        print(f"\n❌ Error: {e}\n")

if __name__ == "__main__":
    show_samples()

