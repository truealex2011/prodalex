"""Хранилище файлов (3D-модели): S3-совместимое (AWS S3, MinIO, Yandex Object Storage) или локальная папка.

    STORAGE_BACKEND=s3   S3_ENDPOINT=http://minio:9000   S3_BUCKET=autochoice-models
    S3_ACCESS_KEY=...    S3_SECRET_KEY=...               S3_REGION=us-east-1   (S3_ENDPOINT не нужен для AWS)

В БД лежат только ключи объектов (car_assets.storage_key, model_sources.storage_key). Браузер получает файл через
наш адрес /media/<ключ>: сервер сам ходит в S3 и кэширует копию на диске, поэтому не нужны ни CORS на бакете,
ни публичные ссылки, а ключи доступа не покидают сервер.
"""
import hashlib
import logging
import mimetypes
import os
from pathlib import Path

log = logging.getLogger("storage")
CONTENT_TYPES = {".glb": "model/gltf-binary", ".json": "application/json"}


def content_type(key: str) -> str:
    return CONTENT_TYPES.get(Path(key).suffix.lower()) or mimetypes.guess_type(key)[0] or "application/octet-stream"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class LocalStorage:
    """Файлы в app/static/media (режим разработки без S3)."""
    name = "local"
    bucket = None

    def __init__(self, root: Path):
        self.root = root

    def put_file(self, key: str, path: Path) -> dict:
        dest = self.root / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        if Path(path).resolve() != dest.resolve():
            dest.write_bytes(Path(path).read_bytes())
        return {"etag": None, "size": dest.stat().st_size}

    def exists(self, key: str) -> bool:
        return (self.root / key).is_file()

    def fetch(self, key: str, cache: Path) -> Path | None:
        p = self.root / key
        return p if p.is_file() else None


class S3Storage:
    name = "s3"

    def __init__(self, bucket: str, endpoint: str | None, access_key: str | None, secret_key: str | None, region: str | None):
        import boto3
        from botocore.config import Config
        self.bucket = bucket
        self.client = boto3.client(
            "s3", endpoint_url=endpoint or None, aws_access_key_id=access_key or None, aws_secret_access_key=secret_key or None,
            region_name=region or "us-east-1", config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 3}))

    def ensure_bucket(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except Exception:
            self.client.create_bucket(Bucket=self.bucket)

    def put_file(self, key: str, path: Path) -> dict:
        self.client.upload_file(str(path), self.bucket, key, ExtraArgs={"ContentType": content_type(key)})
        head = self.client.head_object(Bucket=self.bucket, Key=key)
        return {"etag": head.get("ETag", "").strip('"'), "size": head.get("ContentLength")}

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False

    def fetch(self, key: str, cache: Path) -> Path | None:
        """Локальная копия объекта (скачивается один раз): из неё Flask отдаёт файл с поддержкой Range и кэша."""
        dest = cache / key
        if dest.is_file():
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        try:
            self.client.download_file(self.bucket, key, str(tmp))
        except Exception as e:
            log.warning("S3: %s не получен: %s", key, e)
            tmp.unlink(missing_ok=True)
            return None
        tmp.replace(dest)
        return dest


_instance = None


def get_storage(app=None):
    """Хранилище по настройкам окружения (LocalStorage, если S3 не настроен). Клиент создаётся один раз."""
    global _instance
    if _instance is None:
        from flask import current_app
        app = app or current_app
        if os.getenv("STORAGE_BACKEND", "local") == "s3":
            _instance = S3Storage(os.environ["S3_BUCKET"], os.getenv("S3_ENDPOINT"), os.getenv("S3_ACCESS_KEY"),
                                  os.getenv("S3_SECRET_KEY"), os.getenv("S3_REGION"))
        else:
            _instance = LocalStorage(Path(app.root_path) / "static" / "media")
    return _instance
