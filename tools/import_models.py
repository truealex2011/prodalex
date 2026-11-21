"""Реестр 3D-моделей Sketchfab -> PostgreSQL (метаданные) и S3 (файлы GLB).

    python tools/import_models.py                  # реестр в БД; файлы — те, что удалось получить
    python tools/import_models.py --inbox <папка>  # папка с вручную скачанными <uid>.glb
    python tools/import_models.py --dry-run        # ничего не писать, только показать план

Файл модели берётся из папки --inbox (по умолчанию data/models_inbox) или через официальный Sketchfab Download API,
если задан SKETCHFAB_TOKEN (токен из личного кабинета Sketchfab, в репозиторий не кладём).
В S3 уходят только модели со статусом pending: Creative Commons без ND и скачиваемые (см. build_model_registry.py).
Повторный запуск безопасен: уже сохранённые модели пропускаются.
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from app import create_app  # noqa: E402
from app.models import db  # noqa: E402
from app.sketchfab import download_glb, license_code, looks_like_car, normal, verify_glb  # noqa: E402
from app.storage import content_type, get_storage, sha256_of  # noqa: E402


def match_car(title: str, cars: list[dict]) -> int | None:
    """Машина каталога, которой соответствует модель: все слова бренда и модели есть в названии (приоритет — совпадение комплектации)."""
    words = set(normal(title).split())
    hits = []
    for c in cars:
        brand = {"lada": "lada", "уаз": "uaz"}.get(normal(c["brand"]), normal(c["brand"]))
        tokens = (brand + " " + normal(c["model"])).split()
        if all(t in words for t in tokens):
            trim = [t for t in normal(c["trim"]).split() if not t.isdigit() and len(t) > 2]
            hits.append((sum(t in words for t in trim), -c["id"], c["id"]))
    return max(hits)[2] if hits else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inbox", default=str(ROOT / "data" / "models_inbox"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    registry = json.loads((ROOT / "data" / "catalog" / "sketchfab_models.json").read_text(encoding="utf-8"))
    inbox, token = Path(args.inbox), os.getenv("SKETCHFAB_TOKEN")
    app = create_app({"MODEL_INDEX_PREWARM": False})
    stats = {"stored": 0, "already": 0, "waiting": [], "blocked": 0, "bad": []}
    with app.app_context():
        store = get_storage()
        if store.name == "s3" and not args.dry_run:
            store.ensure_bucket()
        cars = [dict(r) for r in db.session.execute(text("SELECT id, brand, model, trim FROM v_cars")).mappings()]
        for m in registry:
            car_id = match_car(m["title"], cars)
            code = license_code(m["license_url"], m["license"]) if m["license"] else None
            if args.dry_run:
                print(f"{m['status']:8} {m['title'][:44]:44} -> машина {car_id or '—'}")
                continue
            if code:
                db.session.execute(text("""INSERT INTO licences (code, name, url, needs_credit, allows_commercial)
                    VALUES (:c, :n, :u, true, :com) ON CONFLICT (code) DO NOTHING"""),
                                   {"c": code, "n": m["license"], "u": m["license_url"], "com": "NonCommercial" not in m["license"]})
            db.session.execute(text("""
                INSERT INTO model_sources (sketchfab_uid, short_code, title, author, license_label, license_url, license_code,
                                           downloadable, faces, source_url, status, blocked_reason, car_id)
                VALUES (:uid, :sc, :t, :a, :ll, :lu, :lc, :d, :f, :src, :st, :br, :car)
                ON CONFLICT (sketchfab_uid) DO UPDATE SET title = EXCLUDED.title, author = EXCLUDED.author,
                    license_label = EXCLUDED.license_label, license_url = EXCLUDED.license_url, license_code = EXCLUDED.license_code,
                    downloadable = EXCLUDED.downloadable, faces = EXCLUDED.faces, car_id = EXCLUDED.car_id,
                    status = CASE WHEN model_sources.status = 'stored' THEN 'stored' ELSE EXCLUDED.status END,
                    blocked_reason = CASE WHEN model_sources.status = 'stored' THEN NULL ELSE EXCLUDED.blocked_reason END"""),
                               {"uid": m["uid"], "sc": m["short_code"], "t": m["title"], "a": m["author"], "ll": m["license"],
                                "lu": m["license_url"], "lc": code, "d": m["downloadable"], "f": m["faces"], "src": m["url"],
                                "st": m["status"], "br": m["blocked_reason"], "car": car_id})
            db.session.commit()
            if m["status"] == "blocked":
                stats["blocked"] += 1
                continue
            key = f"models/sf/{m['uid']}.glb"
            if db.session.execute(text("SELECT 1 FROM model_sources WHERE sketchfab_uid=:u AND status='stored'"), {"u": m["uid"]}).first():
                stats["already"] += 1
                continue
            local = inbox / f"{m['uid']}.glb"
            try:
                if not local.is_file() and token:
                    download_glb(m["uid"], None, local)           # официальный API, файл проверяется при скачивании
                if not local.is_file():
                    stats["waiting"].append(m["title"])
                    continue
                data = local.read_bytes()
                verify_glb(data)
                if not looks_like_car(data):
                    raise ValueError("в файле не одна машина")
            except Exception as e:
                stats["bad"].append(f"{m['title']}: {e}")
                continue
            put = store.put_file(key, local)
            db.session.execute(text("""UPDATE model_sources SET status='stored', storage_key=:k, size_bytes=:s, sha256=:h, stored_at=now()
                                       WHERE sketchfab_uid=:u"""), {"k": key, "s": put["size"], "h": sha256_of(local), "u": m["uid"]})
            has_model = car_id and db.session.execute(text("SELECT 1 FROM car_assets WHERE car_id=:c AND kind='model_glb'"), {"c": car_id}).first()
            if car_id and not has_model:    # собственная разобранная модель каталога важнее
                db.session.execute(text("""
                    INSERT INTO car_assets (car_id, kind, storage_key, licence_code, credit, size_bytes, origin, source_url, title,
                                            license_url, storage, bucket, etag, content_type)
                    VALUES (:c, 'model_glb', :k, :lc, :cr, :s, 'sketchfab', :src, :t, :lu, :st, :b, :e, :ct)
                    ON CONFLICT (car_id, kind, storage_key) DO NOTHING"""),
                                   {"c": car_id, "k": key, "lc": code, "cr": f"«{m['title']}» — {m['author']} (Sketchfab), {m['license']}",
                                    "s": put["size"], "src": m["url"], "t": m["title"], "lu": m["license_url"], "st": store.name,
                                    "b": store.bucket, "e": put["etag"], "ct": content_type(key)})
            db.session.commit()
            stats["stored"] += 1
    if args.dry_run:
        return
    print(f"Хранилище: {store.name}. В реестре {len(registry)}: сохранено сейчас {stats['stored']}, уже было {stats['already']}, "
          f"закрыто лицензией/автором {stats['blocked']}, ждут файла {len(stats['waiting'])}, отклонено {len(stats['bad'])}.")
    if stats["waiting"]:
        print(f"Нет файла GLB (положите <uid>.glb в {inbox} или задайте SKETCHFAB_TOKEN):")
        print("  " + "\n  ".join(stats["waiting"]))
    for b in stats["bad"]:
        print("  отклонено:", b)


if __name__ == "__main__":
    main()
