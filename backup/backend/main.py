"""
TITAN Backend API
FastAPI application with REST endpoints and WebSocket support
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
import redis
import json
import os
import sys
import time
from typing import List
from datetime import datetime
from dotenv import load_dotenv
from loguru import logger

# Data flow logging and validation
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.flow_logger import get_flow_logger
from utils.data_validators import APIResponseValidator
from utils.data_fingerprint import create_fingerprint, track_data, get_validation_summary

# Initialize flow logger and validator for this component
flow_log = get_flow_logger("Backend")
api_validator = APIResponseValidator()

# Import routers
from backend.routers import budget, bets, analytics, match_budgets, ml_predictions, paper_trading, outcomes

# Import middleware
from backend.middleware.auth import AuthMiddleware

load_dotenv()

# Initialize FastAPI app
app = FastAPI(
    title="TITAN API",
    description="Cricket Betting Intelligence System API",
    version="2.0.0"
)

# Include routers
app.include_router(budget.router)
app.include_router(bets.router)
app.include_router(analytics.router)
app.include_router(match_budgets.router)
app.include_router(ml_predictions.router)
app.include_router(paper_trading.router)
app.include_router(outcomes.router)

# CORS configuration from environment
cors_origins_str = os.getenv('CORS_ORIGINS', 'http://localhost:3000,http://127.0.0.1:3000')
cors_origins = [origin.strip() for origin in cors_origins_str.split(',')]

# Allow all origins only if explicitly set (for development)
if cors_origins_str == '*':
    cors_origins = ['*']

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add authentication middleware (optional - controlled by AUTH_ENABLED env var)
app.add_middleware(AuthMiddleware)

# Redis connection
redis_client = redis.Redis(
    host=os.getenv('REDIS_HOST', 'localhost'),
    port=int(os.getenv('REDIS_PORT', 6379)),
    decode_responses=True
)

# WebSocket connection manager
class ConnectionManager:
    """Manages WebSocket connections"""
    
    def __init__(self):
        self.active_connections: List[WebSocket] = []
    
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WebSocket connected. Total connections: {len(self.active_connections)}")
    
    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)
        logger.info(f"WebSocket disconnected. Total connections: {len(self.active_connections)}")
    
    async def broadcast(self, message: str):
        """Broadcast message to all connected clients"""
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception as e:
                logger.error(f"Error broadcasting to client: {e}")

manager = ConnectionManager()


# REST Endpoints

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "name": "TITAN API",
        "version": "1.0.0",
        "status": "operational",
        "timestamp": datetime.utcnow().isoformat()
    }


@app.get("/api/health")
async def health_check():
    """Health check endpoint with scraper monitoring"""
    try:
        # Check Redis connection
        redis_client.ping()
        redis_healthy = True
    except:
        redis_healthy = False
    
    # Check scraper health
    scraper_status = "down"
    scraper_last_update = None
    
    if redis_healthy:
        try:
            # Check age of active_matches
            matches_json = redis_client.get('active_matches')
            if matches_json:
                matches = json.loads(matches_json)
                if matches and len(matches) > 0:
                    # Check the last_updated timestamp of first match
                    last_updated_str = matches[0].get('last_updated')
                    if last_updated_str:
                        try:
                            last_update = datetime.fromisoformat(last_updated_str.replace('Z', '+00:00'))
                            now = datetime.utcnow()
                            age_seconds = (now - last_update.replace(tzinfo=None)).total_seconds()
                            scraper_last_update = age_seconds
                            
                            if age_seconds < 10:
                                scraper_status = "healthy"
                            elif age_seconds < 30:
                                scraper_status = "stale"
                            else:
                                scraper_status = "down"
                        except:
                            scraper_status = "unknown"
        except Exception as e:
            logger.error(f"Error checking scraper health: {e}")
            scraper_status = "error"
    
    # Check database connection
    db_healthy = False
    try:
        from backend.db.connection import get_db_context
        with get_db_context() as db:
            db.execute(text("SELECT 1"))
            db_healthy = True
    except:
        db_healthy = False
    
    overall_status = "healthy"
    if not redis_healthy or not db_healthy:
        overall_status = "degraded"
    if scraper_status == "down":
        overall_status = "degraded"
    
    return {
        "status": overall_status,
        "redis": "connected" if redis_healthy else "disconnected",
        "database": "connected" if db_healthy else "disconnected",
        "scraper_status": scraper_status,
        "scraper_last_update_seconds": scraper_last_update,
        "timestamp": datetime.utcnow().isoformat()
    }


@app.get("/api/signals/recent")
async def get_recent_signals(limit: int = 10):
    """Get recent signals"""
    try:
        # Get from Redis signal_history
        signals_json = redis_client.lrange('signal_history', 0, limit - 1)
        signals = [json.loads(s) for s in signals_json]
        
        return {
            "count": len(signals),
            "signals": signals
        }
    except Exception as e:
        logger.error(f"Error fetching signals: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/stats")
async def get_stats():
    """Get system statistics"""
    try:
        # Get stats from Redis (would be populated by processor)
        stats_json = redis_client.get('system_stats')
        
        if stats_json:
            stats = json.loads(stats_json)
        else:
            stats = {
                "total_signals": 0,
                "active_matches": 0,
                "uptime_seconds": 0
            }
        
        return stats
    except Exception as e:
        logger.error(f"Error fetching stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/matches/active")
async def get_active_matches():
    """Get list of active matches with enriched data"""
    start_time = time.time()
    try:
        # Get from Redis (populated by scraper)
        matches_json = redis_client.get('active_matches')
        
        if matches_json:
            matches = json.loads(matches_json)
            
            # Enrich each match with additional data
            for match in matches:
                match_id = match.get('match_id')
                
                # Get signal count for this match from recent signals
                signal_count = 0
                try:
                    signals_json = redis_client.lrange('signal_history', 0, 50)
                    for signal_json in signals_json:
                        signal = json.loads(signal_json)
                        if signal.get('match_id') == match_id:
                            signal_count += 1
                except:
                    pass
                
                match['signal_count'] = signal_count
                
                # Calculate odds velocity (change rate)
                # For now, set to 0; could be enhanced with historical comparison
                match['odds_velocity'] = 0.0
                
                # Add match phase calculation (handle None values)
                overs = match.get('overs')
                if overs is not None and overs < 6:
                    match['match_phase'] = 'powerplay'
                elif overs is not None and overs < 16:
                    match['match_phase'] = 'middle'
                elif overs is not None:
                    match['match_phase'] = 'death'
                else:
                    match['match_phase'] = 'unknown'
                
                # Calculate momentum (simplified, handle None values)
                run_rate = match.get('run_rate')
                if run_rate is not None and run_rate > 10:
                    match['momentum'] = min(100, 50 + (run_rate - 10) * 5)
                elif run_rate is not None and run_rate < 6:
                    match['momentum'] = max(0, 50 - (6 - run_rate) * 5)
                else:
                    match['momentum'] = 50
        else:
            matches = []
        
        # Stage 5 Data Validation: Validate API response
        response_data = {
            "status": "success",
            "count": len(matches),
            "matches": matches
        }
        
        # Get original redis data for comparison
        redis_matches = json.loads(matches_json) if matches_json else []
        
        validation_result = api_validator.validate(
            response_data,
            context={'redis_data': redis_matches}
        )
        
        if not validation_result.is_valid():
            logger.warning(f"[STAGE-5] API response validation failed: {validation_result}")
            if validation_result.details.get('issues'):
                for issue in validation_result.details['issues'][:2]:
                    logger.warning(f"  - {issue}")
        else:
            # Track validated response with fingerprint
            if matches:
                first_match = matches[0]
                fingerprint = track_data(
                    stage=5,
                    component="backend_api",
                    data={
                        'match_id': first_match.get('match_id'),
                        'back_price': list(first_match.get('current_odds', {}).values())[0] if first_match.get('current_odds') else None,
                        'timestamp': first_match.get('last_updated')
                    },
                    validation_result=validation_result
                )
                logger.debug(f"[STAGE-5] API response validated [FP:{fingerprint}]")
        
        # Stage 4 Flow Logging: Serving API response
        latency_ms = (time.time() - start_time) * 1000
        flow_log.stage4_serving(
            match_count=len(matches),
            latency_ms=round(latency_ms, 2)
        )
        
        return response_data
    except Exception as e:
        logger.error(f"[STAGE-5] Error fetching matches: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/validation/summary")
async def get_validation_stats():
    """Get data validation summary across all pipeline stages"""
    try:
        summary = get_validation_summary()
        
        return {
            "status": "success",
            "validation_summary": summary,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        logger.error(f"Error fetching validation summary: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/matches/{match_id}/odds-history")
async def get_match_odds_history(match_id: str, limit: int = 50):
    """Get historical odds data for a specific match"""
    try:
        from backend.db.connection import get_db_connection
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Query last N odds data points for this match
        cursor.execute("""
            SELECT 
                time,
                team,
                back_price,
                lay_price,
                odds,
                score,
                wickets,
                overs
            FROM market_ticks
            WHERE match_id = %s
            ORDER BY time DESC
            LIMIT %s
        """, (match_id, limit))
        
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        
        # Format data for chart display
        history = []
        for row in rows:
            history.append({
                'timestamp': row[0].isoformat() if row[0] else None,
                'team': row[1],
                'back_price': float(row[2]) if row[2] else None,
                'lay_price': float(row[3]) if row[3] else None,
                'odds': float(row[4]) if row[4] else None,
                'score': row[5],
                'wickets': row[6],
                'overs': float(row[7]) if row[7] else None
            })
        
        # Reverse to get chronological order
        history.reverse()
        
        return {
            "status": "success",
            "match_id": match_id,
            "count": len(history),
            "history": history
        }
    except Exception as e:
        logger.error(f"Error fetching odds history for {match_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# WebSocket Endpoint

@app.websocket("/ws/signals")
async def websocket_signals(websocket: WebSocket):
    """
    WebSocket endpoint for real-time signal streaming
    
    Clients connect here to receive live signals as they are generated
    """
    await manager.connect(websocket)
    
    # Try to subscribe to Redis signals channel (graceful degradation if Redis unavailable)
    pubsub = None
    redis_available = False
    try:
        pubsub = redis_client.pubsub()
        pubsub.subscribe('signals')
        redis_available = True
        logger.info("WebSocket connected to Redis pub/sub")
    except Exception as e:
        logger.warning(f"Redis not available for WebSocket: {e}. Connection will remain open for future signals.")
        redis_available = False
    
    try:
        # Send welcome message
        await websocket.send_json({
            "type": "connection",
            "message": "Connected to TITAN signal stream" + (" (Redis unavailable - waiting for signals)" if not redis_available else ""),
            "timestamp": datetime.utcnow().isoformat()
        })
        
        # Listen for signals and forward to client
        while True:
            # Check for Redis messages (only if Redis is available)
            if redis_available and pubsub:
                try:
                    message = pubsub.get_message()
                    
                    if message and message['type'] == 'message':
                        try:
                            signal_data = json.loads(message['data'])
                            
                            # Send signal to client
                            await websocket.send_json({
                                "type": "signal",
                                "data": signal_data,
                                "timestamp": datetime.utcnow().isoformat()
                            })
                            
                            logger.info(f"Signal forwarded to WebSocket client")
                        
                        except Exception as e:
                            logger.error(f"Error processing signal: {e}")
                except Exception as e:
                    logger.error(f"Error reading from Redis: {e}")
                    redis_available = False
            
            # Small delay to prevent CPU spinning
            import asyncio
            await asyncio.sleep(0.1 if not redis_available else 0.01)
    
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        if pubsub:
            try:
                pubsub.unsubscribe('signals')
            except:
                pass
        logger.info("WebSocket client disconnected")
    
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket)
        pubsub.unsubscribe('signals')


# Startup/Shutdown Events

@app.on_event("startup")
async def startup_event():
    """Run on application startup"""
    logger.info("🚀 TITAN Backend API starting...")
    logger.info(f"Redis: {os.getenv('REDIS_HOST')}:{os.getenv('REDIS_PORT')}")
    
    try:
        redis_client.ping()
        logger.info("✅ Redis connection established")
    except Exception as e:
        logger.error(f"❌ Redis connection failed: {e}")
    
    # Initialize database
    try:
        from backend.db.connection import test_connection
        from backend.db.init_schema import initialize_database, seed_default_budget
        
        if test_connection():
            logger.info("✅ Database connection established")
            # Initialize schema if needed
            initialize_database()
            seed_default_budget()
        else:
            logger.warning("⚠️  Database connection failed")
    except Exception as e:
        logger.warning(f"⚠️  Database initialization warning: {e}")


@app.on_event("shutdown")
async def shutdown_event():
    """Run on application shutdown"""
    logger.info("TITAN Backend API shutting down...")


if __name__ == "__main__":
    import uvicorn
    
    port = int(os.getenv('API_PORT', 8000))
    reload = os.getenv('API_RELOAD', 'true').lower() == 'true'
    
    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=port,
        reload=reload,
        log_level="info"
    )

