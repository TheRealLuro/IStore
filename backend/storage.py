from io import BytesIO
from typing import BinaryIO, Iterable

from minio import Minio
from minio.error import S3Error
from minio.sse import SseKMS, SseS3

from backend.config import settings


class Storage:
    def __init__(self) -> None:
        self.client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
        self.bucket_originals = settings.minio_bucket_originals
        self.bucket_served = settings.minio_bucket_served
        self.bucket_faces = settings.minio_bucket_faces
        self.bucket_quarantine = settings.minio_bucket_quarantine
        # C8.2 — checkpoint storage for fine-tuned models. Objects are
        # `.pkl` / `.safetensors` files written by the trainer when a
        # fine-tune (D6) finishes. The `model_runs` row points at the
        # object key so the admin Models tab can offer a download.
        self.bucket_models = getattr(settings, "minio_bucket_models", "neuthek-models")
        # VLT-8 — zero-knowledge vault ciphertext. Its own bucket so E2E
        # blobs never share space with AI-processed originals/served.
        self.bucket_vault = getattr(settings, "minio_bucket_vault", "neuthek-vault")

    def _bucket_names(self) -> Iterable[str]:
        return (
            self.bucket_originals,
            self.bucket_served,
            self.bucket_faces,
            self.bucket_quarantine,
            self.bucket_models,
            self.bucket_vault,
        )

    def ensure_buckets(self) -> None:
        for name in self._bucket_names():
            if not self.client.bucket_exists(name):
                self.client.make_bucket(name)

    def _sse(self, scope: str = "content"):
        if settings.minio_sse_mode == "sse-s3":
            return SseS3()
        if settings.minio_sse_mode == "sse-kms":
            key_id = (
                settings.minio_sse_kms_key_id_biometric
                if scope == "biometric"
                else settings.minio_sse_kms_key_id_content
            )
            return SseKMS(key_id) if key_id else SseS3()
        return None

    def put(
        self,
        bucket: str,
        key: str,
        data: bytes,
        content_type: str,
        *,
        sse_scope: str = "content",
    ) -> None:
        self.client.put_object(
            bucket,
            key,
            BytesIO(data),
            length=len(data),
            content_type=content_type,
            sse=self._sse(sse_scope),
        )

    def put_stream(
        self,
        bucket: str,
        key: str,
        stream: BinaryIO,
        length: int,
        content_type: str,
        *,
        sse_scope: str = "content",
        part_size: int = 16 * 1024 * 1024,
    ) -> None:
        """Stream a file-like object straight to object storage without
        buffering the whole payload in a Python `bytes`. Used for vault
        file uploads (potentially multi-GB ciphertext): the request body
        is spooled by Starlette to a temp file, and we hand that file
        handle to MinIO's multipart uploader. `length` must be the exact
        byte length of `stream` (we seek to size it before calling)."""
        self.client.put_object(
            bucket,
            key,
            stream,
            length=length,
            content_type=content_type,
            sse=self._sse(sse_scope),
            part_size=part_size,
        )

    def get(self, bucket: str, key: str) -> bytes:
        resp = self.client.get_object(bucket, key)
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()

    def stat(self, bucket: str, key: str) -> int:
        """Return the byte length of an object without downloading it.
        Used by the video / audio stream endpoint to size the
        `Content-Length` and `Content-Range` headers when serving
        partial bytes via HTTP Range — the size header has to land
        in the response even on a 206, so we need it before reading.
        """
        info = self.client.stat_object(bucket, key)
        return int(info.size)

    def get_range(
        self, bucket: str, key: str, offset: int, length: int,
    ) -> bytes:
        """Read a byte range from an object. Used by the video / audio
        stream endpoint to answer HTTP Range requests without loading
        the entire blob into memory — a 1-MB seek window on a 4-GB
        movie should be a 1-MB read, not a 4-GB one.

        `offset` is the first byte to read; `length` is how many bytes
        to read past it. MinIO's `get_object(offset=, length=)` maps
        directly to S3's `Range: bytes=<offset>-<offset+length-1>`.
        """
        if length <= 0:
            return b""
        resp = self.client.get_object(
            bucket, key, offset=offset, length=length,
        )
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()

    def delete(self, bucket: str, key: str) -> None:
        try:
            self.client.remove_object(bucket, key)
        except S3Error:
            pass


storage = Storage()
