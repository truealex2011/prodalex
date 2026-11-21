import os
from pathlib import Path

_env = Path(__file__).resolve().parent.parent / ".env"
if _env.is_file():                      # локальный запуск: переменные из .env (в docker их задаёт compose)
    for _line in _env.read_text(encoding="utf-8").splitlines():
        _k, _, _v = _line.partition("=")
        if _k.strip() and not _k.startswith("#") and _v.strip():
            os.environ.setdefault(_k.strip(), _v.strip())


class Config:
    # Единственная БД проекта — PostgreSQL (в docker-compose сервис db)
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://autochoice:autochoice@localhost:5432/autochoice",
    )
    # Подпись cookie входа. В проде обязательно задать SECRET_KEY в .env
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-change-me")
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.getenv("COOKIE_SECURE", "0") == "1"  # 1 — когда сайт отдаётся по https
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 24 * 30
    # Автопоиск 3D-моделей на Sketchfab: модели NonCommercial допустимы, пока проект некоммерческий.
    # Для коммерческого запуска поставьте SKETCHFAB_ALLOW_NC=0. SKETCHFAB_TOKEN включает официальное скачивание.
    MODEL_ALLOW_NC = os.getenv("SKETCHFAB_ALLOW_NC", "1") == "1"
    MODEL_INDEX_PREWARM = os.getenv("MODEL_INDEX_PREWARM", "1") == "1"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    # Пока медиа отдаёт сам Flask из static/media; позже — S3/MinIO, меняется только этот URL
    MEDIA_BASE_URL = os.getenv("MEDIA_BASE_URL", "/static/media")

    CATALOG_PATH = os.getenv(
        "CATALOG_PATH",
        os.path.join(os.path.dirname(__file__), "..", "data", "catalog", "cars.json"),
    )
