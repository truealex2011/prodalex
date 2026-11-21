"""Каталог: загрузка сида в нормализованную схему (db/schema.sql) и чтение карточек машин.

Сид — data/catalog/cars.json. Загрузка идемпотентна: если версия (sha256 файла) уже в
catalog_versions, ничего не делаем; если файл изменился — обновляем строки, а машины,
которых в сиде больше нет, скрываем (is_active = false), не удаляя историю выдач.
"""
import hashlib
import json
import re

from sqlalchemy import text

from .models import db

COLORS = {
    "red": ("красный", "#c0392b"), "white": ("белый", "#f2f2f2"), "black": ("чёрный", "#111111"),
    "blue": ("синий", "#1f4e9c"), "gray": ("серый", "#7d7f84"), "green": ("зелёный", "#2e7d4f"),
    "silver": ("серебристый", "#c4c7cc"), "yellow": ("жёлтый", "#e5b800"),
    "orange": ("оранжевый", "#e07b1a"), "brown": ("коричневый", "#6b4a2f"),
}
TAGS = {
    "first-car": "Первая машина", "city": "Город", "family": "Семья", "road-trip": "Дальние поездки",
    "off-road": "Бездорожье", "eco": "Экономичная", "premium": "Премиум", "comfort": "Комфорт",
    "sport": "Динамика", "budget": "Бюджетная",
}
COUNTRY = {"LADA": "RU", "УАЗ": "RU", "Hyundai": "KR", "Kia": "KR", "Toyota": "JP", "Skoda": "CZ",
           "Renault": "FR", "Haval": "CN", "Geely": "CN", "Chery": "CN", "BMW": "DE", "Porsche": "DE"}

# ключ в specs сида -> колонка car_specs
SPEC_COLS = {
    "accel_0_100": "accel_0_100", "top_speed": "top_speed_kmh", "clearance_mm": "clearance_mm",
    "length_mm": "length_mm", "width_mm": "width_mm", "height_mm": "height_mm", "weight_kg": "weight_kg",
    "track_mm": "track_mm", "tank_l": "tank_l", "tires": "tires", "airbags": "airbags", "esp": "esp",
    "led_headlights": "led_headlights", "led_taillights": "led_taillights",
    "electric_handbrake": "electric_handbrake", "power_mirrors": "power_mirrors",
    "screen_in": "screen_in", "climate": "climate", "chassis": "chassis",
}
SPEC_KEYS = {col: key for key, col in SPEC_COLS.items()}
PART_GROUP = [(r"light", "lights"), (r"wheel", "wheels"),
              (r"door|hood|grille|bumper|roof|windshield|mirror|trunk|paint", "exterior"),
              (r"seat|dashboard|console|steering|pedal|gear|vent|screen|cluster|handbrake", "interior")]


def catalog_version(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:12]


def _part_group(pid: str) -> str:
    return next((g for rx, g in PART_GROUP if re.search(rx, pid)), "other")


def _source(label: str) -> str:
    for name in ("Euro NCAP", "ANCAP", "ARCAP", "km77", "Teknikens Värld"):
        if name.lower() in label.lower():
            return name
    return "Прочее"


def load_items(cur, items: list[dict], version: str) -> None:
    """Записать каталог через курсор psycopg (используется и приложением, и db/load_catalog.py)."""
    cur.execute("INSERT INTO licences (code, name, needs_credit, allows_commercial) VALUES "
                "('CC-BY-4.0','Creative Commons Attribution 4.0',true,true),"
                "('UNKNOWN','Уточнить на странице модели',true,false) ON CONFLICT DO NOTHING")
    for code, (ru, hx) in COLORS.items():
        cur.execute("INSERT INTO colors VALUES (%s,%s,%s) ON CONFLICT (code) DO UPDATE "
                    "SET name_ru=EXCLUDED.name_ru, hex=EXCLUDED.hex", (code, ru, hx))
    for code, ru in TAGS.items():
        cur.execute("INSERT INTO tags VALUES (%s,%s) ON CONFLICT DO NOTHING", (code, ru))

    for c in items:
        cur.execute("INSERT INTO brands (name, country) VALUES (%s,%s) "
                    "ON CONFLICT (name) DO UPDATE SET name=EXCLUDED.name RETURNING id",
                    (c["brand"], COUNTRY.get(c["brand"])))
        brand_id = cur.fetchone()[0]
        cur.execute("INSERT INTO car_models (brand_id, name) VALUES (%s,%s) "
                    "ON CONFLICT (brand_id, name) DO UPDATE SET name=EXCLUDED.name RETURNING id",
                    (brand_id, c["model"]))
        model_id = cur.fetchone()[0]
        cur.execute(
            """INSERT INTO cars (id, model_id, trim, year, body, seats, trunk_l, power_hp, consumption_l100,
                                 drive, transmission, fuel, price_rub, listing_url, is_active)
               VALUES (%(id)s,%(m)s,%(trim)s,%(year)s,%(body)s,%(seats)s,%(trunk_l)s,%(power_hp)s,
                       %(consumption_l100)s,%(drive)s,%(transmission)s,%(fuel)s,%(price)s,%(listing_url)s,true)
               ON CONFLICT (id) DO UPDATE SET model_id=EXCLUDED.model_id, trim=EXCLUDED.trim, year=EXCLUDED.year,
                 body=EXCLUDED.body, seats=EXCLUDED.seats, trunk_l=EXCLUDED.trunk_l, power_hp=EXCLUDED.power_hp,
                 consumption_l100=EXCLUDED.consumption_l100, drive=EXCLUDED.drive,
                 transmission=EXCLUDED.transmission, fuel=EXCLUDED.fuel, price_rub=EXCLUDED.price_rub,
                 listing_url=EXCLUDED.listing_url, is_active=true""",
            {**{k: c.get(k) for k in ("id", "trim", "year", "body", "seats", "trunk_l", "power_hp",
                                      "consumption_l100", "drive", "transmission", "fuel", "price",
                                      "listing_url")}, "m": model_id})

        specs = c.get("specs") or {}
        cols = {SPEC_COLS[k]: v for k, v in specs.items() if k in SPEC_COLS}
        extra = {k: v for k, v in specs.items() if k not in SPEC_COLS and k not in ("moose_real", "crash_real")}
        names = ["car_id", "extra", *cols]
        cur.execute(
            f"INSERT INTO car_specs ({','.join(names)}) VALUES ({','.join(['%s'] * len(names))}) "
            "ON CONFLICT (car_id) DO UPDATE SET " + ",".join(f"{n}=EXCLUDED.{n}" for n in names[1:]),
            [c["id"], json.dumps(extra), *cols.values()])

        cur.execute("DELETE FROM car_colors WHERE car_id=%s", (c["id"],))
        for col in c.get("colors") or []:
            cur.execute("INSERT INTO car_colors VALUES (%s,%s)", (c["id"], col))
        cur.execute("DELETE FROM car_tags WHERE car_id=%s", (c["id"],))
        for tag in c.get("tags") or []:
            cur.execute("INSERT INTO tags VALUES (%s,%s) ON CONFLICT DO NOTHING", (tag, tag))
            cur.execute("INSERT INTO car_tags VALUES (%s,%s)", (c["id"], tag))

        cur.execute("DELETE FROM moose_results WHERE car_id=%s", (c["id"],))
        cur.execute("DELETE FROM crash_ratings WHERE car_id=%s", (c["id"],))
        if specs.get("moose_real"):
            m = specs["moose_real"]
            cur.execute("INSERT INTO test_sources (name, kind) VALUES (%s,'moose') "
                        "ON CONFLICT (name) DO UPDATE SET name=EXCLUDED.name RETURNING id", (_source(m["label"]),))
            cur.execute("INSERT INTO moose_results (car_id, source_id, speed_kmh, label, url) VALUES (%s,%s,%s,%s,%s)",
                        (c["id"], cur.fetchone()[0], m["speed"], m["label"], m.get("url")))
        if specs.get("crash_real"):
            r = specs["crash_real"]
            cur.execute("INSERT INTO test_sources (name, kind) VALUES (%s,'crash') "
                        "ON CONFLICT (name) DO UPDATE SET name=EXCLUDED.name RETURNING id", (_source(r["label"]),))
            stars = re.search(r"(\d)\s*★", r["label"])
            cur.execute("INSERT INTO crash_ratings (car_id, source_id, stars, score_idx, label, url) "
                        "VALUES (%s,%s,%s,%s,%s,%s)",
                        (c["id"], cur.fetchone()[0], int(stars.group(1)) if stars else None,
                         r["index"], r["label"], r.get("url")))

        cur.execute("DELETE FROM car_assets WHERE car_id=%s AND origin='catalog'", (c["id"],))  # найденное автоматически не трогаем
        credit = c.get("model_credit")
        for kind, key in (("model_glb", c.get("model_key")), ("parts_json", c.get("parts_key")),
                          ("image", c.get("image_key"))):
            if key:
                lic = ("CC-BY-4.0" if "CC BY" in credit else "UNKNOWN") if credit and kind == "model_glb" else None
                cur.execute("INSERT INTO car_assets (car_id, kind, storage_key, licence_code, credit) "
                            "VALUES (%s,%s,%s,%s,%s)",
                            (c["id"], kind, key, lic, credit if kind == "model_glb" else None))

        cur.execute("DELETE FROM car_hotspots WHERE car_id=%s", (c["id"],))
        for pid, fact in (c.get("hotspots") or {}).items():
            cur.execute("INSERT INTO car_parts VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                        (pid, pid.replace("_", " "), _part_group(pid)))
            cur.execute("INSERT INTO car_hotspots VALUES (%s,%s,%s)", (c["id"], pid, fact))

    cur.execute("UPDATE cars SET is_active = false WHERE NOT (id = ANY(%s))", ([c["id"] for c in items],))
    cur.execute("INSERT INTO catalog_versions (version, cars_count) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                (version, len(items)))


def load_seed(path: str) -> int:
    version = catalog_version(path)
    if db.session.execute(text("SELECT 1 FROM catalog_versions WHERE version = :v"), {"v": version}).first():
        return 0
    with open(path, encoding="utf-8") as f:
        items = json.load(f)
    conn = db.session.connection().connection  # psycopg-соединение той же транзакции
    with conn.cursor() as cur:
        load_items(cur, items, version)
    db.session.commit()
    return len(items)


# ───────────── чтение ─────────────
_CARD_SQL = text("""
    SELECT to_jsonb(v) || jsonb_build_object('spec_row', to_jsonb(s) - 'car_id',
                                             'hotspots', COALESCE(h.hotspots, '{}'::jsonb)) AS card
      FROM v_cars v
      LEFT JOIN car_specs s ON s.car_id = v.id
      LEFT JOIN v_car_hotspots h ON h.car_id = v.id
     {where}
     ORDER BY {order}
""")


def _card(row: dict, media_base: str) -> dict:
    """Строка v_cars -> тот же словарь, что раньше отдавал Car.to_dict()."""
    def url(key):
        if key and key.startswith("http"):  # внешняя ссылка (фото Wikimedia)
            return key
        return f"{media_base}/{key}" if key else None

    spec_row = row.pop("spec_row") or {}
    specs = {SPEC_KEYS[k]: v for k, v in spec_row.items() if k in SPEC_KEYS and v is not None}
    specs.update(spec_row.get("extra") or {})
    specs["moose_real"] = row.pop("moose_real")
    specs["crash_real"] = row.pop("crash_real")
    model_key, parts_key, image_key = row.pop("model_key"), row.pop("parts_key"), row.pop("image_key")
    s3 = row.pop("model_storage", None) == "s3"
    model_url = f"/media/{model_key}" if model_key and s3 else url(model_key)
    parts_key = parts_key and (f"/media/{parts_key}" if s3 else url(parts_key))
    return {**row, "specs": specs, "image_url": url(image_key), "model_url": model_url,
            "parts_url": parts_key}


def fetch_cars(media_base: str, car_id: int | None = None, random: bool = False) -> list[dict]:
    where = "WHERE v.id = :id" if car_id is not None else ""
    sql = text(_CARD_SQL.text.replace("{where}", where).replace("{order}", "random() LIMIT 1" if random else "v.id"))
    rows = db.session.execute(sql, {"id": car_id} if car_id is not None else {}).scalars().all()
    return [_card(r, media_base) for r in rows]
