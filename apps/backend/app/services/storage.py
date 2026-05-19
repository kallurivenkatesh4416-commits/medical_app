"""StorageGateway abstraction (brief §2.3, PLAN.md provider abstraction).

Business logic depends only on the Protocol; the concrete backend is chosen
by PROVIDER_MODE.

- stub  -> local filesystem, blobs encrypted at rest (Fernet/AES) so even the
           dev store never holds plaintext PHI. Signed URLs are short-lived,
           HMAC-signed (JWT) capability links served by the backend.
- live  -> S3 with SSE-KMS (falls back to SSE-S3 if no KMS key). Signed URLs
           are native S3 presigned GETs.

Signed-URL TTL is hard-capped at 15 minutes regardless of caller/config
(brief §2.3).
"""

import base64
import hashlib
from pathlib import Path
from typing import Protocol

from cryptography.fernet import Fernet

from app.config import get_settings
from app.security.jwt import create_record_url_token

#: Brief §2.3: signed URLs must never live longer than 15 minutes.
MAX_SIGNED_URL_TTL = 900


def signed_url_ttl_seconds() -> int:
    return max(1, min(get_settings().s3_signed_url_ttl_seconds, MAX_SIGNED_URL_TTL))


def safe_download_name(name: str) -> str:
    cleaned = name.replace("\\", "_").replace("/", "_")
    cleaned = cleaned.replace("\r", "_").replace("\n", "_").replace('"', "'")
    cleaned = cleaned.strip(" .") or "record"
    return cleaned[:160]


class StorageError(RuntimeError):
    pass


class StorageGateway(Protocol):
    def put_encrypted(self, *, key: str, data: bytes, content_type: str) -> None: ...

    def signed_url(self, *, key: str, download_name: str) -> str: ...

    def get_bytes(self, *, key: str) -> bytes: ...


class LocalStorageGateway:
    """Dev/CI stub. Files are Fernet-encrypted at rest; never plaintext PHI."""

    def __init__(self) -> None:
        s = get_settings()
        self._dir = Path(s.local_storage_dir)
        self._base = s.public_base_url.rstrip("/")
        # Derive a stable 32-byte Fernet key from the app secret.
        digest = hashlib.sha256(s.jwt_secret.encode()).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(digest))

    def _path(self, key: str) -> Path:
        # Keys are server-generated UUID hex (no separators) — no traversal.
        if "/" in key or "\\" in key or ".." in key:
            raise StorageError("invalid storage key")
        return self._dir / key

    def put_encrypted(self, *, key: str, data: bytes, content_type: str) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path(key).write_bytes(self._fernet.encrypt(data))

    def get_bytes(self, *, key: str) -> bytes:
        p = self._path(key)
        if not p.exists():
            raise StorageError("object not found")
        return self._fernet.decrypt(p.read_bytes())

    def signed_url(self, *, key: str, download_name: str) -> str:
        token = create_record_url_token(
            storage_key=key,
            download_name=safe_download_name(download_name),
            ttl=signed_url_ttl_seconds(),
        )
        return f"{self._base}/api/v1/records/download/{token}"


class S3StorageGateway:
    """Production. Server-side encrypted (SSE-KMS, else SSE-S3); native
    presigned GETs that expire at the capped TTL."""

    def __init__(self) -> None:
        import boto3  # lazy: only needed in live mode

        s = get_settings()
        self._bucket = s.s3_bucket
        self._kms = s.s3_kms_key_id
        self._client = boto3.client(
            "s3", region_name=s.s3_region, endpoint_url=s.s3_endpoint_url
        )

    def _sse(self) -> dict:
        if self._kms:
            return {"ServerSideEncryption": "aws:kms", "SSEKMSKeyId": self._kms}
        return {"ServerSideEncryption": "AES256"}

    def put_encrypted(self, *, key: str, data: bytes, content_type: str) -> None:
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
            **self._sse(),
        )

    def get_bytes(self, *, key: str) -> bytes:
        raise StorageError("live storage is fetched via presigned URL, not proxied")

    def signed_url(self, *, key: str, download_name: str) -> str:
        name = safe_download_name(download_name)
        return self._client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self._bucket,
                "Key": key,
                "ResponseContentDisposition": f'attachment; filename="{name}"',
            },
            ExpiresIn=signed_url_ttl_seconds(),
        )


def get_storage_gateway() -> StorageGateway:
    if get_settings().provider_mode == "stub":
        return LocalStorageGateway()
    return S3StorageGateway()
