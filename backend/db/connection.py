"""
Database Connection Manager
SQLAlchemy connection to TimescaleDB
"""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from contextlib import contextmanager
import os
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

# Database connection string
DATABASE_URL = os.getenv(
    'DATABASE_URL',
    'postgresql://titan:titan_secure_2025@localhost:5432/titan_betting'
)

# Create engine
engine = create_engine(
    DATABASE_URL,
    pool_size=20,
    max_overflow=40,
    pool_pre_ping=True,  # Verify connections before using
    echo=False  # Set to True for SQL debugging
)

# Session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Session:
    """
    Get database session for FastAPI dependency injection
    
    Usage in FastAPI:
        @app.get("/endpoint")
        async def endpoint(db: Session = Depends(get_db)):
            ...
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def get_db_context():
    """
    Get database session for use in context manager
    
    Usage:
        with get_db_context() as db:
            result = db.query(Model).all()
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Database error: {e}")
        raise
    finally:
        db.close()


def get_db_connection():
    """
    Get raw psycopg2 connection for cursor-based operations
    
    Usage:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM table")
        results = cursor.fetchall()
        cursor.close()
        conn.close()
    """
    return engine.raw_connection()


def test_connection():
    """Test database connection"""
    try:
        with get_db_context() as db:
            db.execute(text("SELECT 1"))
        logger.info("✅ Database connection successful")
        return True
    except Exception as e:
        logger.error(f"❌ Database connection failed: {e}")
        return False

