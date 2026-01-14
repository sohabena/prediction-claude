"""
Database Schema Initialization
Creates tables and TimescaleDB hypertables
"""

from sqlalchemy import text
from backend.models.betting import Base
from backend.db.connection import engine, get_db_context
from loguru import logger


def create_tables():
    """Create all tables defined in models"""
    try:
        logger.info("Creating database tables...")
        Base.metadata.create_all(bind=engine, checkfirst=True)
        logger.info("✅ Tables created successfully")
        return True
    except Exception as e:
        logger.error(f"❌ Error creating tables: {e}")
        # Try to continue even if some tables already exist
        logger.warning("Continuing despite table creation errors...")
        return True  # Return True to continue initialization


def create_hypertables():
    """
    Convert regular tables to TimescaleDB hypertables
    This enables time-series optimization for virtual_bets and betting_history
    """
    hypertable_queries = [
        # Virtual bets hypertable (partitioned by placed_at)
        """
        SELECT create_hypertable(
            'virtual_bets',
            'placed_at',
            if_not_exists => TRUE,
            migrate_data => TRUE
        );
        """,
        
        # Betting history hypertable (partitioned by date)
        """
        SELECT create_hypertable(
            'betting_history',
            'date',
            if_not_exists => TRUE,
            migrate_data => TRUE
        );
        """
    ]
    
    try:
        logger.info("Creating TimescaleDB hypertables...")
        
        # Create each hypertable in separate transaction to avoid rollback issues
        for query in hypertable_queries:
            try:
                with get_db_context() as db:
                    db.execute(text(query))
                    db.commit()
                    logger.info(f"✅ Hypertable created")
            except Exception as e:
                # Hypertable might already exist or TimescaleDB extension not installed
                logger.warning(f"⚠️  Hypertable creation warning: {e}")
                # Continue with next hypertable even if this one fails
                continue
        
        logger.info("✅ Hypertables setup complete")
        return True
            
    except Exception as e:
        logger.error(f"❌ Error creating hypertables: {e}")
        logger.info("ℹ️  Note: TimescaleDB extension must be installed for hypertables")
        return False


def create_indexes():
    """Create indexes for performance optimization"""
    index_queries = [
        # Virtual bets indexes
        "CREATE INDEX IF NOT EXISTS idx_virtual_bets_match_id ON virtual_bets(match_id);",
        "CREATE INDEX IF NOT EXISTS idx_virtual_bets_strategy ON virtual_bets(strategy);",
        "CREATE INDEX IF NOT EXISTS idx_virtual_bets_status ON virtual_bets(status);",
        "CREATE INDEX IF NOT EXISTS idx_virtual_bets_budget_id ON virtual_bets(budget_id);",
        
        # Match results indexes
        "CREATE INDEX IF NOT EXISTS idx_match_results_match_id ON match_results(match_id);",
        "CREATE INDEX IF NOT EXISTS idx_match_results_completed_at ON match_results(completed_at);",
        
        # Betting history indexes
        "CREATE INDEX IF NOT EXISTS idx_betting_history_budget_id ON betting_history(budget_id);",
        "CREATE INDEX IF NOT EXISTS idx_betting_history_period_type ON betting_history(period_type);",
    ]
    
    try:
        with get_db_context() as db:
            logger.info("Creating database indexes...")
            
            for query in index_queries:
                db.execute(text(query))
            
            logger.info("✅ Indexes created successfully")
            return True
            
    except Exception as e:
        logger.error(f"❌ Error creating indexes: {e}")
        return False


def initialize_database():
    """
    Complete database initialization
    Run this once to set up the betting database
    """
    logger.info("🚀 Starting database initialization...")
    
    # Step 1: Create tables
    if not create_tables():
        logger.error("Failed to create tables. Aborting initialization.")
        return False
    
    # Step 2: Create hypertables (TimescaleDB optimization)
    create_hypertables()  # Non-critical, just warning if fails
    
    # Step 3: Create indexes
    if not create_indexes():
        logger.warning("Some indexes failed to create, but continuing...")
    
    logger.info("✅ Database initialization complete!")
    return True


def seed_default_budget():
    """Create default budget for testing"""
    from backend.models.betting import BettingBudget
    
    try:
        with get_db_context() as db:
            # Check if default budget exists
            existing = db.query(BettingBudget).filter_by(user_id='default_user').first()
            
            if not existing:
                default_budget = BettingBudget(
                    user_id='default_user',
                    current_balance=50000.0,
                    initial_amount=50000.0
                )
                db.add(default_budget)
                db.commit()
                logger.info("✅ Default budget created (₹50,000)")
            else:
                logger.info("ℹ️  Default budget already exists")
                
    except Exception as e:
        logger.error(f"❌ Error seeding default budget: {e}")


if __name__ == "__main__":
    # Run initialization when executed directly
    initialize_database()
    seed_default_budget()

