"""Текст -> изменение профиля.

СЕЙЧАС: заглушка на захардкоженных правилах (ключевые слова и регулярки).
ПОТОМ: сюда подключается наша дообученная модель. Контракт не меняется:
на вход текст, на выход ProfileDelta. Остальной код ничего не заметит.
"""
import re

from . import llm
from .brands import LUXURY, PREMIUM, find_brands
from .models_text import find_models
from .profile import ProfileDelta

_END = r"(?:ая|ый|ой|ую|ого|ее|енькая|енький|еньк\w*)\b"
COLORS = {  # проверяются по порядку, текст уже приведён к «е» вместо «ё»
    rf"красн{_END}": "red", rf"бел{_END}": "white", rf"черн{_END}": "black",
    r"син(?:яя|ий|юю|его|ей|еньк\w*)\b": "blue", rf"голуб{_END}": "blue",
    r"серебрист\w*": "silver", rf"сер{_END}": "gray", rf"зелен{_END}": "green",
    rf"желт{_END}": "yellow", r"оранжев\w*": "orange", r"коричнев\w*": "brown",
}

NICE_RULES = [
    (r"(не|без)\s+(очень\s+|сильно\s+|слишком\s+)?мощн|спокойн", "low_power", 0.5),
    (r"(?<!не )(?<!не очень )мощн|быстр|динамичн|разгон", "powerful", 0.7),
    (r"мало\s+(ест|жр|расх|бенз)|бензина?\s+мало|эконом|расход", "low_consumption", 0.8),
    (r"город|пробк|парков", "city", 0.6),
    (r"бездорож|дач|рыбал|охот|лес|грунт", "offroad", 0.7),
    (r"багаж|вещ|чемодан|коляск", "big_trunk", 0.6),
    (r"путешеств|трасс|дальн|поездк", "comfort", 0.6),
    (r"сем[ьея]|дет[ейи]|ребен", "family", 0.8),
    (r"супер\s*дорог|(?<![а-я])(?<!не\s)дорог(ую|ая|ой|ие|им|ого|их)\b|премиум|люкс|роскош|элитн|престиж|статус|суперкар|спорткар|эксклюзив|"
     r"деньги\s+не\s+проблем|не\s+жалко\s+денег|без\s+ограничени\w*\s+по\s+бюджет", "premium", 1.0),
    (r"крут(ую|ая|ой|ые|ому|ое)\b|эффектн|впечатля|на\s+зависть|выделя", "powerful", 0.6),
    (r"дешев|недорог|бюджетн|подешевле|не\s+дорог", "cheap", 0.6),
    (r"перв\w+\s+машин|новичок|только\s+получил", "first_car", 0.5),
]

BODY_RULES = [
    (r"кроссовер|внедорожник|джип|suv", "suv"), (r"седан", "sedan"),
    (r"х[еэ]тчб[еэ]к|лифтб[еэ]к", "hatch"), (r"универсал", "wagon"), (r"минив[эе]н", "minivan"),
]


def parse_budget(text: str):
    """Бюджет — число рядом со словами «до / за / не дороже / бюджет» или с «млн / тыс». Числа в названиях моделей («х5») не считаются."""
    text = re.sub(r"(?<![а-яa-z0-9])(?:[a-z]{1,3}[\s-]?\d{1,3}|[а-я]\d{1,3})(?![а-яa-z0-9.,])", " ", text)       # x5, m3, rs6, а4
    cands = list(re.finditer(r"(?:до|за|не\s+дороже|бюджет\w*(?:\s+до)?|около|порядка)\s*(\d+(?:[.,]\d+)?)\s*(млн|миллион\w*|лям\w*|тыс\w*|к)?", text))
    m = cands[0] if cands else re.search(r"(\d+(?:[.,]\d+)?)\s*(млн|миллион\w*|лям\w*|тыс\w*)", text)
    if not m:
        return None
    unit = (m.group(2) or "").lower()
    if not unit and re.match(r"\s*(мест|л|литр|км|лет|человек|дверей|двер)", text[m.end(1):]):
        return None                                       # «до 5 мест» — не деньги
    num = float(m.group(1).replace(",", "."))
    if unit.startswith(("млн", "миллион", "лям")):
        num *= 1_000_000
    elif unit.startswith(("тыс", "к")):
        num *= 1_000
    elif num < 50:  # «до 2.5» без единиц — это миллионы
        num *= 1_000_000
    return int(num) if 100_000 <= num <= 100_000_000 else None


def _market_paths() -> list[str]:
    try:
        from . import engine
        return [m["path"] for m in engine.market_models()]
    except Exception:                           # снимка рынка нет — модели в тексте не ищем
        return []


def parse(text: str) -> ProfileDelta:
    t = text[:1000].lower().replace("ё", "е")
    hard: dict = {}
    nice: dict = {}
    color = None

    for pattern, key, w in NICE_RULES:
        if re.search(pattern, t):
            nice[key] = w
    if "low_power" in nice:
        nice.pop("powerful", None)
    if "premium" in nice:                      # «дорогую» и «подешевле» одновременно не бывает
        nice.pop("cheap", None)
    skip: list[str] = []
    if re.search(r"для\s+(одного|себя|меня)|вдвоем|для\s+двоих|без\s+пассажир|только\s+я\b", t):
        skip.append("seats")                   # число мест уже понятно из слов клиента
    if {"premium", "powerful"} & set(nice) and re.search(r"катат|для\s+души|удовольств|эмоци|прокатит|покрасовать|выпендр|выделя", t):
        skip.append("purpose")
    for pattern, c in COLORS.items():
        if re.search(rf"\b{pattern}", t):
            color = c
            break
    named = find_brands(t)                     # «хочу ролс ройс» — марка обязательна, подмены не будет
    if named:
        hard["brands"] = named
        if set(named) & LUXURY:
            nice["premium"] = 1.0
            skip.append("purpose")
        elif set(named) & PREMIUM:
            nice.setdefault("premium", 0.7)
        if "premium" in nice:
            nice.pop("cheap", None)
    if named:
        models = find_models(t, named, _market_paths())
        if models:                              # «хочу X5» — именно X5, а не любая премиальная BMW
            hard["models"] = models
            hard["brands"] = sorted({m.partition("/")[0] for m in models} | set(named))
    if "family" in nice:
        hard["seats_min"] = 5
    if re.search(r"7\s*мест|семь\s+мест|многодет", t):
        hard["seats_min"] = 7
    if re.search(r"автомат|акпп|без\s+сцеплен", t):
        hard["transmission"] = "auto"
    elif re.search(r"механ|мкпп|ручк", t):
        hard["transmission"] = "manual"
    if re.search(r"полн\w*\s+привод|4x4|4х4|4wd|awd", t):
        hard["drive"] = "awd"
    bodies = [b for p, b in BODY_RULES if re.search(p, t)]
    if bodies:
        hard["body"] = bodies
    if re.search(r"бюджет|до\s+\d|за\s+\d|не\s+дороже|\d\s*(млн|тыс|лям)", t):
        b = parse_budget(t)
        if b:
            hard["budget_max"] = b
    budget_unknown = bool(re.search(r"не\s+знаю\s+(сколько|бюджет)|бюджет\s+не\s+знаю", t))
    return ProfileDelta(hard=hard, nice=nice, color=color, budget_unknown=budget_unknown, skip_slots=skip)


def parse_ex(text: str) -> tuple[ProfileDelta, str]:
    """Описание -> (изменение профиля, чем разобрано). Если установлена нейросеть, она понимает смысл
    и пересказывает цель, а правила перекрывают её числа и жёсткие условия (бюджет, места, коробка), чтобы ничего не выдумать."""
    rules = parse(text)
    res = llm.understand(text.strip()[:600])
    if not res:
        return rules, "rules"
    rule_hard = rules.hard.model_dump(exclude_none=True)
    hard = {**res["hard"], **rule_hard}
    if "seats_min" in res["hard"] and "seats_min" in rule_hard:   # «семья» по правилам = 5 мест, но «7 мест» от нейросети важнее
        hard["seats_min"] = max(res["hard"]["seats_min"], rule_hard["seats_min"])
    nice = {**res["nice"], **rules.nice}
    if "low_power" in nice:
        nice.pop("powerful", None)
    if "premium" in nice:
        nice.pop("cheap", None)
    delta = ProfileDelta(hard=hard, nice=nice, color=rules.color or res["color"], budget_unknown=rules.budget_unknown,
                         goal=res["goal"], skip_slots=rules.skip_slots)
    return delta, "llm+rules"
