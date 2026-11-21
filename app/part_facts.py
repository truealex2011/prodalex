"""Реальные данные для подписей деталей 3D-модели.

Источники (по убыванию точности):
  1. объявление (движок «парсинка»): двигатель, коробка, привод, пробег, цвет, цена;
  2. таблица характеристик комплектации из каталога Drom (её уже открывает движок): масса, шины, тормоза, подвеска,
     бак, разгон, рулевое, оснащение салона;
  3. Википедия (карточка модели) — когда в каталоге Drom комплектация не нашлась: масса и размеры.
Ничего не придумывается: деталь без данных получает только описание «зачем она» (это делает просмотрщик).
"""
import json
import logging
import re
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)
NO = {"", "—", "-", "–", "нет", "no"}
UA = {"User-Agent": "AutoChoice/1.0 (car selection demo)"}


def _n(s) -> float | None:
    m = re.search(r"\d+(?:[.,]\d+)?", str(s or "").replace(" ", "").replace(" ", ""))
    return float(m.group(0).replace(",", ".")) if m else None


def _get(spec: dict, pattern: str) -> str | None:
    """Значение первой строки таблицы, чьё название подходит под шаблон; «—» = нет."""
    rx = re.compile(pattern, re.I)
    for k, v in spec.items():
        if rx.search(k):
            v = re.sub(r"\s*(Отзывы о шинах|Купить шины).*$", "", str(v)).strip()
            return None if v.lower() in NO else v
    return None


def _has(spec: dict, pattern: str) -> list[str]:
    """Названия опций оснащения, которые у комплектации есть (значение не «—»)."""
    rx = re.compile(pattern, re.I)
    out = []
    for k, v in spec.items():
        if rx.search(k) and str(v).strip().lower() not in NO:
            v = str(v).strip()
            if v.lower() == "опция":
                continue                                 # опция — у этого экземпляра может не быть
            out.append(k if v.lower() in ("✓", "+", "да", "есть", "✔") else f"{k}: {v}")
    return out


def parse_spec(spec: dict) -> dict:
    """Числа из таблицы Drom — во входы карточки и физики теста на лося."""
    dims = _get(spec, r"^Габариты кузова")
    d = [int(x) for x in re.findall(r"\d{3,5}", dims or "")]
    out = {
        "weight_kg": _n(_get(spec, r"^Масса, кг|^Снаряженная масса")), "accel_0_100": _n(_get(spec, r"разгона 0-100")),
        "top_speed": _n(_get(spec, r"^Максимальная скорость")), "tank_l": _n(_get(spec, r"топливного бака")),
        "clearance_mm": _n(_get(spec, r"^Клиренс")), "wheelbase_mm": _n(_get(spec, r"^Колесная база")),
        "track_mm": _n(_get(spec, r"передней колеи")), "turn_radius_m": _n(_get(spec, r"радиус разворота")),
        "length_mm": d[0] if len(d) == 3 else None, "width_mm": d[1] if len(d) == 3 else None, "height_mm": d[2] if len(d) == 3 else None,
        "torque_nm": _n(_get(spec, r"крутящий момент")),
    }
    return {k: (int(v) if isinstance(v, float) and v.is_integer() else v) for k, v in out.items() if v}


# ───────────── Википедия: масса и размеры, если каталог Drom не дал ─────────────
_wiki_lock = threading.Lock()


def _cache_file() -> Path:
    p = Path(__file__).resolve().parent.parent / "data" / "cache" / "wiki_specs.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _json(url: str):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


def wiki_specs(title: str, year=None) -> dict:
    """Карточка модели в Википедии (ru, затем en): масса, длина, ширина, высота, колёсная база. Кэш на диске."""
    key = f"{title.lower().strip()}|{year or ''}"
    with _wiki_lock:
        cache = json.loads(_cache_file().read_text(encoding="utf-8")) if _cache_file().exists() else {}
    if key in cache and time.time() - cache[key].get("at", 0) < 30 * 86400:
        return cache[key]["data"]
    data, failed = {}, False
    want =[t for t in re.split(r"[\s\-]+", title.lower()) if t]
    relevant = lambda name: all(t in re.split(r"[\s\-()]+", name.lower()) for t in want)   # noqa: E731  статья именно об этой модели
    for lang, fields in (("en", {"weight_kg": r"curb_weight|weight", "length_mm": r"length", "width_mm": r"width", "height_mm": r"height",
                                 "wheelbase_mm": r"wheelbase"}),
                         ("ru", {"weight_kg": r"масса", "length_mm": r"длина", "width_mm": r"ширина", "height_mm": r"высота",
                                 "wheelbase_mm": r"колёсная база|колесная база"})):
        try:
            api = f"https://{lang}.wikipedia.org/w/api.php?"
            pages = []
            for q in ([f"{title} {year}"] if year else []) + [title]:       # статья о поколении точнее статьи о семействе
                hits = _json(api + urllib.parse.urlencode({"action": "query", "list": "search", "srsearch": q, "srlimit": 4, "format": "json"}))
                pages += [x["title"] for x in hits.get("query", {}).get("search", []) if x["title"] not in pages]
            for page in [x for x in pages if relevant(x)][:4]:
                text = _json(api + urllib.parse.urlencode({"action": "parse", "page": page, "prop": "wikitext", "section": 0,
                                                           "format": "json", "redirects": 1}))["parse"]["wikitext"]["*"]
                found = _infobox(text, fields)
                if len(found) >= 2:
                    data = {**found, "source": f"Википедия ({lang}): {page}"}
                    break
            if data:
                break
        except Exception as e:
            log.info("википедия для %s: %s", title, e)
            failed = True
    if not data and failed:
        return data                                  # сеть или лимит запросов: пустоту не кэшируем, спросим в следующий раз
    with _wiki_lock:
        cache = json.loads(_cache_file().read_text(encoding="utf-8")) if _cache_file().exists() else {}
        cache[key] = {"at": time.time(), "data": data}
        _cache_file().write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return data


def _infobox(text: str, fields: dict) -> dict:
    data = {}
    for k, rx in fields.items():
        if k in data:
            continue
        # значение может занимать несколько строк (списки комплектаций): до следующего поля карточки
        m = re.search(rf"^\s*\|\s*(?:{rx})\s*=\s*(.+?)(?=\n\s*\|\s*[\w ]+=|\n\}}\}}|\Z)", text, re.I | re.M | re.S)
        if not m:
            continue
        val = m.group(1)
        conv = re.search(r"\{\{convert\|([\d.,]+)\|(?:[^|}]*\|)*?(mm|in|m|kg|lb)\b", val, re.I)
        num = conv or re.search(r"(\d[\d\s.,]*)\s*(мм|mm|кг|kg|м\b|m\b|in)?", val)
        if not num:
            continue
        try:
            x = float(re.sub(r"[\s,]", "", num.group(1)).rstrip(".") or 0)
        except ValueError:
            continue
        unit = (num.group(2) or "").lower()
        if unit == "lb":
            x *= 0.4536
        if k == "weight_kg" and 500 < x < 6000:
            data[k] = int(x)
        elif k != "weight_kg":
            mm = x * 1000 if unit in ("м", "m") or x < 10 else x * 25.4 if unit == "in" else x
            if 100 < mm < 7000:
                data[k] = int(mm)
    return data


# ───────────── подписи деталей ─────────────
SIDES = ("left", "right")
ENDS = ("front", "rear")


def _j(*xs) -> str:
    return " · ".join(x for x in xs if x)


def part_texts(car: dict, f: dict, spec: dict) -> dict:
    """{id детали: текст} — у каждой детали только реальные цифры этой машины (объявление, Drom, Википедия)."""
    s = car.get("specs") or {}
    out = {}

    def put(ids, text):
        if text:
            for i in ([ids] if isinstance(ids, str) else ids):
                out[i] = text

    g = lambda rx: _get(spec, rx)                       # noqa: E731
    kg = lambda v, u: f"{v} {u}" if v else ""           # noqa: E731
    # снаружи
    put("hood", _j(f"Двигатель: {f.get('engine')}" if f.get("engine") else "", kg(car.get("power_hp"), "л.с."),
                   kg(s.get("torque_nm"), "Н·м"), g(r"^Тип двигателя"), f"разгон 0–100 за {s['accel_0_100']} с" if s.get("accel_0_100") else ""))
    put("body_paint", _j(f"Масса {s['weight_kg']} кг" if s.get("weight_kg") else "", f"цвет: {f['color']}" if f.get("color") else "",
                         s.get("weight_source", "")))
    put("roof", _j(f"Высота {s['height_mm']} мм" if s.get("height_mm") else "",
                   f"цена {car['price']:,} ₽".replace(",", " ") if car.get("price") else ""))
    put("front_bumper", _j(f"Длина {s['length_mm']} мм" if s.get("length_mm") else "", f"передний свес {g(r'^Передний свес')} мм" if g(r"^Передний свес") else ""))
    put("rear_bumper", _j(f"Задний свес {g(r'^Задний свес')} мм" if g(r"^Задний свес") else "",
                          f"прицеп до {g(r'прицепа с тормозами')} кг" if g(r"прицепа с тормозами") else ""))
    put("trunk_lid", _j(f"Багажник {car['trunk_l']} л" if car.get("trunk_l") else "", f"грузоподъёмность {g(r'грузоподъ')} кг" if g(r"грузоподъ") else ""))
    put("grille", _j(g(r"^Нагнетатель") and f"Нагнетатель: {g(r'^Нагнетатель')}", g(r"Экологический") and f"экокласс {g(r'Экологический')}",
                     g(r"^Используемое топливо")))
    put("exhaust", _j(f"Расход: город {g(r'городском цикле')}" if g(r"городском цикле") else "",
                      f"трасса {g(r'за городом')}" if g(r"за городом") else "", f"смешанный {g(r'смешанном')} л/100 км" if g(r"смешанном") else ""))
    put("wipers", _j(*_has(spec, r"Датчик дождя|подогрев.*(лобов|форсун)")))
    put(["mirror_left", "mirror_right"], _j(*_has(spec, r"зеркал")[:3]))
    put(["headlight_left", "headlight_right"], _j(*_has(spec, r"фар|ходовые огни|Датчик света|освещени")[:3]))
    put(["taillight_left", "taillight_right"], _j(*_has(spec, r"задние фонари")))
    put(["fog_light_left", "fog_light_right"], _j(*_has(spec, r"противотуман")))
    put(["sill_left", "sill_right"], f"Клиренс {s['clearance_mm']} мм" if s.get("clearance_mm") else "")
    put(["fender_front_left", "fender_front_right"], _j(f"Колея {s['track_mm']} мм" if s.get("track_mm") else "",
                                                        f"радиус разворота {s['turn_radius_m']} м" if s.get("turn_radius_m") else ""))
    put(["fender_rear_left", "fender_rear_right"], _j(f"Бак {s['tank_l']} л" if s.get("tank_l") else "", g(r"^Используемое топливо")))
    put(["door_front_left", "door_front_right"], _j(f"{car['year']} г." if car.get("year") else "", f"пробег {f['mileage']} км" if f.get("mileage") else "",
                                                    f"дверей: {g(r'^Число дверей')}" if g(r"^Число дверей") else ""))
    put(["door_rear_left", "door_rear_right"], _j(f"Мест: {car['seats']}" if car.get("seats") else "", *_has(spec, r"Блокировка задних дверей")))
    put(["handle_front_left", "handle_front_right", "handle_rear_left", "handle_rear_right"], _j(*_has(spec, r"бесключ|Кнопка запуска")))
    put(["pillar_a_left", "pillar_a_right", "pillar_c_left", "pillar_c_right"], _j(*_has(spec, r"подушк|шторк")[:3]))
    put(["badge_front", "badge_rear"], _j(f"{car['title']}", g(r"^Марка кузова") and f"кузов {g(r'^Марка кузова')}", g(r"^Страна сборки")))
    for e in ENDS:
        tires = g(r"^Передние колеса" if e == "front" else r"^Задние колеса")
        brakes = g(r"^Передние тормоза" if e == "front" else r"^Задние тормоза")
        susp = g(r"^Передняя подвеска" if e == "front" else r"^Задняя подвеска")
        drive = f.get("drive") or {"awd": "полный", "fwd": "передний", "rwd": "задний"}.get(car.get("drive") or "", "")
        for sd in SIDES:
            put(f"wheel_{e}_{sd}", _j(f"Шины {tires}" if tires else "", f"привод {drive}" if drive else "", susp and f"подвеска: {susp}"))
            put(f"tire_{e}_{sd}", _j(f"Шины {tires}" if tires else "", f"клиренс {s['clearance_mm']} мм" if s.get("clearance_mm") else ""))
            put(f"rim_{e}_{sd}", _j(f"Диски {g(r'^Размер дисков')}" if g(r"^Размер дисков") else "", f"привод {drive}" if drive else ""))
            put(f"brake_{e}_{sd}", _j(f"Тормоза: {brakes}" if brakes else "", e == "rear" and g(r"^Стояночный тормоз") and f"стояночный: {g(r'^Стояночный тормоз')}"))
    # салон
    put("steering_wheel", _j(g(r"^Тип рулевого") and f"Рулевое: {g(r'^Тип рулевого')}", g(r"усилитель руля") and f"усилитель: {g(r'усилитель руля')}",
                             g(r"оборотов руля") and f"{g(r'оборотов руля')} об. от упора до упора", *_has(spec, r"рулевого колеса")[:2],
                             "руль правый" if s.get("right_hand") else ""))
    put("dashboard", _j(*_has(spec, r"Климат|Кондиционер|Декоративная отделка")[:3]))
    put("instrument_cluster", _j(*_has(spec, r"дисплей|приборн|бортовой компьютер")[:2]))
    put("multimedia_screen", _j(*_has(spec, r"ЖК-монитор|навигац|аудиосистем|Apple|Android|мультимедиа")[:3]))
    put("gear_shifter", _j(g(r"^Тип трансмиссии") and f"Коробка: {g(r'^Тип трансмиссии')}", f.get("gearbox") and f"по объявлению: {f['gearbox']}",
                           g(r"^Главная передача") and f"главная передача {g(r'^Главная передача')}"))
    put("handbrake", g(r"^Стояночный тормоз") and f"Стояночный тормоз: {g(r'^Стояночный тормоз')}")
    put("pedals", _j(*_has(spec, r"круиз|Система помощи|Hill|педал")[:2]))
    put("center_console", _j(*_has(spec, r"подлокотник|Кнопка запуска|беспроводн")[:3]))
    put(["seat_driver", "seat_passenger"], _j(*_has(spec, r"передних сидений|водительского сиденья|Спортивные сиденья|вентиляц")[:3]))
    put("seat_rear", _j(f"Мест: {car['seats']}" if car.get("seats") else "", g(r"^Второй ряд сидений") and f"второй ряд {g(r'^Второй ряд сидений')}",
                        *_has(spec, r"задних сидений|подлокотник сзади")[:2]))
    put("door_panels", _j(*_has(spec, r"стеклоподъ|Подсветка при посадке|Комбинированная обивка|Кожан")[:3]))
    put("headliner", _j(*_has(spec, r"люк|панорам|Подсветка салона")[:2]))
    put("floor_carpet", _j(*_has(spec, r"^Коврики")))
    return {k: v for k, v in out.items() if v}
