"""FastAPI entry point for the billing anomaly detection service."""

from fastapi import FastAPI

from app.config import settings
from app.routers import cache, customers, fee_rules, reconciliations, transactions


app = FastAPI(title=settings.app_name, version=settings.app_version)
app.include_router(transactions.router)
app.include_router(customers.router)
app.include_router(fee_rules.router)
app.include_router(cache.router)
app.include_router(reconciliations.router)


@app.get("/health", tags=["system"])
def health_check() -> dict[str, str]:
    """Provide a dependency-free readiness check for local development."""
    return {"status": "ok"}
