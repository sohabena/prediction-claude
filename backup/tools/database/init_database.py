"""
TITAN Database Initialization Script
Creates TimescaleDB schema with hypertables and indexes
"""

import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
import os
from dotenv import load_dotenv
import sys

load_dotenv()

DB_CONFIG = {
    'host': os.getenv('POSTGRES_HOST', 'localhost'),
    'port': int(os.getenv('POSTGRES_PORT', 5432)),
    'database': os.getenv('POSTGRES_DB', 'titan_betting'),
    'user': os.getenv('POSTGRES_USER', 'titan'),
    'password': os.getenv('POSTGRES_PASSWORD', 'titan_secure_2025')
}

def create_database():
    """Create database if it doesn't exist"""
    try:
        # Connect to default postgres database
        conn = psycopg2.connect(
            host=DB_CONFIG['host'],
            port=DB_CONFIG['port'],
            database='postgres',
            user=DB_CONFIG['user'],
            password=DB_CONFIG['password']
        )
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cursor = conn.cursor()
        
        # Check if database exists
        cursor.execute(f"SELECT 1 FROM pg_database WHERE datname='{DB_CONFIG['database']}'")
        exists = cursor.fetchone()
        
        if not exists:
            cursor.execute(f"CREATE DATABASE {DB_CONFIG['database']}")
            print(f"✅ Database '{DB_CONFIG['database']}' created successfully")
        else:
            print(f"ℹ️  Database '{DB_CONFIG['database']}' already exists")
        
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"❌ Error creating database: {e}")
        sys.exit(1)

def init_schema():
    """Initialize database schema with TimescaleDB extension and tables"""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()
        
        # Enable TimescaleDB extension
        cursor.execute("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;")
        print("✅ TimescaleDB extension enabled")
        
        # Create market_ticks table (time-series data)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS market_ticks (
                time TIMESTAMPTZ NOT NULL,
                match_id VARCHAR(100) NOT NULL,
                market_type VARCHAR(50) NOT NULL,
                team VARCHAR(100),
                odds DECIMAL(10, 2),
                back_price DECIMAL(10, 2),
                lay_price DECIMAL(10, 2),
                volume BIGINT,
                score INT,
                wickets INT,
                overs DECIMAL(4, 1),
                run_rate DECIMAL(4, 2),
                required_run_rate DECIMAL(4, 2),
                is_suspended BOOLEAN DEFAULT FALSE,
                stake_limit INT,
                metadata JSONB,
                PRIMARY KEY (time, match_id, market_type)
            );
        """)
        print("✅ market_ticks table created")
        
        # Convert to hypertable
        try:
            cursor.execute("""
                SELECT create_hypertable('market_ticks', 'time', 
                    if_not_exists => TRUE,
                    chunk_time_interval => INTERVAL '1 day'
                );
            """)
            print("✅ market_ticks converted to hypertable")
        except Exception as e:
            if "already a hypertable" in str(e):
                print("ℹ️  market_ticks is already a hypertable")
            else:
                raise
        
        # Create indexes for fast queries
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_market_ticks_match_id 
            ON market_ticks (match_id, time DESC);
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_market_ticks_market_type 
            ON market_ticks (market_type, time DESC);
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_market_ticks_metadata 
            ON market_ticks USING GIN (metadata);
        """)
        print("✅ Indexes created on market_ticks")
        
        # Create signals table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id SERIAL PRIMARY KEY,
                signal_id VARCHAR(100) UNIQUE NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                match_id VARCHAR(100) NOT NULL,
                strategy VARCHAR(50) NOT NULL,
                action VARCHAR(20) NOT NULL,
                team VARCHAR(100),
                odds DECIMAL(10, 2),
                confidence DECIMAL(5, 4),
                stake_recommended INT,
                edge_window_seconds INT,
                reasoning TEXT,
                match_context JSONB,
                quality_gates_passed JSONB,
                status VARCHAR(20) DEFAULT 'active',
                outcome VARCHAR(20),
                profit_loss DECIMAL(12, 2),
                settled_at TIMESTAMPTZ
            );
        """)
        print("✅ signals table created")
        
        # Create indexes on signals
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_signals_match_id 
            ON signals (match_id, created_at DESC);
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_signals_strategy 
            ON signals (strategy, created_at DESC);
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_signals_status 
            ON signals (status, created_at DESC);
        """)
        print("✅ Indexes created on signals")
        
        # Create matches table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS matches (
                match_id VARCHAR(100) PRIMARY KEY,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                team1 VARCHAR(100),
                team2 VARCHAR(100),
                match_type VARCHAR(50),
                venue VARCHAR(200),
                start_time TIMESTAMPTZ,
                status VARCHAR(20),
                winner VARCHAR(100),
                metadata JSONB
            );
        """)
        print("✅ matches table created")
        
        # Create performance_metrics table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS performance_metrics (
                time TIMESTAMPTZ NOT NULL,
                metric_name VARCHAR(100) NOT NULL,
                metric_value DECIMAL(12, 4),
                strategy VARCHAR(50),
                match_id VARCHAR(100),
                metadata JSONB,
                PRIMARY KEY (time, metric_name)
            );
        """)
        print("✅ performance_metrics table created")
        
        # Convert to hypertable
        try:
            cursor.execute("""
                SELECT create_hypertable('performance_metrics', 'time', 
                    if_not_exists => TRUE,
                    chunk_time_interval => INTERVAL '7 days'
                );
            """)
            print("✅ performance_metrics converted to hypertable")
        except Exception as e:
            if "already a hypertable" in str(e):
                print("ℹ️  performance_metrics is already a hypertable")
            else:
                raise
        
        # Create continuous aggregates for analytics
        try:
            cursor.execute("""
                CREATE MATERIALIZED VIEW IF NOT EXISTS hourly_signal_stats
                WITH (timescaledb.continuous) AS
                SELECT 
                    time_bucket('1 hour', created_at) AS bucket,
                    strategy,
                    COUNT(*) as signal_count,
                    AVG(confidence) as avg_confidence,
                    COUNT(CASE WHEN outcome = 'won' THEN 1 END) as wins,
                    COUNT(CASE WHEN outcome = 'lost' THEN 1 END) as losses,
                    SUM(profit_loss) as total_pnl
                FROM signals
                WHERE created_at > NOW() - INTERVAL '30 days'
                GROUP BY bucket, strategy;
            """)
            print("✅ Continuous aggregate hourly_signal_stats created")
        except Exception as e:
            if "already exists" in str(e):
                print("ℹ️  Continuous aggregate already exists")
            else:
                print(f"⚠️  Warning creating continuous aggregate: {e}")
        
        conn.commit()
        cursor.close()
        conn.close()
        
        print("\n🎉 Database initialization completed successfully!")
        print(f"\n📊 Database: {DB_CONFIG['database']}")
        print(f"🔗 Connection: {DB_CONFIG['host']}:{DB_CONFIG['port']}")
        print("\n📋 Tables created:")
        print("  - market_ticks (hypertable)")
        print("  - signals")
        print("  - matches")
        print("  - performance_metrics (hypertable)")
        print("  - hourly_signal_stats (continuous aggregate)")
        
    except Exception as e:
        print(f"❌ Error initializing schema: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

def main():
    print("🚀 TITAN Database Initialization")
    print("=" * 50)
    
    create_database()
    init_schema()
    
    print("\n✅ All done! Database is ready for TITAN.")

if __name__ == "__main__":
    main()

