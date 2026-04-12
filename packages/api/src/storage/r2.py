"""Cloudflare R2 storage adapter.

R2 is S3-API compatible, so we use boto3 with a custom endpoint_url.
All operations are wrapped in asyncio.to_thread since boto3 is synchronous.

Config is read from environment variables at adapter construction time:
  R2_ACCOUNT_ID
  R2_ACCESS_KEY_ID
  R2_SECRET_ACCESS_KEY
  R2_BUCKET
  R2_ENDPOINT_URL

For tests, `moto` is used to mock the S3 API with zero network calls.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

try:
    import boto3
    from botocore.config import Config as BotoConfig
    from botocore.exceptions import ClientError
except ImportError:  # pragma: no cover
    boto3 = None  # type: ignore
    BotoConfig = None  # type: ignore
    ClientError = Exception  # type: ignore

from src.common.models import utcnow
from src.storage.base import StorageAdapter, compute_sha256
from src.storage.models import (
    ObjectNotFound,
    ObjectRef,
    SignedUrl,
    StorageConfigError,
    StorageError,
    StoredObject,
)


class CloudflareR2Adapter(StorageAdapter):
    """S3-compatible adapter targeting Cloudflare R2."""

    backend_name = "r2"

    def __init__(
        self,
        bucket: str,
        account_id: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        endpoint_url: str | None = None,
        region: str = "auto",
        client: Any = None,  # Injectable for tests
    ):
        if boto3 is None:
            raise StorageConfigError(
                "boto3 is not installed. Install with: pip install boto3"
            )

        self.bucket = bucket
        self._account_id = account_id

        if client is not None:
            # Tests inject a pre-configured (moto) client
            self._client = client
        else:
            if not (access_key_id and secret_access_key):
                raise StorageConfigError(
                    "R2 adapter requires access_key_id and secret_access_key"
                )
            if not endpoint_url:
                if not account_id:
                    raise StorageConfigError(
                        "R2 adapter requires either endpoint_url or account_id"
                    )
                endpoint_url = f"https://{account_id}.r2.cloudflarestorage.com"

            self._client = boto3.client(
                "s3",
                endpoint_url=endpoint_url,
                aws_access_key_id=access_key_id,
                aws_secret_access_key=secret_access_key,
                region_name=region,
                config=BotoConfig(
                    signature_version="s3v4",
                    retries={"max_attempts": 3, "mode": "standard"},
                ),
            )

    # ---- core operations ----

    async def put(
        self,
        tenant_id: str,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> ObjectRef:
        self._check_tenant(key, tenant_id)
        content_hash = compute_sha256(data)

        def _put():
            try:
                self._client.put_object(
                    Bucket=self.bucket,
                    Key=key,
                    Body=data,
                    ContentType=content_type,
                    Metadata={"content-sha256": content_hash, "tenant-id": tenant_id},
                )
            except ClientError as exc:
                raise StorageError(f"R2 put failed for {key}: {exc}") from exc

        await asyncio.to_thread(_put)

        return ObjectRef(
            backend="r2",
            bucket=self.bucket,
            key=key,
            size_bytes=len(data),
            content_hash=content_hash,
            content_type=content_type,
        )

    async def get(self, tenant_id: str, key: str) -> StoredObject:
        self._check_tenant(key, tenant_id)

        def _get() -> tuple[bytes, str]:
            try:
                resp = self._client.get_object(Bucket=self.bucket, Key=key)
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code in ("NoSuchKey", "404"):
                    raise ObjectNotFound(key, "r2") from exc
                raise StorageError(f"R2 get failed for {key}: {exc}") from exc
            body = resp["Body"].read()
            ctype = resp.get("ContentType", "application/octet-stream")
            return body, ctype

        data, content_type = await asyncio.to_thread(_get)
        ref = ObjectRef(
            backend="r2",
            bucket=self.bucket,
            key=key,
            size_bytes=len(data),
            content_hash=compute_sha256(data),
            content_type=content_type,
        )
        return StoredObject(ref=ref, data=data)

    async def delete(self, tenant_id: str, key: str) -> bool:
        self._check_tenant(key, tenant_id)

        def _delete() -> bool:
            try:
                # HEAD first to determine if it existed
                self._client.head_object(Bucket=self.bucket, Key=key)
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code in ("NoSuchKey", "404", "NotFound"):
                    return False
                raise StorageError(f"R2 head failed for {key}: {exc}") from exc
            try:
                self._client.delete_object(Bucket=self.bucket, Key=key)
                return True
            except ClientError as exc:
                raise StorageError(f"R2 delete failed for {key}: {exc}") from exc

        return await asyncio.to_thread(_delete)

    async def exists(self, tenant_id: str, key: str) -> bool:
        self._check_tenant(key, tenant_id)

        def _exists() -> bool:
            try:
                self._client.head_object(Bucket=self.bucket, Key=key)
                return True
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code in ("NoSuchKey", "404", "NotFound"):
                    return False
                raise StorageError(f"R2 head failed for {key}: {exc}") from exc

        return await asyncio.to_thread(_exists)

    async def list_keys(self, tenant_id: str, prefix: str) -> list[str]:
        self._check_tenant(prefix, tenant_id)

        def _list() -> list[str]:
            keys: list[str] = []
            paginator = self._client.get_paginator("list_objects_v2")
            try:
                for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                    for obj in page.get("Contents", []):
                        keys.append(obj["Key"])
            except ClientError as exc:
                raise StorageError(f"R2 list failed for {prefix}: {exc}") from exc
            return sorted(keys)

        return await asyncio.to_thread(_list)

    async def sign_url(
        self,
        tenant_id: str,
        key: str,
        method: str = "GET",
        expires_in: timedelta = timedelta(minutes=15),
        content_type: str | None = None,
    ) -> SignedUrl:
        self._check_tenant(key, tenant_id)
        if method not in ("GET", "PUT"):
            raise ValueError(f"Unsupported method for signed URL: {method}")

        op = "get_object" if method == "GET" else "put_object"
        params: dict[str, Any] = {"Bucket": self.bucket, "Key": key}
        headers: dict[str, str] = {}
        if method == "PUT" and content_type:
            params["ContentType"] = content_type
            headers["Content-Type"] = content_type

        def _sign() -> str:
            try:
                return self._client.generate_presigned_url(
                    op,
                    Params=params,
                    ExpiresIn=int(expires_in.total_seconds()),
                    HttpMethod=method,
                )
            except ClientError as exc:
                raise StorageError(f"R2 sign_url failed for {key}: {exc}") from exc

        url = await asyncio.to_thread(_sign)
        expires_at = utcnow() + expires_in
        return SignedUrl(url=url, method=method, expires_at=expires_at, headers=headers)
