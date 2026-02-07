"""
PHOENIX Configuration Management
Typed configuration via Pydantic Settings. All services load config from here.

Usage:
    from shared.config import get_settings
    settings = get_settings()
    print(settings.database.url)
"""

from functools import lru_cache
from pydantic_settings import BaseSettings
from pydantic import Field


class DatabaseConfig(BaseSettings):
    """TimescaleDB connection settings."""

    host: str = "localhost"
    port: int = 5432
    name: str = "phoenix_betting"
    user: str = "phoenix"
    password: str = "phoenix_secure_2026"

    @property
    def url(self) -> str:
        return f"postgresql+asyncpg://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"

    @property
    def sync_url(self) -> str:
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"

    model_config = {"env_prefix": "POSTGRES_"}


class RedisConfig(BaseSettings):
    """Redis connection settings."""

    host: str = "localhost"
    port: int = 6379
    db: int = 0

    @property
    def url(self) -> str:
        return f"redis://{self.host}:{self.port}/{self.db}"

    model_config = {"env_prefix": "REDIS_"}


class ScraperConfig(BaseSettings):
    """LotusBook scraper settings."""

    betting_site_url: str = "https://lotusbook.site/cricket"
    headless: bool = True
    poll_interval: int = 3
    max_retries: int = 3
    session_timeout: int = 30000
    international_only: bool = True  # Filter for international + major franchise matches only

    model_config = {"env_prefix": "SCRAPER_"}


class RLConfig(BaseSettings):
    """Reinforcement Learning agent settings."""

    training_mode: str = "offline"  # offline | online | eval
    model_path: str = "models/best_model.zip"
    starting_bankroll: int = 100000
    graduation_enabled: bool = True
    learning_rate: float = 3e-4
    total_timesteps: int = 500000
    observation_size: int = 66
    n_steps: int = 2048
    batch_size: int = 64
    min_matches_to_train: int = 30  # Minimum completed matches before training starts
    nightly_retrain_steps: int = 50000  # Incremental training steps per nightly run
    auto_advance_curriculum: bool = True  # Auto-advance curriculum stages
    data_lookback_days: int = 90  # How far back to load training data
    min_ticks_per_match: int = 50  # Minimum odds ticks for a match to be usable

    model_config = {"env_prefix": "RL_"}


class APIConfig(BaseSettings):
    """Backend API settings."""

    port: int = 8000
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    auth_enabled: bool = False
    api_key: str = "change_me_in_production"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",")]

    model_config = {"env_prefix": "API_"}


class LogConfig(BaseSettings):
    """Logging settings."""

    level: str = "INFO"
    format: str = "json"  # json | console

    model_config = {"env_prefix": "LOG_"}


class Settings:
    """Aggregated settings for all PHOENIX components."""

    def __init__(self) -> None:
        self.database = DatabaseConfig()
        self.redis = RedisConfig()
        self.scraper = ScraperConfig()
        self.rl = RLConfig()
        self.api = APIConfig()
        self.log = LogConfig()


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance. Call once at startup."""
    return Settings()
