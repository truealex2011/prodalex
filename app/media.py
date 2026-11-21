"""3D-модель или фото машины: берём сохранённое, иначе ищем (Sketchfab → GLB-файл, нет модели → фото) и запоминаем в БД."""
import json
import logging
import threading
import time
from collections import defaultdict
from pathlib import Path

from flask import current_app
from sqlalchemy import text

from . import photos as photos_mod
from . import sketchfab, splitter
from .models import db
from .storage import content_type, get_storage

log = logging.getLogger("media")
_locks: dict[int, threading.Lock] = defaultdict(threading.Lock)   # одна машина — один поиск за раз
RECHECK_OK = "7 days"          # «модели нет» — перепроверяем раз в неделю
RECHECK_ERROR = "10 minutes"   # источник был недоступен — пробуем скоро снова


def _dirs() -> tuple[Path, Path]:
    root = Path(current_app.root_path)
    return root / "static" / "media" / "models" / "sf2", root.parent / "data" / "cache"


def _rows(car_id: int) -> list[dict]:
    q = text("""SELECT kind, storage_key, credit, title, source_url, license_url, origin, storage
                  FROM car_assets WHERE car_id = :c AND kind IN ('model_glb', 'parts_json', 'image') ORDER BY id""")
    return [dict(r) for r in db.session.execute(q, {"c": car_id}).mappings()]


def _check(car_id: int) -> dict | None:
    q = text("""SELECT error, (now() - checked_at) < CAST(CASE WHEN error IS NULL THEN :ok ELSE :err END AS interval) AS fresh
                  FROM car_media_checks WHERE car_id = :c""")
    r = db.session.execute(q, {"c": car_id, "ok": RECHECK_OK, "err": RECHECK_ERROR}).mappings().first()
    return dict(r) if r else None


def _add_asset(car_id, kind, key, credit, origin, *, title=None, source_url=None, license_code=None, license_name=None,
               license_url=None, size=None, storage="local", bucket=None, etag=None, content_type=None):
    if license_code:
        db.session.execute(text("""INSERT INTO licences (code, name, url, needs_credit, allows_commercial)
                                   VALUES (:c, :n, :u, true, :com) ON CONFLICT (code) DO NOTHING"""),
                           {"c": license_code, "n": license_name or license_code, "u": license_url,
                            "com": "NC" not in license_code})
    db.session.execute(text("""INSERT INTO car_assets (car_id, kind, storage_key, licence_code, credit, size_bytes, origin,
                                                       source_url, title, license_url, storage, bucket, etag, content_type)
                               VALUES (:car, CAST(:kind AS asset_kind), :key, :lic, :credit, :size, :origin, :src, :title, :lurl,
                                       :storage, :bucket, :etag, :ctype)
                               ON CONFLICT (car_id, kind, storage_key) DO NOTHING"""),
                       {"car": car_id, "kind": kind, "key": key, "lic": license_code, "credit": credit, "size": size,
                        "origin": origin, "src": source_url, "title": title, "lurl": license_url,
                        "storage": storage, "bucket": bucket, "etag": etag, "ctype": content_type})


def _split_model(car: dict, out_dir: Path, model: dict) -> dict | None:
    """Разбивает скачанный GLB на детали (≈1 с): перезаписывает файл модели и кладёт рядом parts.json.
    Реальные размеры модели (колёсная база, колея), пересчитанные в метры по длине машины из каталога, идут в physics теста на лося."""
    path = out_dir / model["file"]
    raw = _dirs()[1] / "raw_glb" / model["file"]
    raw.parent.mkdir(parents=True, exist_ok=True)
    if not raw.exists():                       # исходник автора: разбиваем всегда его, а не свой прошлый результат
        raw.write_bytes(path.read_bytes())
    try:
        length = (car.get("specs") or {}).get("length_mm")
        glb, manifest = splitter.split_glb(raw.read_bytes(), length / 1000 if length else None)
    except Exception as e:     # не нашли колёса, сжатые меши и т. п.: показываем модель без разметки, а не ломаем показ
        log.warning("разбиение %s не удалось: %s", car["title"], e)
        return None
    path.write_bytes(glb)
    manifest["quality"] = splitter.quality_stats(raw.read_bytes())
    manifest["credit"] = f"«{model['title']}» — {model['author']} (Sketchfab), {model['license']}"
    manifest["source_file"] = model["file"]
    path.with_suffix(".json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    m, k = manifest["measured"], manifest["measured"].get("scale_to_m")
    if not k and m.get("wheel_diameter"):      # длина машины неизвестна: масштаб по колесу (реальный диаметр ≈ 0,66 м)
        k = 0.66 / m["wheel_diameter"]
    if k:
        real = {"wheelbase_m": round(m["wheelbase"] * k, 3), "track_m": round(m["track"] * k, 3),
                "width_m": round(m["width"] * k, 3), "height_m": round(m["height"] * k, 3), "length_m": round(m["length"] * k, 3),
                "wheel_diameter_m": round(m["wheel_diameter"] * k, 3), "source": "3d-model"}
        manifest["measured_real"] = real
        if not isinstance(car.get("id"), int):    # машина из объявления: в БД каталога не пишем
            return manifest
        db.session.execute(text("""UPDATE car_specs SET extra = jsonb_set(COALESCE(extra, '{}'::jsonb), '{measured}', CAST(:m AS jsonb))
                                   WHERE car_id = :c"""), {"m": json.dumps(real), "c": car["id"]})
    return manifest


def _too_poor(manifest: dict | None, glb_path: Path) -> str | None:
    """Причина отказа от 3D-модели по качеству (число полигонов, разрешение текстур); None — модель годится."""
    cfg = current_app.config
    q = (manifest or {}).get("quality")
    if q is None:
        try:
            q = splitter.quality_stats(glb_path.read_bytes())
        except Exception:
            return None
    return splitter.low_quality_reason(q, int(cfg.get("MODEL_MIN_TRIANGLES", 60_000)), int(cfg.get("MODEL_MIN_TEXTURE_PX", 512)))


def _lookup(car: dict) -> None:
    out_dir, cache_dir = _dirs()
    allow_nc = bool(current_app.config.get("MODEL_ALLOW_NC", True))
    error, model, found = None, None, []
    try:
        model = sketchfab.find_and_download(car, out_dir, cache_dir, allow_nc)
    except Exception as e:      # сеть, лимиты, битый файл — не ломаем показ машины
        error = str(e)[:200]
        log.warning("поиск 3D для %s: %s", car["title"], error)
    manifest = None
    if model:
        manifest = _split_model(car, out_dir, model)
        why = _too_poor(manifest, out_dir / model["file"])
        if why:                                  # низкополигональная или «мыльная» — показываем фото
            log.info("3D для %s отклонена: %s", car["title"], why)
            error, model, manifest = f"3D отклонена: {why}", None, None
    if model:
        key = f"models/sf2/{model['file']}"
        put = {}
        store = get_storage()
        if store.name == "s3":   # файл модели (и разметка деталей) уходит в S3, локальная копия остаётся кэшем
            put = store.put_file(key, out_dir / model["file"]) | {"storage": "s3", "bucket": store.bucket, "content_type": content_type(key)}
            if manifest:
                store.put_file(key.replace(".glb", ".json"), (out_dir / model["file"]).with_suffix(".json"))
        if manifest:
            _add_asset(car["id"], "parts_json", key.replace(".glb", ".json"), None, "sketchfab", title=model["title"],
                       source_url=model["source_url"], storage=put.get("storage", "local"), bucket=put.get("bucket"),
                       content_type="application/json")
        _add_asset(car["id"], "model_glb", key,
                   f"«{model['title']}» — {model['author']} (Sketchfab), {model['license']}", "sketchfab",
                   title=model["title"], source_url=model["source_url"], license_code=model["license_code"],
                   license_name=model["license"], license_url=model["license_url"], size=model["size"],
                   storage=put.get("storage", "local"), bucket=put.get("bucket"), etag=put.get("etag"), content_type=put.get("content_type"))
    else:
        try:
            found = photos_mod.find_photos(car)
        except Exception as e:
            error = error or str(e)[:200]
            log.warning("поиск фото для %s: %s", car["title"], e)
        if found:  # свежие фото заменяют прежние
            db.session.execute(text("DELETE FROM car_assets WHERE car_id = :c AND origin = 'wikimedia'"), {"c": car["id"]})
        for p in found:
            code = sketchfab.license_code(p["license_url"], p["license"])
            _add_asset(car["id"], "image", p["url"], f"{p['author']} / Wikimedia Commons, {p['license']}", "wikimedia",
                       title=p["title"], source_url=p["page"], license_code=code, license_name=p["license"], license_url=p["license_url"])
    # ошибка важна только если ничего не нашли: иначе результат уже есть
    db.session.execute(text("""INSERT INTO car_media_checks (car_id, model_found, photos_found, error)
                               VALUES (:c, :m, :p, :e)
                               ON CONFLICT (car_id) DO UPDATE SET checked_at = now(), model_found = :m, photos_found = :p, error = :e"""),
                       {"c": car["id"], "m": bool(model), "p": len(found), "e": None if (model or found) else error})
    db.session.commit()


def _payload(rows: list[dict], base: str) -> dict:
    model = next((r for r in rows if r["kind"] == "model_glb"), None)
    if model:
        parts = next((r for r in rows if r["kind"] == "parts_json"), None)
        root = "/media" if model["storage"] == "s3" else base
        return {"kind": "model", "model": {
            "url": f"{root}/{model['storage_key']}", "parts_url": f"{root}/{parts['storage_key']}" if parts else None, "credit": model["credit"], "title": model["title"],
            "source_url": model["source_url"], "license_url": model["license_url"], "origin": model["origin"]}}
    photos = [{"url": r["storage_key"], "credit": r["credit"], "title": r["title"], "page": r["source_url"],
               "license_url": r["license_url"]} for r in rows if r["kind"] == "image" and r["storage_key"].startswith("http")]
    return {"kind": "photos", "photos": photos} if photos else {"kind": "none"}


def resolve(car: dict, base: str) -> dict:
    """Что показать для машины: {"kind": "model" | "photos" | "none", ...}. Первый вызов может занять время (поиск и загрузка)."""
    with _locks[car["id"]]:
        rows = _rows(car["id"])
        if not any(r["kind"] == "model_glb" for r in rows):
            chk = _check(car["id"])
            if not (chk and chk["fresh"]):
                _lookup(car)
                rows = _rows(car["id"])
        return _payload(rows, base)


def prewarm_index(app) -> None:
    """Фоном готовим индекс файлов Objaverse (один раз), чтобы первый поиск не ждал его скачивания."""
    def run():
        with app.app_context():
            try:
                _, cache_dir = _dirs()
                if not (cache_dir / "objaverse-paths.sqlite").exists():
                    sketchfab.mirror_paths([], cache_dir)
            except Exception as e:
                log.warning("индекс Objaverse пока недоступен: %s", e)
    threading.Thread(target=run, daemon=True, name="objaverse-index").start()


# ───────────── 3D для машины из объявления (не из каталога) ─────────────
_name_lock = threading.Lock()


def _index_path() -> Path:
    return _dirs()[1] / "model3d_index.json"


def resolve_name(name: str, year, body: str | None, length_mm, base: str) -> dict:
    """3D-модель по названию из объявления: поиск на Sketchfab, загрузка GLB, разбиение на детали. Результат кэшируется."""
    key = f"{sketchfab.normal(name)}|{year or ''}|q2"      # q2: с проверкой качества модели
    with _locks[hash(key)]:
        idx = json.loads(_index_path().read_text(encoding="utf-8")) if _index_path().exists() else {}
        hit = idx.get(key)
        out_dir, cache_dir = _dirs()
        if hit and (hit["kind"] == "model" and (out_dir / hit["file"]).exists() or hit["kind"] == "none" and time.time() - hit["at"] < 7 * 86400):
            return hit
        words = name.split()
        car = {"brand": words[0], "model": " ".join(words[1:]) or name, "title": name, "year": int(year) if str(year or "").isdigit() else None,
               "body": body, "specs": {"length_mm": length_mm}}
        payload = {"kind": "none", "at": time.time()}
        allow = bool(current_app.config.get("MODEL_ALLOW_NC", True))
        similar, manifest = None, None

        def accept(m):
            """Скачанную модель разбиваем на детали и проверяем качество: низкополигональные и «мыльные» отбрасываем (будут фото)."""
            nonlocal manifest
            mf = _split_model(car, out_dir, m)
            why = _too_poor(mf, out_dir / m["file"])
            if why:
                log.info("3D %s для %s отклонена: %s", m.get("title"), name, why)
                payload["reason"] = why
                return False
            manifest = mf
            return True

        try:
            model = sketchfab.find_and_download(car, out_dir, cache_dir, allow)
        except Exception as e:
            log.warning("3D для %s: %s", name, e)
            model = None
        if model and not accept(model):
            model = None
        elif not model and current_app.config.get("MODEL_ALLOW_SIMILAR"):   # точной нет: ближайшая той же марки (по умолчанию выключено — лучше фото)
            for alt in _similar_names(name, body):
                words = alt.split()
                try:
                    model = sketchfab.find_and_download({**car, "brand": words[0], "model": " ".join(words[1:]), "fallback": True},
                                                        out_dir, cache_dir, allow)
                except Exception as e:
                    log.warning("3D для %s: %s", alt, e)
                    model = None
                if model and accept(model):
                    similar = alt
                    break
                model = None
        if model:
            store, k = get_storage(), f"models/sf2/{model['file']}"
            root = base
            if store.name == "s3":
                store.put_file(k, out_dir / model["file"])
                if manifest:
                    store.put_file(k.replace(".glb", ".json"), (out_dir / model["file"]).with_suffix(".json"))
                root = "/media"
            payload = {"kind": "model", "at": time.time(), "file": model["file"], "url": f"{root}/{k}",
                       "parts_url": f"{root}/{k.replace('.glb', '.json')}" if manifest else None,
                       "credit": f"«{model['title']}» — {model['author']} (Sketchfab), {model['license']}",
                       "measured": (manifest or {}).get("measured_real"), "similar": similar}
        with _name_lock:
            idx = json.loads(_index_path().read_text(encoding="utf-8")) if _index_path().exists() else {}
            idx[key] = payload
            _index_path().parent.mkdir(parents=True, exist_ok=True)
            _index_path().write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")
        return payload


def _similar_names(name: str, body: str | None) -> list[str]:
    """Запасные названия для 3D, когда точной модели нет: другие модели ТОЙ ЖЕ марки и ТОГО ЖЕ типа кузова (по популярности),
    а для марок вне снимка рынка — их известные линейки. Седан не заменяем внедорожником и наоборот."""
    import re as _re
    from . import engine
    toks = engine._tokens(name)
    brand = name.split()[0].lower().replace("-benz", "")
    want_body = engine.BODY_CATS.get(body or "")
    same = []
    for m in engine.market_models():
        if not m["brand"].startswith(brand) or engine._tokens(m["title"]) >= toks:
            continue
        if want_body and not (m["desc"] and _re.search(want_body, m["desc"])):      # тип кузова неизвестен или другой — не берём
            continue
        same.append(m)
    same.sort(key=lambda m: (-len(engine._tokens(m["title"]) & toks), -m["count"]))      # сначала родственные по названию, затем популярные
    kin = {"rolls-royce": ["Rolls-Royce Phantom", "Rolls-Royce Ghost", "Rolls-Royce Cullinan", "Rolls-Royce Dawn", "Rolls-Royce Wraith"],
           "bentley": ["Bentley Continental GT", "Bentley Bentayga", "Bentley Flying Spur"],
           "lamborghini": ["Lamborghini Urus", "Lamborghini Huracan", "Lamborghini Aventador"],
           "ferrari": ["Ferrari 488", "Ferrari F8", "Ferrari Roma", "Ferrari 812"], "mclaren": ["McLaren 720S", "McLaren 570S"]}
    names = kin.get(brand, []) + [m["title"] for m in same[:8]]
    return list(dict.fromkeys(n for n in names if n.lower() != name.lower()))[:8]
