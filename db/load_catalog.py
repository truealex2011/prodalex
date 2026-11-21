"""Ручная загрузка data/catalog/cars.json в БД со схемой db/schema.sql (приложение делает то же при старте).

    python db/load_catalog.py postgresql://user@localhost:5432/autochoice
"""
import json
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.catalog import catalog_version, load_items  # noqa: E402

SEED = ROOT / "data" / "catalog" / "cars.json"

if __name__ == "__main__":
    items = json.loads(SEED.read_text(encoding="utf-8"))
    with psycopg.connect(sys.argv[1]) as con, con.cursor() as cur:
        load_items(cur, items, catalog_version(str(SEED)))
    print(f"загружено {len(items)} машин, версия каталога {catalog_version(str(SEED))}")
