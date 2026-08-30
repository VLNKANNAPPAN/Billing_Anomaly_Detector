"""Manual cache invalidation endpoint used for demos and cache testing."""

from fastapi import APIRouter

from app.cache.redis_client import delete_cached_key


router = APIRouter(prefix="/cache", tags=["cache"])


@router.delete("/{key}")
def delete_cache_key(key: str) -> dict[str, object]:
    """Remove one explicit Redis key without exposing broader cache commands."""
    return {"key": key, "deleted": delete_cached_key(key)}
