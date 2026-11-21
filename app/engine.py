"""Связка с движком «парсинка» (Node, отдельный сервис): живые объявления Drom/FRESH по моделям, подобранным здесь.

Почему не «как есть»: смысловой поиск движка на «супер дорогую крутую машину» выдавал Toyota Comfort 2001 года
(зацепился за слово «комфорт»). Поэтому роли разделены:
  1. AutoChoice понимает запрос (профиль: премиум, мощная, семья, бюджет, кузов…) и выбирает МОДЕЛИ из 182 моделей,
     реально продающихся на рынке (market.json движка), по их настоящим категориям DBpedia (люкс, флагман, спортседан…);
  2. движок находит по этим моделям живые объявления: цена, год, двигатель, мощность, коробка, привод, пробег, фото,
     а каталог Drom — клиренс, длину, багажник, места, расход;
  3. AutoChoice ранжирует объявления по профилю (бюджет — жёстко) и показывает машину в 3D: модель ищется по
     названию из объявления, автоматически разбивается на детали, подписи и тест на лося берут данные объявления.
"""
import hashlib
import json
import math
import os
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

from . import brands, models_text, part_facts
from .recommender import explain, score

FAST_CARDS = int(os.getenv("PARSER_FAST_CARDS", "4"))     # сколько объявлений с модели разбирать подробно: меньше — быстрее
PARSER_URL = os.getenv("PARSER_URL", "http://localhost:3100")
PARSER_DIR = Path(os.getenv("PARSER_DIR", r"C:\Users\1\Desktop\парсинк"))
PREMIUM_BRANDS = {"mercedes-benz", "bmw", "audi", "lexus", "porsche", "land_rover", "genesis", "infiniti", "volvo",
                  "cadillac", "jaguar", "maserati", "bentley", "acura"}
BRAND_NAMES = {"bmw": "BMW", "mercedes-benz": "Mercedes-Benz", "volkswagen": "Volkswagen", "lada": "Lada", "land_rover": "Land Rover"}
BUDGET_BRANDS = {"lada", "renault", "hyundai", "kia", "chevrolet", "nissan", "geely", "haval", "volkswagen", "skoda", "datsun", "daewoo"}
# пожелание профиля -> признаки модели в её реальных категориях (DBpedia)
NEED_CATS = {      # (признак, вес): точные категории весят больше общих
    "premium": [(r"flagship|limousine|full-size vehicles|luxury sport utility|grand tourer|представительск", 1.3), (r"luxury|executive", 0.6)],
    "powerful": [(r"sports (sedan|car)|grand tourer|performance|hot hatch|muscle|спортив", 1.0), (r"coupé|convertible", 0.5)],
    "family": [(r"minivan|mpv|station wagon|large family|семейн", 1.2), (r"full-size|mid-size sport utility|mid-size cars", 0.6)],
    "big_trunk": [(r"station wagon|minivan|mpv|large family", 0.9), (r"full-size|mid-size sport utility", 0.4)],
    "offroad": [(r"off-road|sport utility|four-wheel|внедорож", 1.3)],
    "city": [(r"subcompact|city car|supermini|kei car|mini mpv", 1.0), (r"compact cars", 0.5)],
    "comfort": [(r"executive|full-size|luxury|large family", 0.8), (r"mid-size cars", 0.4)],
    "low_consumption": [(r"hybrid|electric", 0.9), (r"subcompact|supermini", 0.5)],
    "first_car": [(r"subcompact|compact cars|supermini", 0.6)],
    "cheap": [(r"subcompact|supermini|cars of russia|taxi", 0.4)],
}
BODY_CATS = {"sedan": r"sedan", "hatch": r"hatchback", "suv": r"sport utility|off-road", "wagon": r"station wagon",
             "minivan": r"minivan|mpv"}
NEGATIVE_FOR_PREMIUM = r"taxi|police|subcompact|supermini|kei car|cars of russia"


@lru_cache(maxsize=1)
def market_models() -> list[dict]:
    """Модели, которые реально продаются (по снимку рынка движка), с их категориями."""
    cat = json.loads((PARSER_DIR / "catalog" / "models.json").read_text(encoding="utf-8"))["models"]
    counts = json.loads((PARSER_DIR / "catalog" / "market.json").read_text(encoding="utf-8"))["counts"]
    by_path = {m["path"]: m for m in cat}
    out = []
    for path, n in counts.items():
        brand, _, model = path.partition("/")
        m = by_path.get(path)
        title = m["title"] if m else f"{BRAND_NAMES.get(brand, brand.replace('_', ' ').title())} {model.replace('_', ' ').replace('-', ' ').upper() if len(model) <= 5 else model.replace('_', ' ').replace('-', ' ').title()}"
        out.append({"path": path, "brand": brand, "title": title, "desc": (m["description"] if m else "").lower(), "count": n})
    return out


def _tokens(s: str) -> set:
    return set(re.sub(r"[^a-z0-9]+", " ", s.lower().replace("mercedes-benz", "mercedes benz")).split())


def match_market(name: str) -> dict | None:
    """Название модели («Mercedes-Benz S-Class», «BMW X5») -> модель со снимка рынка; None — на рынке её нет."""
    want = _tokens(name)
    best = None
    for m in market_models():
        have = _tokens(m["path"].replace("/", " ").replace("_", " ")) | _tokens(m["title"])
        if want and want <= have | {"class", "series"} and (best is None or len(have) < len(best[1])):
            best = (m, have)
    return best[0] if best else None


def candidate_models(profile: dict, k: int = 3, exclude: set | None = None, suggested: list[str] | None = None) -> list[dict]:
    """Модели под профиль: категории модели совпадают с пожеланиями, жёсткие условия (кузов, полный привод) соблюдены."""
    nice, hard = profile.get("nice", {}), profile.get("hard", {})
    want = set(hard.get("brands") or [])                 # клиент назвал марку — только она, никакой подмены
    named = list(hard.get("models") or [])               # назвал модель — только она
    if named:
        by_path = {m["path"]: m for m in market_models()}
        out = [by_path.get(p) or {"path": p, "brand": p.partition("/")[0], "title": f"{brands.title_of(p.partition('/')[0])} {models_text.model_title(p)}",
                                  "desc": "", "count": 0, "external": True} for p in named if not (exclude and p in exclude)]
        if out:
            return out[:k]
    if want:
        suggested = [n for n in suggested or [] if brands.slug_of(n) in want]
    scored = []
    for m in market_models():
        if exclude and m["path"] in exclude:
            continue
        if want and m["brand"] not in want:
            continue
        d = m["desc"]
        if hard.get("body") and not any(re.search(BODY_CATS[b], d) for b in hard["body"] if b in BODY_CATS):
            continue
        if hard.get("drive") == "awd" and d and not re.search(r"all-wheel|four-wheel|off-road", d):
            continue
        s = 0.0
        for key, w in nice.items():
            for rx, k_w in NEED_CATS.get(key, []):
                if re.search(rx, d):
                    s += w * k_w
                    break
        if "premium" in nice:
            s += 1.2 * nice["premium"] * (m["brand"] in PREMIUM_BRANDS) - 1.0 * bool(re.search(NEGATIVE_FOR_PREMIUM, d))
        if "cheap" in nice or "first_car" in nice:
            s += 0.6 * (m["brand"] in BUDGET_BRANDS) - 0.8 * (m["brand"] in PREMIUM_BRANDS)
        s += 0.08 * math.log10(1 + m["count"])          # популярные модели — больше живых объявлений
        scored.append((s, m))
    scored.sort(key=lambda x: -x[0])
    out, used = [], set()
    allowed = {m["path"] for _, m in scored}            # жёсткие условия уже применены в scored
    nice = profile.get("nice", {})
    for name in suggested or []:                         # сначала модели, которые назвала нейросеть, если они есть на рынке
        m = match_market(name)
        if not m or m["path"] not in allowed or m in out:
            continue
        if "premium" in nice and m["brand"] in BUDGET_BRANDS or ("cheap" in nice and m["brand"] in PREMIUM_BRANDS):
            continue                                     # явное противоречие запросу — отбрасываем
        out.append(m)
        used.add(m["brand"])
        if len(out) >= k:
            return out
    off = 0                                              # модели вне снимка рынка (суперкары и т.п.): ищем их на Drom напрямую
    for name in suggested or []:
        if off >= 2 or len(out) >= k + 1 or match_market(name):
            continue
        brand = name.split()[0].lower()
        if ("premium" in nice and brand in BUDGET_BRANDS) or ("cheap" in nice and brand in PREMIUM_BRANDS) or (brand in used and not want):
            continue
        out.append({"path": brand + "/" + "_".join(name.lower().split()[1:]), "brand": brand, "title": name, "desc": "", "count": 0, "external": True})
        used.add(brand)
        off += 1
    for slug in sorted(want - used):                      # марки нет в снимке рынка: ищем на Drom всю марку целиком
        out.append({"path": slug, "brand": slug, "title": brands.title_of(slug), "desc": "", "count": 0, "external": True})
        used.add(slug)
    for s, m in scored:                                  # разные марки в первой тройке
        if m["brand"] in used and len(out) < k and not want:
            continue
        if m in out:
            continue
        out.append(m)
        used.add(m["brand"])
        if len(out) >= k:
            break
    return out


def _post(path: str, payload: dict, timeout: int = 300) -> dict:
    req = urllib.request.Request(PARSER_URL + path, data=json.dumps(payload, ensure_ascii=False).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def available() -> bool:
    try:
        with urllib.request.urlopen(PARSER_URL + "/health", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def _listings_for(model: dict, profile: dict, mode: str) -> list[dict]:
    hard = profile.get("hard", {})
    seats = max(2, int(hard.get("seats_min") or 2))
    answers = [{"field": "model", "label": "Модель", "value": model["title"][:100], "priority": "must"},
               {"field": "seats", "label": "Места", "value": f"{seats} мест", "priority": "want"}]   # включает сбор характеристик из каталога Drom
    if model.get("external") or hard.get("brands"):   # раздел марки/модели на Drom напрямую — без подмешивания других марок
        path = model["path"].strip("/")
        if re.fullmatch(r"[a-z0-9_-]+(?:/[a-z0-9_-]+)?", path):
            answers.append({"field": "drom_path", "label": "Раздел Drom", "value": path, "priority": "want"})
    answers.append({"field": "fast", "label": "Быстрый режим", "value": str(FAST_CARDS), "priority": "want"})   # Drom+Fresh, N карточек
    if hard.get("budget_max"):
        answers.append({"field": "budget", "label": "Бюджет", "value": f"До {int(hard['budget_max']):,} ₽".replace(",", " "), "priority": "must"})
    try:
        res = _post("/api/search", {"description": model["title"][:600], "mode": mode, "answers": answers, "asked": [], "phase": "initial"})
    except Exception:
        return []
    return [dict(r, model=model) for r in res.get("results", [])]


# ───────────── объявление -> карточка машины ─────────────
def _num(s) -> float | None:
    m = re.search(r"\d+(?:[.,]\d+)?", str(s or "").replace(" ", "").replace("\u00a0", ""))
    return float(m.group(0).replace(",", ".")) if m else None


BODY_RU = {"седан": "sedan", "хэтчбек": "hatch", "лифтбек": "hatch", "универсал": "wagon", "джип": "suv", "внедорожник": "suv",
           "кроссовер": "suv", "минивэн": "minivan", "микроавтобус": "minivan", "купе": "sedan", "открытый": "sedan", "пикап": "suv"}
FUEL_RU = {"бенз": "petrol", "дизел": "diesel", "гибрид": "hybrid", "электр": "electric"}
COLOR_RU = {"бел": "white", "черн": "black", "чёрн": "black", "красн": "red", "син": "blue", "сер": "gray", "серебр": "silver",
            "зелен": "green", "зелён": "green", "желт": "yellow", "жёлт": "yellow", "оранж": "orange", "коричн": "brown", "бордов": "red"}


def _pick(table: dict, s: str):
    s = (s or "").lower()
    return next((v for k, v in table.items() if k in s), None)


def to_car(r: dict) -> dict:
    """Объявление движка -> словарь машины в формате AutoChoice (все значения — из объявления и каталога Drom)."""
    f, m, model = r.get("facts") or {}, r.get("measurements") or {}, r["model"]
    raw = str(r.get("raw") or "")
    gear = (f.get("gearbox") or "").lower()
    drive = (f.get("drive") or "").lower()
    body = _pick(BODY_RU, f.get("body")) or _pick(BODY_RU, model["desc"]) or ("suv" if re.search(r"sport utility|off-road", model["desc"]) else "sedan")
    year = int(_num(f.get("year")) or 0) or None
    power = int(_num(f.get("power")) or 0) or None
    # название — из самого объявления (Drom мог вернуть не ту модель, что искали): «Продажа Mercedes-Benz S-Class, 2025 год в Москве»
    title = re.split(r",\s*(?:19|20)\d{2}|\s+(?:19|20)\d{2}\s*год", re.sub(r"^Продажа\s+", "", r.get("title") or ""))[0].strip() or model["title"]
    seg = [x for x in urllib.parse.urlparse(r.get("link") or "").path.split("/") if x]
    path = "/".join(seg[1:3]) if r.get("source") == "drom" and len(seg) >= 4 else model["path"]
    seats = int(m.get("seats") or 0) or None
    car = {
        "id": "m_" + hashlib.sha1(r["link"].encode()).hexdigest()[:12], "market": True, "title": title,
        "brand": title.split()[0], "model": " ".join(title.split()[1:]),
        "trim": " · ".join(x for x in [f.get("engine"), (f.get("gearbox") or "").lower(), f"{f.get('mileage')} км" if f.get("mileage") else ""] if x),
        "year": year, "body": body, "seats": seats, "trunk_l": int(m["boot"]) if m.get("boot") else None, "power_hp": power,
        "consumption_l100": m.get("mixed") or m.get("city"), "price": int(r.get("price") or 0) or None,
        "drive": "awd" if re.search(r"полн|4wd|awd", drive) else "rwd" if "задн" in drive else "fwd" if "передн" in drive else None,
        "transmission": "manual" if "механ" in gear else "auto" if gear else None, "fuel": _pick(FUEL_RU, f.get("fuel")),
        "colors": [c for c in [_pick(COLOR_RU, f.get("color"))] if c] or ["gray"], "tags": [],
        "listing_url": r.get("link"), "source": r.get("source"), "image_url": r.get("image"), "images": r.get("images") or [],
        "model_path": path, "searched_for": model["title"], "asset": r.get("assetQuery") or {"name": title.lower(), "year": str(year or ""), "body": ""},
        "specs": {"clearance_mm": int(m["clearance"]) if m.get("clearance") else None, "length_mm": int(m["length"]) if m.get("length") else None,
                  "right_hand": "руль правый" in raw.lower(), "mileage_km": int(_num(f.get("mileage")) or 0) or None},
        "facts": f, "description": (r.get("description") or "")[:300],
    }
    spec = r.get("spec") or {}
    for k, v in part_facts.parse_spec(spec).items():       # таблица комплектации Drom: масса, разгон, бак, колея...
        if not car["specs"].get(k):
            car["specs"][k] = v
    if spec:
        car["specs"]["source"] = "каталог Drom: " + (r.get("specURL") or "")
    car["spec_table"] = spec
    car["hotspots"] = part_facts.part_texts(car, f, spec)
    return car


def enrich(car: dict) -> dict:
    """Чего нет в каталоге Drom (масса, размеры) — из Википедии; подписи деталей пересобираются."""
    s = car["specs"]
    if not s.get("weight_kg") or not s.get("length_mm"):
        w = part_facts.wiki_specs(car["title"])
        for k, v in w.items():
            if k != "source" and not s.get(k):
                s[k] = v
        if w.get("source"):
            s["weight_source"] = w["source"]
    car["hotspots"] = part_facts.part_texts(car, car.get("facts") or {}, car.get("spec_table") or {})
    return car


def hotspots(car: dict, f: dict) -> dict:
    """Подписи деталей 3D-модели — только реальные данные этого объявления."""
    s = car["specs"]
    out = {}
    eng = " · ".join(x for x in [f.get("engine"), f"{car['power_hp']} л.с." if car["power_hp"] else "", f.get("fuel")] if x)
    if eng:
        out["hood"] = f"Двигатель: {eng}"
    if car["drive"] or f.get("drive"):
        for w in ("wheel_front_left", "wheel_front_right", "wheel_rear_left", "wheel_rear_right"):
            out[w] = f"Привод: {f.get('drive') or car['drive']}" + (f" · клиренс {s['clearance_mm']} мм" if s.get("clearance_mm") else "")
    if f.get("gearbox"):
        out["windshield"] = f"Коробка: {f['gearbox']}" + (" · руль правый" if s.get("right_hand") else "")
    if car["price"]:
        out["roof"] = f"Цена по объявлению: {car['price']:,} ₽".replace(",", " ") + (f" ({car['source']})" if car.get("source") else "")
    if f.get("mileage") or car["year"]:
        out["door_front_left"] = out["door_front_right"] = " · ".join(x for x in [f"{car['year']} г." if car["year"] else "", f"пробег {f['mileage']} км" if f.get("mileage") else ""] if x)
    if car["trunk_l"]:
        out["trunk_lid"] = f"Багажник {car['trunk_l']} л"
    if car["seats"]:
        out["door_rear_left"] = out["door_rear_right"] = f"Мест: {car['seats']}"
    if car["consumption_l100"]:
        out["fender_front_left"] = out["fender_front_right"] = f"Расход {car['consumption_l100']} л/100 км"
    if f.get("color"):
        out["body_paint"] = f"Цвет: {f['color']}"
    if s.get("length_mm"):
        out["front_bumper"] = out["rear_bumper"] = f"Длина кузова {s['length_mm']} мм"
    return out


# ───────────── подбор ─────────────
def recommend_market(profile: dict, mode: str, exclude: set | None = None, description: str = "") -> dict | None:
    """Живые объявления под профиль. None — движок недоступен или ничего не нашёл (тогда работает каталог)."""
    if not available():
        return None
    t0 = time.time()
    from . import llm
    known = ", ".join([*profile.get("nice", {}), *[f"{k}={v}" for k, v in profile.get("hard", {}).items()]])
    named = bool(profile.get("hard", {}).get("models"))
    # Локальная 1.5B-модель не понимает смысл описания и выдаёт один и тот же
    # список для разных запросов. Модели берём из каталога по профилю и тегам —
    # это воспроизводимо и зависит от того, что действительно сказал клиент.
    suggested = [] if named else []
    models = candidate_models(profile, 3, exclude, suggested)
    with ThreadPoolExecutor(len(models) or 1) as ex:
        found = [c for lst in ex.map(lambda m: _listings_for(m, profile, mode), models) for c in lst]
    cars, seen = [], set()
    for r in found:
        if r.get("link") in seen or not r.get("price"):
            continue
        seen.add(r["link"])
        cars.append(to_car(r))
    hard = profile.get("hard", {})
    if hard.get("brands"):                              # Drom может подмешать другие марки — убираем
        cars = [c for c in cars if brands.slug_of(c["title"]) in hard["brands"]]
    if hard.get("models"):                              # и другие модели той же марки (X7 вместо X5)
        cars = [c for c in cars if c["model_path"] in hard["models"] or any(models_text.title_matches(c["title"], p) for p in hard["models"])]
    if hard.get("budget_max"):
        cars = [c for c in cars if c["price"] <= hard["budget_max"]]
    if hard.get("transmission"):
        cars = [c for c in cars if c["transmission"] in (None, hard["transmission"])]
    if not cars:
        return None
    ranked = sorted(((score(c, profile, cars)[0], c) for c in cars), key=lambda x: (-x[0], -(x[1]["year"] or 0)))
    best_s, best = ranked[0]
    parts = score(best, profile, cars)[1]
    alternatives, used = [], {best["model_path"]}
    rest = [x for x in ranked[1:] if x[1]["model_path"] != best["model_path"]] + [x for x in ranked[1:] if x[1]["model_path"] == best["model_path"]]
    nice = profile.get("nice", {})
    for s, c in rest:                                   # сначала другие модели, потом другие экземпляры той же
        if len(alternatives) >= 4:
            break
        if "premium" in nice and (c["price"] < 0.3 * best["price"] or (c.get("model_path") or "").split("/")[0] in BUDGET_BRANDS):
            continue                                    # для запроса «дорогая» дешёвые варианты не предлагаем
        alternatives.append({"id": c["id"], "title": f"{c['title']} {c['year'] or ''}".strip(), "score": s,
                             "diff": f"{c['price']:,} ₽".replace(",", " ") + (" · другая модель" if c["model_path"] not in used else ""), "car": c})
        used.add(c["model_path"])
    enrich(best)                                           # масса и размеры из Википедии, если Drom их не дал (только для показанной машины)
    return {"car": best, "score": best_s, **explain(best, parts, profile), "alternatives": alternatives, "uncertain": [],
            "empty": False, "source": "market", "models": [m["title"] for m in models], "suggested": suggested, "collected": len(cars), "seconds": round(time.time() - t0)}


# ───────────── тест на лося для машины из объявления ─────────────
BODY_DEFAULTS = {"sedan": (1450, 1.53, 150), "hatch": (1300, 1.50, 150), "wagon": (1500, 1.54, 150), "suv": (1750, 1.60, 190), "minivan": (1700, 1.56, 160)}


def physics_specs(car: dict) -> dict:
    """Входы физической модели теста на лося: что известно — из объявления, каталога Drom и 3D-модели; остальное — оценка по кузову."""
    s = dict(car.get("specs") or {})
    meas = s.get("measured") or {}
    weight, track, clearance = BODY_DEFAULTS.get(car.get("body"), BODY_DEFAULTS["sedan"])
    sources = []
    if meas.get("track_m"):
        s["track_mm"] = int(meas["track_m"] * 1000)
        sources.append("колея измерена по 3D-модели")
    else:
        s["track_mm"] = s.get("track_mm") or int(track * 1000)
    if not s.get("clearance_mm"):
        s["clearance_mm"] = clearance
    else:
        sources.append("клиренс из каталога Drom")
    s.setdefault("weight_kg", weight)
    s.setdefault("esp", (car.get("year") or 2015) >= 2012)          # в РФ ESP массово с 2012-2014 гг.
    s.setdefault("airbags", 6 if (car.get("year") or 2015) >= 2010 else 2)
    s.setdefault("chassis", "premium" if car.get("brand", "").lower() in {b.split("_")[0] for b in PREMIUM_BRANDS} else "modern")
    s.setdefault("moose_real", None)
    s.setdefault("crash_real", None)
    s["physics_sources"] = sources
    return s
