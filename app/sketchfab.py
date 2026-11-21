"""Поиск 3D-модели машины на Sketchfab и скачивание её ФАЙЛОМ (GLB) на наш сервер.

Модель не встраивается чужим плеером: файл лежит у нас (app/static/media/models/sf/<uid>.glb) и
показывается нашим просмотрщиком three.js, а автор и лицензия сохраняются в БД и выводятся в интерфейсе.

Как это работает (идея взята из проекта «парсинк»):
  1. публичный поиск Sketchfab (без токена) по «марка модель [год]», только скачиваемые модели;
  2. отбор по названию, году, кузову и лицензии (Creative Commons, без ND — мы перекрашиваем кузов);
  3. файл берётся:
       - официально через Sketchfab Download API, если задан SKETCHFAB_TOKEN;
       - иначе из зеркала Objaverse (датасет allenai/objaverse на Hugging Face с GLB моделей Sketchfab под CC);
  4. GLB проверяется (автономный файл без внешних ссылок, размер ограничен) и сохраняется.
"""
import gzip
import json
import logging
import math
import os
import re
import sqlite3
import tempfile
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

log = logging.getLogger("sketchfab")

API = "https://api.sketchfab.com/v3"
HF = "https://huggingface.co/datasets/allenai/objaverse/resolve/main/"
MAX_BYTES = 96 * 1024 * 1024
MAX_FACES = 1_200_000
UA = {"User-Agent": "AutoChoice/1.0 (educational project)"}

BODIES = {
    "sedan": ("sedan", "saloon", "седан"), "hatch": ("hatchback", "хэтчбек"), "wagon": ("wagon", "estate", "touring", "универсал"),
    "suv": ("suv", "crossover", "кроссовер"), "minivan": ("minivan", "van", "минивэн"),
}
PARTS = re.compile(r"\b(?:wheels?|rims?|brakes?|chassis|engines?|bumpers?|headlights?|steering|dashboard|seats?|bodykit|pack|collection|interior only)\b")
LOW_QUALITY = re.compile(r"\b(low poly|lowpoly|psx|stylized|cartoon|wreck|tuned|custom|turbo|sport type|toy|minecraft|lego)\b")
TRANSLIT = {"УАЗ": "UAZ", "ЛАДА": "LADA"}


def normal(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"[^\w]+", " ", s).strip()


def _body_group(text: str):
    padded = f" {normal(text)} "
    return next((k for k, words in BODIES.items() if any(f" {normal(w)} " in padded for w in words)), None)


# Одна и та же машина в разных странах называется по-разному: на Sketchfab её ищут под мировым названием
ALIASES = {
    "hyundai solaris": ["Hyundai Accent"], "kia rio": ["Kia K2"], "hyundai elantra": ["Hyundai Avante"], "hyundai creta": ["Hyundai ix25"],
    "renault logan": ["Dacia Logan"], "renault sandero": ["Dacia Sandero"], "renault duster": ["Dacia Duster"],
    "mitsubishi asx": ["Mitsubishi Outlander Sport"], "nissan qashqai": ["Nissan Dualis"], "toyota land cruiser prado": ["Toyota Land Cruiser Prado 150"],
    "skoda rapid": ["Skoda Rapid Spaceback"], "chevrolet cruze": ["Holden Cruze"], "toyota rav4": ["Toyota RAV4"],
}


def names_for(car: dict) -> list[str]:
    """Названия для поиска: полное («Kia Sportage») и сокращённое без хвоста («Tiggo 7 Pro Max» → «Tiggo 7»)."""
    brand = TRANSLIT.get(car["brand"].upper(), car["brand"])
    model = car["model"]
    out = [f"{brand} {model}"]
    words = model.split()
    if len(words) > 1:
        out.append(f"{brand} {' '.join(words[:-1])}")
    out += ALIASES.get(normal(out[0]), [])
    return list(dict.fromkeys(out))


def best_model(candidates: list[dict], name: str, year=None, body=None):
    """Выбирает лучшую модель среди результатов поиска. Совпадение важнее «красивых» метрик."""
    tokens, target = normal(name).split(), body if body in BODIES else _body_group(body or "")
    best, best_score = None, -1e9
    for m in candidates:
        title = normal(m.get("name"))
        words = title.split()
        if (not re.fullmatch(r"[a-f0-9]{32}", m.get("uid", "")) or m.get("isAgeRestricted") or PARTS.search(title)
                or not all(t in words for t in tokens) or (m.get("faceCount") or 0) > MAX_FACES):
            continue
        suffix = " ".join(w for w in words if w not in tokens)
        years = [int(y) for y in re.findall(r"\b(?:19|20)\d{2}\b", suffix)]
        rng = re.search(r"\b((?:19|20)\d{2})\s*[-–—]\s*((?:19|20)\d{2})\b", m.get("name") or "")
        year_match = bool(year and (year in years or (rng and int(rng[1]) <= int(year) <= int(rng[2]))))
        if year and years and not year_match and min(abs(y - int(year)) for y in years) > 6:
            continue
        cand_body = _body_group(suffix)
        if target and cand_body and target != cand_body:
            continue
        body_match = bool(target and cand_body == target)
        quality = (min(6, math.log10(1 + (m.get("faceCount") or 0))) * 2 + min(4, math.log10(1 + (m.get("likeCount") or 0)))
                   + (3 if m.get("staffpickedAt") else 0) + (3 if title == normal(name) else 0)
                   - (7 if LOW_QUALITY.search(suffix) else 0))
        score = (100 if year_match else 0) + (30 if body_match else 0) + quality
        if score > best_score:
            best, best_score = m, score
    return best


# ───────────── лицензии ─────────────
def license_ok(lic: dict | None, allow_nc: bool) -> bool:
    label = (lic or {}).get("label") or ""
    if not label.startswith("CC"):
        return False
    if "NoDeriv" in label:      # мы перекрашиваем кузов, то есть изменяем модель
        return False
    return allow_nc or "NonCommercial" not in label


def license_code(url: str, label: str) -> str:
    m = re.search(r"creativecommons\.org/(licenses|publicdomain)/([^/]+)/([^/]+)", url or "")
    if not m:
        return "CC-" + re.sub(r"\W+", "-", label).strip("-").upper()[:30] if label else "UNKNOWN"
    return "CC0-1.0" if m[1] == "publicdomain" else f"CC-{m[2].upper()}-{m[3]}"


# ───────────── сеть ─────────────
_cooldown = 0.0
_cache: dict[str, tuple[float, list]] = {}


def _get(url: str, timeout: int = 12, headers: dict | None = None):
    global _cooldown
    if _cooldown > time.time():
        raise RuntimeError("rate limited")
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            _cooldown = time.time() + max(60, int(e.headers.get("retry-after") or 0))
        raise


def _json(url: str, **kw):
    with _get(url, **kw) as r:
        return json.loads(r.read().decode("utf-8"))


def search(q: str) -> list[dict]:
    key = normal(q)
    hit = _cache.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    params = urllib.parse.urlencode({"type": "models", "q": q, "count": 48, "downloadable": "true", "restricted": "false"})
    results = _json(f"{API}/search?{params}").get("results")
    if not isinstance(results, list):
        raise RuntimeError("Sketchfab: неожиданный ответ")
    if len(_cache) >= 256:
        _cache.pop(next(iter(_cache)))
    _cache[key] = (time.time() + 3600, results)
    return results


# ───────────── индекс Objaverse: uid -> путь GLB в датасете ─────────────
_index_lock = threading.Lock()


def _index_db(cache_dir: Path) -> sqlite3.Connection:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / "objaverse-paths.sqlite"
    with _index_lock:
        if not path.exists():
            log.warning("скачиваю индекс Objaverse (один раз)")
            with tempfile.NamedTemporaryFile(delete=False, dir=cache_dir) as tmp, _get(HF + "object-paths.json.gz", timeout=300) as r:
                while chunk := r.read(1 << 20):
                    tmp.write(chunk)
            paths = json.loads(gzip.decompress(Path(tmp.name).read_bytes()))
            Path(tmp.name).unlink(missing_ok=True)
            part = path.with_suffix(".part")
            part.unlink(missing_ok=True)
            con = sqlite3.connect(part)
            con.execute("CREATE TABLE p (uid TEXT PRIMARY KEY, path TEXT NOT NULL)")
            con.executemany("INSERT INTO p VALUES (?,?)", paths.items())
            con.commit()
            con.close()
            part.replace(path)
    return sqlite3.connect(path)


def mirror_paths(uids: list[str], cache_dir: Path) -> dict[str, str]:
    con = _index_db(cache_dir)
    try:
        marks = ",".join("?" * len(uids))
        return dict(con.execute(f"SELECT uid, path FROM p WHERE uid IN ({marks})", uids)) if uids else {}
    finally:
        con.close()


# ───────────── файл ─────────────
def verify_glb(b: bytes) -> None:
    import struct
    if (len(b) < 20 or b[:4] != b"glTF" or struct.unpack_from("<I", b, 4)[0] != 2 or struct.unpack_from("<I", b, 8)[0] != len(b)
            or b[16:20] != b"JSON"):
        raise ValueError("Некорректный GLB")
    doc = json.loads(b[20:20 + struct.unpack_from("<I", b, 12)[0]])
    external = [x for x in (doc.get("images") or []) + (doc.get("buffers") or []) if x.get("uri") and not x["uri"].startswith("data:")]
    if not doc.get("meshes") or external:
        raise ValueError("Файл не содержит автономной модели")


def glb_dims(b: bytes) -> tuple[float, float, float] | None:
    """Габариты сцены GLB (по границам POSITION и преобразованиям узлов), по убыванию. None — не удалось определить."""
    import struct
    doc = json.loads(b[20:20 + struct.unpack_from("<I", b, 12)[0]])
    nodes, meshes, acc = doc.get("nodes") or [], doc.get("meshes") or [], doc.get("accessors") or []
    ident = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]

    def mul(a, b):  # матрицы 4x4 по столбцам (как в glTF)
        return [sum(a[k * 4 + r] * b[c * 4 + k] for k in range(4)) for c in range(4) for r in range(4)]

    def local(n):
        if "matrix" in n:
            return n["matrix"]
        x, y, z, w = n.get("rotation", [0, 0, 0, 1])
        sx, sy, sz = n.get("scale", [1, 1, 1])
        tx, ty, tz = n.get("translation", [0, 0, 0])
        return [(1 - 2 * (y * y + z * z)) * sx, 2 * (x * y + z * w) * sx, 2 * (x * z - y * w) * sx, 0,
                2 * (x * y - z * w) * sy, (1 - 2 * (x * x + z * z)) * sy, 2 * (y * z + x * w) * sy, 0,
                2 * (x * z + y * w) * sz, 2 * (y * z - x * w) * sz, (1 - 2 * (x * x + y * y)) * sz, 0, tx, ty, tz, 1]

    lo, hi = [1e30] * 3, [-1e30] * 3

    def walk(i, parent, depth=0):
        if depth > 64:
            return
        n = nodes[i]
        m = mul(parent, local(n))
        for prim in (meshes[n["mesh"]].get("primitives") or []) if "mesh" in n else []:
            a = acc[prim["attributes"]["POSITION"]]
            if not (a.get("min") and a.get("max")):
                continue
            for cx in (a["min"][0], a["max"][0]):
                for cy in (a["min"][1], a["max"][1]):
                    for cz in (a["min"][2], a["max"][2]):
                        for k in range(3):
                            v = m[k] * cx + m[4 + k] * cy + m[8 + k] * cz + m[12 + k]
                            lo[k], hi[k] = min(lo[k], v), max(hi[k], v)
        for c in n.get("children") or []:
            walk(c, m, depth + 1)

    try:
        for root in doc["scenes"][doc.get("scene", 0)]["nodes"]:
            walk(root, ident)
    except (KeyError, IndexError, TypeError):
        return None
    dims = sorted((h - l for l, h in zip(lo, hi)), reverse=True)
    return tuple(dims) if dims[2] > 0 else None


def looks_like_car(b: bytes) -> bool:
    """Одна машина, а не сцена с дорогой, двумя машинами или россыпью деталей: пропорции длина/ширина/высота."""
    d = glb_dims(b)
    if not d:
        return False
    length, mid, low = d
    return 1.6 <= length / mid <= 3.4 and 2.0 <= length / low <= 4.8 and mid / low <= 2.3


def _read_limited(resp) -> bytes:
    size = int(resp.headers.get("content-length") or 0)
    if size > MAX_BYTES:
        raise ValueError("GLB слишком большой")
    chunks, total = [], 0
    while chunk := resp.read(1 << 20):
        total += len(chunk)
        if total > MAX_BYTES:
            raise ValueError("GLB слишком большой")
        chunks.append(chunk)
    return b"".join(chunks)


def download_glb(uid: str, mirror_path: str | None, dest: Path) -> int:
    """Скачивает GLB в dest. Возвращает размер. Токен Sketchfab (если есть) — официальный путь, иначе зеркало."""
    token = os.getenv("SKETCHFAB_TOKEN")
    url = None
    if token:
        info = _json(f"{API}/models/{uid}/download", headers={"Authorization": f"Token {token}"})
        url = (info.get("glb") or {}).get("url")
    if not url:
        if not mirror_path or not re.fullmatch(rf"glbs/\d+-\d+/{uid}\.glb", mirror_path):
            raise LookupError("файл модели недоступен")
        url = HF + mirror_path
    with _get(url, timeout=120) as r:
        data = _read_limited(r)
    verify_glb(data)
    if not looks_like_car(data):
        raise ValueError("в файле не одна машина (сцена, детали или странные пропорции)")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    tmp.write_bytes(data)
    tmp.replace(dest)
    return len(data)


BAD_SOURCE = re.compile(r"(?i)(scan|photogramm|realitycapture|polycam|meshroom|wreck|rust|abandon|destroyed|crash|damaged|burnt|junk|^\s*\d{2,4}\s*:|[68]x[68]|monster|lifted|drift|rally|police|taxi|ambulance|widebody|tuned|tuning|custom|kit\b)")


def good_quality(m: dict) -> bool:
    """Отсекает модели, которые выглядят плохо: фотосканы, разбитые/ржавые машины, слишком грубая геометрия."""
    name = m.get("name") or ""
    return not BAD_SOURCE.search(name) and not re.search(r"\b(low ?poly|psx|stylized|cartoon|toy|minecraft|lego|roblox)\b", normal(name)) and (m.get("faceCount") or 0) >= 20_000


ERA_WORDS = re.compile(r"\b(?:19\d0s|[5-9]0s|classic|vintage|retro|antique|old)\b", re.I)
# поколения, которые точно современные: BMW F/G, Mercedes W2xx/X2xx, Audi B8/B9/C7/C8, VW Mk7/Mk8, Kia/Hyundai K5/DN8
MODERN_GEN = re.compile(r"\b(?:w2\d\d|x2\d\d|[fg][0-4]\d|b[89]|c[78]|mk[78]|k5|dn8|dl3)\b", re.I)


def era_ok(m: dict, year) -> bool:
    """Подходит ли замена по возрасту: в названии есть год в пределах 12 лет от года объявления или известное современное поколение.
    Без таких признаков («Toyota Corolla (KE80)», «crown 90s») замену не берём."""
    name = m.get("name") or ""
    if ERA_WORDS.search(name):
        return False
    ys = [int(y) for y in re.findall(r"(?:19|20)\d{2}", name)]
    if ys:
        return any((year or 2020) - 12 <= y <= (year or 2020) + 3 for y in ys)
    return bool(MODERN_GEN.search(name))


def find_and_download(car: dict, out_dir: Path, cache_dir: Path, allow_nc: bool = True) -> dict | None:
    """Ищет модель машины, скачивает GLB в out_dir. None — подходящей скачиваемой модели нет."""
    year = car.get("year")
    last_error = None
    for name in names_for(car):
        pool, seen = [], set()
        for q in dict.fromkeys([name, f"{name} {year}" if year else name]):
            try:
                for m in search(q):
                    if m["uid"] not in seen:
                        seen.add(m["uid"])
                        pool.append(m)
            except Exception as e:
                last_error = e
        pool = [m for m in pool if m.get("isDownloadable") and license_ok(m.get("license"), allow_nc)]
        if not os.getenv("SKETCHFAB_TOKEN") and pool:   # без токена нужна копия модели в зеркале
            have = mirror_paths([m["uid"] for m in pool], cache_dir)
            pool = [m for m in pool if m["uid"] in have]
        else:
            have = {}
        pool = [m for m in pool if good_quality(m)]
        if car.get("fallback"):           # замена другой моделью: только современная и заметно проработанная
            pool = [m for m in pool if era_ok(m, year) and (m.get("likeCount") or 0) >= 10 and (m.get("faceCount") or 0) >= 60_000]
        while pool:
            sel = best_model(pool, name, year if not car.get("fallback") else None, car.get("body"))
            if not sel:
                break
            try:
                detail = _json(f"{API}/models/{sel['uid']}")
            except Exception as e:
                last_error = e
                break
            lic = detail.get("license") or {}
            full = {"name": detail.get("name") or "", "faceCount": detail.get("faceCount") or sel.get("faceCount")}
            if not good_quality(full) or (car.get("fallback") and not era_ok(full, year)):     # в поиске название короче, чем на странице модели («… Low Poly», «(1980)»)
                pool = [m for m in pool if m["uid"] != sel["uid"]]
                continue
            if detail.get("isDownloadable") and re.match(r"https?://creativecommons\.org/(?:licenses|publicdomain)/", lic.get("url") or ""):
                dest = out_dir / f"{sel['uid']}.glb"
                try:
                    size = dest.stat().st_size if dest.exists() else download_glb(sel["uid"], have.get(sel["uid"]), dest)
                except (ValueError, LookupError, OSError) as e:
                    log.warning("модель %s пропущена: %s", sel["uid"], e)
                else:
                    user = detail.get("user") or {}
                    return {"uid": sel["uid"], "title": detail.get("name") or sel["name"],
                            "author": user.get("displayName") or user.get("username") or "Sketchfab",
                            "license": lic.get("label"), "license_url": lic.get("url"),
                            "license_code": license_code(lic.get("url"), lic.get("label")),
                            "source_url": f"https://sketchfab.com/models/{sel['uid']}", "size": size, "file": f"{sel['uid']}.glb"}
            pool = [m for m in pool if m["uid"] != sel["uid"]]
    if last_error and not isinstance(last_error, LookupError):
        raise RuntimeError(f"Sketchfab недоступен: {last_error}")
    return None
