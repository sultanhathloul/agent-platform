"""Uploaded bytes in MinIO, bucket `uploads` (README §2, ADR-0129). The gateway writes an
upload; the ingest service reads it; the orphan sweeper removes an object no attachment
record names (owner decision S4). MinIO is internal to the compose network; no browser
reaches it."""

import asyncio
import io
from datetime import UTC, datetime, timedelta

from minio import Minio
from minio.error import S3Error


class StorageUnavailable(RuntimeError):
    pass


class ObjectStore:
    def __init__(self, endpoint: str, access_key: str, secret_key: str, bucket: str, *, secure: bool = False):
        self._client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
        self.bucket = bucket

    def _get(self, key: str) -> bytes:
        response = self._client.get_object(self.bucket, key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    async def get(self, key: str) -> bytes:
        try:
            return await asyncio.to_thread(self._get, key)
        except Exception as exc:  # S3Error, urllib3 errors: the object cannot be read now
            raise StorageUnavailable(type(exc).__name__) from exc

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        """Tests and tools only; uploads arrive through the gateway."""
        await asyncio.to_thread(
            self._client.put_object, self.bucket, key, io.BytesIO(data), len(data), content_type=content_type
        )

    async def delete(self, key: str) -> bool:
        """Idempotent: an object already gone counts as deleted."""
        try:
            await asyncio.to_thread(self._client.remove_object, self.bucket, key)
            return True
        except S3Error as exc:
            if exc.code == "NoSuchKey":
                return True
            raise StorageUnavailable(exc.code) from exc
        except Exception as exc:
            raise StorageUnavailable(type(exc).__name__) from exc

    def _older_than(self, age: timedelta, prefix: str) -> list[str]:
        cutoff = datetime.now(UTC) - age
        return [
            item.object_name
            for item in self._client.list_objects(self.bucket, prefix=prefix or None, recursive=True)
            if item.last_modified is not None and item.last_modified < cutoff
        ]

    async def keys_older_than(self, age: timedelta, prefix: str = "") -> list[str]:
        """Keys of the objects stored longer ago than `age`, under `prefix` (the orphan sweeper)."""
        try:
            return await asyncio.to_thread(self._older_than, age, prefix)
        except Exception as exc:
            raise StorageUnavailable(type(exc).__name__) from exc

    async def exists(self, key: str) -> bool:
        try:
            await asyncio.to_thread(self._client.stat_object, self.bucket, key)
            return True
        except S3Error as exc:
            if exc.code in ("NoSuchKey", "NoSuchObject"):
                return False
            raise StorageUnavailable(exc.code) from exc
