"""
TITAN Authentication Middleware
Provides optional API key authentication for production use
"""

import os
from fastapi import Request, HTTPException
from fastapi.security import APIKeyHeader
from starlette.middleware.base import BaseHTTPMiddleware
from loguru import logger

# API Key header name
API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)


class AuthMiddleware(BaseHTTPMiddleware):
    """
    Simple API Key authentication middleware
    
    Enable by setting AUTH_ENABLED=true in environment
    Set the API key with API_KEY environment variable
    
    Excluded paths (no auth required):
    - /docs, /redoc, /openapi.json (API docs)
    - /api/health (health check)
    - / (root endpoint)
    - WebSocket endpoints
    """
    
    # Paths that don't require authentication
    EXCLUDED_PATHS = {
        "/",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/api/health",
    }
    
    # Prefixes that don't require authentication
    EXCLUDED_PREFIXES = [
        "/ws/",  # WebSocket endpoints
    ]
    
    def __init__(self, app, api_key: str = None):
        super().__init__(app)
        self.api_key = api_key or os.getenv("API_KEY", "")
        self.enabled = os.getenv("AUTH_ENABLED", "false").lower() == "true"
        
        if self.enabled:
            if not self.api_key:
                logger.warning("⚠️ AUTH_ENABLED=true but no API_KEY set!")
            else:
                logger.info("🔐 API Key authentication enabled")
        else:
            logger.info("ℹ️ Authentication disabled (AUTH_ENABLED=false)")
    
    async def dispatch(self, request: Request, call_next):
        """Check API key for protected endpoints"""
        
        # Skip if auth is disabled
        if not self.enabled:
            return await call_next(request)
        
        # Check if path is excluded
        path = request.url.path
        
        if path in self.EXCLUDED_PATHS:
            return await call_next(request)
        
        for prefix in self.EXCLUDED_PREFIXES:
            if path.startswith(prefix):
                return await call_next(request)
        
        # Check API key
        api_key = request.headers.get("X-API-Key")
        
        if not api_key:
            logger.warning(f"Missing API key for request: {request.method} {path}")
            raise HTTPException(
                status_code=401,
                detail="Missing API key. Provide X-API-Key header."
            )
        
        if api_key != self.api_key:
            logger.warning(f"Invalid API key for request: {request.method} {path}")
            raise HTTPException(
                status_code=403,
                detail="Invalid API key."
            )
        
        return await call_next(request)


def verify_api_key(api_key: str = None) -> bool:
    """
    Verify an API key (for use in individual endpoints)
    
    Usage:
        from backend.middleware.auth import verify_api_key
        
        @app.get("/protected")
        async def protected_endpoint(api_key: str = Depends(API_KEY_HEADER)):
            if not verify_api_key(api_key):
                raise HTTPException(status_code=403, detail="Invalid API key")
            return {"message": "Access granted"}
    """
    expected_key = os.getenv("API_KEY", "")
    
    if not expected_key:
        # If no key is configured, deny access
        return False
    
    return api_key == expected_key
