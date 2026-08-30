"""Small, failure-tolerant Redis JSON cache wrapper."""

import json
from typing import Any

from redis import Redis
from redis.exceptions import RedisError

from app.config import settings


# The short timeouts make a missing local Redis service a quick cache miss,
# rather than allowing it to delay otherwise healthy database responses.
redis_client = Redis.from_url(
    settings.redis_url,
    decode_responses=True,
    socket_connect_timeout=0.2,
    socket_timeout=0.2,
)


def get_cached_json(key: str) -> Any | None:
    """Return decoded JSON on a Redis hit, or None for misses and outages."""
    try:
        value = redis_client.get(key)
        return json.loads(value) if value is not None else None
    except (RedisError, json.JSONDecodeError):
        return None


def set_cached_json(key: str, value: Any, ttl_seconds: int) -> bool:
    """Store JSON with a TTL; report failure so callers still serve the DB result."""
    try:
        redis_client.setex(key, ttl_seconds, json.dumps(value))
        return True
    except RedisError:
        return False


def delete_cached_key(key: str) -> bool:
    """Delete one cache key and distinguish a removed value from a cache outage."""
    try:
        return bool(redis_client.delete(key))
    except RedisError:
        return False


def invalidate_customer_reads(customer_id: int) -> None:
    """Remove derived data changed by transaction ingestion immediately."""
    delete_cached_key(f"customer:{customer_id}:metrics")
    delete_cached_key(f"customer:{customer_id}:anomalies")
