"""
Migration: Add match-specific budget support
Adds match_id, match_name, and is_active columns to betting_budgets table
"""

from sqlalchemy import text
from backend.db.connection import get_db_context
from loguru import logger


def migrate():
    """Add columns for match-specific budgets"""
    try:
        with get_db_context() as db:
            logger.info("🔄 Starting migration: match-specific budgets...")
            
            # Add new columns
            migrations = [
                "ALTER TABLE betting_budgets ADD COLUMN IF NOT EXISTS match_id VARCHAR(100);",
                "ALTER TABLE betting_budgets ADD COLUMN IF NOT EXISTS match_name VARCHAR(200);",
                "ALTER TABLE betting_budgets ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE;",
                
                # Drop old unique constraint on user_id
                "ALTER TABLE betting_budgets DROP CONSTRAINT IF EXISTS betting_budgets_user_id_key;",
                
                # Create composite unique constraint (user_id + match_id)
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint 
                        WHERE conname = 'uq_user_match'
                    ) THEN
                        ALTER TABLE betting_budgets 
                        ADD CONSTRAINT uq_user_match UNIQUE (user_id, match_id);
                    END IF;
                END $$;
                """,
                
                # Create index on match_id
                "CREATE INDEX IF NOT EXISTS idx_betting_budgets_match_id ON betting_budgets(match_id);",
                "CREATE INDEX IF NOT EXISTS idx_betting_budgets_is_active ON betting_budgets(is_active);"
            ]
            
            for migration_sql in migrations:
                try:
                    db.execute(text(migration_sql))
                    logger.info(f"✅ Executed: {migration_sql[:60]}...")
                except Exception as e:
                    logger.warning(f"⚠️  Migration warning: {e}")
            
            db.commit()
            logger.info("✅ Migration completed successfully!")
            
    except Exception as e:
        logger.error(f"❌ Migration failed: {e}")
        raise


if __name__ == "__main__":
    migrate()

