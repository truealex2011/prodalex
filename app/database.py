"""Создание схемы при старте: применяем db/schema.sql, затем миграции db/00N_*.sql, если таблиц ещё нет.

В проде вместо этого — миграции (Alembic); для демо и docker compose достаточно файлов.
Старые таблицы прежней версии (cars/sessions с JSONB) не удаляем, а переименовываем в legacy_*.
"""
import logging
from pathlib import Path

from sqlalchemy import text

from .models import db

DB_DIR = Path(__file__).resolve().parent.parent / "db"
LOCK = 724_001  # advisory-lock: несколько воркеров gunicorn не должны применять схему одновременно
log = logging.getLogger("db")


def _exists(conn, table: str) -> bool:
    return conn.execute(text("SELECT to_regclass(:t)"), {"t": f"public.{table}"}).scalar() is not None


def _run(conn, filename: str) -> None:
    with conn.connection.cursor() as cur:  # psycopg-курсор: скрипт из нескольких команд
        cur.execute((DB_DIR / filename).read_text(encoding="utf-8"))
    log.warning("применён db/%s", filename)


def ensure_schema() -> None:
    conn = db.session.connection()
    conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": LOCK})
    try:
        if not _exists(conn, "catalog_versions"):
            legacy = conn.execute(text(
                "SELECT 1 FROM information_schema.columns WHERE table_name='cars' AND column_name='brand'")).first()
            if legacy:
                log.warning("найдены таблицы старой схемы — переименовываю в legacy_cars / legacy_sessions")
                conn.execute(text("ALTER TABLE cars RENAME TO legacy_cars"))
                conn.execute(text("ALTER TABLE IF EXISTS sessions RENAME TO legacy_sessions"))
            _run(conn, "schema.sql")
        if not _exists(conn, "users"):
            _run(conn, "002_accounts.sql")
        if not conn.execute(text("SELECT 1 FROM information_schema.columns WHERE table_name='car_assets' "
                                 "AND column_name='origin'")).first():
            _run(conn, "004_media_lookup.sql")
        if not _exists(conn, "model_sources"):
            _run(conn, "005_s3_and_tz.sql")
        if conn.execute(text("SELECT 1 FROM information_schema.columns WHERE table_name='users' "
                             "AND column_name='mode' AND is_nullable='NO'")).first():
            _run(conn, "003_user_mode_optional.sql")
    finally:
        db.session.commit()
        db.session.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK})
        db.session.commit()
