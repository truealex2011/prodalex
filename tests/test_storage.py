"""Хранилище моделей: S3-клиент подменён заглушкой (без сети); сопоставление реестра с каталогом."""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json  # noqa: E402

from app.storage import LocalStorage, S3Storage, content_type, sha256_of  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.calls = []

    def head_bucket(self, Bucket):
        if Bucket not in {b for b, _ in self.objects} and Bucket != "ok":
            raise KeyError

    def create_bucket(self, Bucket):
        self.calls.append(("create_bucket", Bucket))

    def upload_file(self, path, bucket, key, ExtraArgs=None):
        self.objects[(bucket, key)] = (Path(path).read_bytes(), ExtraArgs)

    def head_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise KeyError
        return {"ETag": '"abc123"', "ContentLength": len(self.objects[(Bucket, Key)][0])}

    def download_file(self, bucket, key, path):
        Path(path).write_bytes(self.objects[(bucket, key)][0])


def s3_with_fake():
    s = S3Storage.__new__(S3Storage)
    s.bucket, s.client = "models", FakeS3()
    return s


def test_s3_put_exists_fetch(tmp_path):
    f = tmp_path / "car.glb"
    f.write_bytes(b"glTF-data")
    s3 = s3_with_fake()
    assert not s3.exists("models/sf/car.glb")
    put = s3.put_file("models/sf/car.glb", f)
    assert put == {"etag": "abc123", "size": 9}
    assert s3.client.objects[("models", "models/sf/car.glb")][1]["ContentType"] == "model/gltf-binary"
    assert s3.exists("models/sf/car.glb")
    got = s3.fetch("models/sf/car.glb", tmp_path / "cache")
    assert got.read_bytes() == b"glTF-data"
    s3.client.objects.clear()                      # повторный запрос берёт кэш, а не S3
    assert s3.fetch("models/sf/car.glb", tmp_path / "cache") == got


def test_s3_missing_object_returns_none(tmp_path):
    assert s3_with_fake().fetch("models/sf/none.glb", tmp_path) is None


def test_local_storage(tmp_path):
    src = tmp_path / "a.glb"
    src.write_bytes(b"12345")
    st = LocalStorage(tmp_path / "media")
    assert st.put_file("models/sf/a.glb", src)["size"] == 5
    assert st.exists("models/sf/a.glb") and st.fetch("models/sf/a.glb", tmp_path).read_bytes() == b"12345"
    assert st.fetch("models/nope.glb", tmp_path) is None
    assert len(sha256_of(src)) == 64 and content_type("x.glb") == "model/gltf-binary"


def test_registry_rules_and_matching():
    from build_model_registry import status
    from import_models import match_car
    assert status("CC Attribution", True)[0] == "pending"
    assert status("CC Attribution-NonCommercial-ShareAlike", True)[0] == "pending"
    assert status("Editorial", False)[0] == "blocked"
    assert status("Free Standard", True)[0] == "blocked"          # скачать можно, но это не Creative Commons
    assert status("CC Attribution-NoDerivs", True)[0] == "blocked"
    cars = [{"id": 10, "brand": "Kia", "model": "Sportage", "trim": "2.0 AT AWD"}, {"id": 21, "brand": "Kia", "model": "Sportage", "trim": "GT-Line"},
            {"id": 11, "brand": "Toyota", "model": "Camry", "trim": "2.5 AT"}]
    assert match_car("2023 Kia Sportage GT-Line", cars) == 21     # комплектация важнее
    assert match_car("Kia Sportage", cars) == 10
    assert match_car("Toyota Camry 2020", cars) == 11
    assert match_car("Mazda CX-5", cars) is None


def test_registry_file_is_consistent():
    root = Path(__file__).resolve().parent.parent
    reg = json.loads((root / "data" / "catalog" / "sketchfab_models.json").read_text(encoding="utf-8"))
    assert len(reg) == len({m["uid"] for m in reg}) and all(len(m["uid"]) == 32 for m in reg)
    assert all((m["status"] == "blocked") == bool(m["blocked_reason"]) for m in reg)
