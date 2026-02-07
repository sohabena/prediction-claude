"""
TITAN Health Check Script
Verifies all system components are operational
"""

import os
import sys
import redis
import psycopg2
import requests
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

def check_redis():
    """Check Redis connection"""
    try:
        client = redis.Redis(
            host=os.getenv('REDIS_HOST', 'localhost'),
            port=int(os.getenv('REDIS_PORT', 6379))
        )
        client.ping()
        logger.info("✅ Redis: Connected")
        return True
    except Exception as e:
        logger.error(f"❌ Redis: Failed - {e}")
        return False

def check_database():
    """Check TimescaleDB connection"""
    try:
        conn = psycopg2.connect(
            host=os.getenv('POSTGRES_HOST', 'localhost'),
            port=int(os.getenv('POSTGRES_PORT', 5432)),
            database=os.getenv('POSTGRES_DB', 'titan_betting'),
            user=os.getenv('POSTGRES_USER', 'titan'),
            password=os.getenv('POSTGRES_PASSWORD', 'titan_secure_2025')
        )
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.close()
        conn.close()
        logger.info("✅ TimescaleDB: Connected")
        return True
    except Exception as e:
        logger.error(f"❌ TimescaleDB: Failed - {e}")
        return False

def check_backend_api():
    """Check Backend API"""
    try:
        response = requests.get('http://localhost:8000/api/health', timeout=5)
        if response.status_code == 200:
            logger.info("✅ Backend API: Running")
            return True
        else:
            logger.error(f"❌ Backend API: HTTP {response.status_code}")
            return False
    except Exception as e:
        logger.error(f"❌ Backend API: Not running - {e}")
        return False

def main():
    """Run all health checks"""
    logger.info("🏥 TITAN Health Check")
    logger.info("=" * 50)
    
    results = {
        'Redis': check_redis(),
        'TimescaleDB': check_database(),
        'Backend API': check_backend_api()
    }
    
    logger.info("=" * 50)
    logger.info(f"Results: {sum(results.values())}/{len(results)} services healthy")
    
    if all(results.values()):
        logger.info("✅ All systems operational")
        sys.exit(0)
    else:
        logger.error("⚠️  Some systems are down")
        sys.exit(1)

if __name__ == "__main__":
    main()

