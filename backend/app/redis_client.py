"""Shared Redis client plus small helpers (rate limiting, pub/sub notifications)."""
from __future__ import annotations

import time

import orjson
import redis

from .config import get_settings

_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(get_settings().redis_url, decode_responses=True)
    return _client


def rate_limit(key: str, limit: int, window_s: int) -> tuple[bool, int]:
    """Fixed-window counter. Returns (allowed, retry_after_seconds)."""
    r = get_redis()
    bucket = int(time.time() // window_s)
    k = f"rl:{key}:{bucket}"
    pipe = r.pipeline()
    pipe.incr(k)
    pipe.expire(k, window_s + 1)
    count, _ = pipe.execute()
    if count > limit:
        return False, window_s - int(time.time() % window_s)
    return True, 0


def publish(channel: str, payload: dict) -> None:
    try:
        get_redis().publish(channel, orjson.dumps(payload).decode())
    except redis.RedisError:
        pass  # notifications are best effort; durable state lives in Postgres


def wake_workers() -> None:
    publish("sah:jobs", {"event": "wake"})
