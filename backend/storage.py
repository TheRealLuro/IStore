from io import BytesIO
from typing import Iterable

from minio import Minio
from minio.error import S3Error

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

    def _bucket_names(self) -> Iterable[str]:
        return (self.bucket_originals, self.bucket_served, self.bucket_faces)

    def ensure_buckets(self) -> None:
        for name in self._bucket_names():
            if not self.client.bucket_exists(name):
                self.client.make_bucket(name)

    def put(self, bucket: str, key: str, data: bytes, content_type: str) -> None:
        self.client.put_object(
            bucket,
            key,
            BytesIO(data),
            length=len(data),
            content_type=content_type,
        )

    def get(self, bucket: str, key: str) -> bytes:
        resp = self.client.get_object(bucket, key)
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
