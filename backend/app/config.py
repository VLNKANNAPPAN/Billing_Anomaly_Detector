"""Application settings kept in one place for later phases."""

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    """Configuration that is safe to use before external services are added."""

    app_name: str = "Billing Anomaly Detection API"
    app_version: str = "0.1.0"
    # A local SQLite file makes the first development phases runnable without
    # infrastructure; DATABASE_URL can later point at another SQL database.
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./billing_anomaly.db")
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")


settings = Settings()
