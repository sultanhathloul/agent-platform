"""The judge's verdict cache in Redis (I4): plain string values with a time to live."""

from typing import Any


class RedisVerdictCache:
    def __init__(self, redis: Any):
        self._redis = redis

    async def get(self, key: str) -> str | None:
        value = await self._redis.get(key)
        return value.decode() if isinstance(value, bytes) else value

    async def set(self, key: str, value: str, ttl_s: int) -> None:
        await self._redis.set(key, value, ex=ttl_s)
