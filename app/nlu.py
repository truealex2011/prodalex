"""Текст -> изменение профиля.

СЕЙЧАС: заглушка на захардкоженных правилах (ключевые слова и регулярки).
ПОТОМ: сюда подключается наша дообученная модель. Контракт не меняется:
на вход текст, на выход ProfileDelta. Остальной код ничего не заметит.
"""
import re

from . import llm
from .brands import LUXURY, PREMIUM, find_brands
from .models_text import find_models, phonetic_models
from .profile import ProfileDelta, chips, empty_profile, merge

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
    cands = list(re.finditer(r"(?:до|за|не\s+дороже|бюджет\w*(?:\s+до)?|около|порядка)\s*(\d+(?:[.,]\d+)?)\s*(млн|миллион\w*|лям\w*|тыс\w*|к\b)?", text))
    m = cands[0] if cands else re.search(r"(\d+(?:[.,]\d+)?)\s*(млн|миллион\w*|лям\w*|тыс\w*)", text)
    if not m:
        return None
    unit = (m.group(2) or "").lower()
    if not unit and re.match(r"\s*(мест|л\b|литр|км|лет|человек|дверей|двер)", text[m.end(1):]):
        return None                                       # «до 5 мест» — не деньги
    num = float(m.group(1).replace(",", "."))
    if unit.startswith(("млн", "миллион", "лям")):
        num *= 1_000_000
    elif unit.startswith(("тыс", "к")):
        num *= 1_000
    elif num < 50:  # «до 2.5» без единиц — это миллионы
        num *= 1_000_000
    return int(num) if 100_000 <= num <= 100_000_000 else None


# ───────────── бессмыслица: «ваорворвораврвроваовра» не должна превращаться в запрос ─────────────
_VOW = set("аеёиоуыэюяaeiouy")
_MASH = re.compile(r"qwert|asdfg|zxcv|йцук|фыва|ячсм|ghjk|hjkl")


def _gibberish_word(w: str) -> bool:
    if len(w) < 4:
        return False
    if re.search(r"(.)\1{3,}", w) or _MASH.search(w):
        return True
    if len(w) < 6:
        return False
    vow = sum(c in _VOW for c in w) / len(w)
    run = max((len(m.group(0)) for m in re.finditer(r"[^аеёиоуыэюяaeiouy\d\-]+", w)), default=0)
    return bool(vow < 0.15 or vow > 0.8 or run >= 6 or re.search(r"(.{2,4})\1{2,}", w)
                or (len(w) >= 8 and len(set(w)) / len(w) < 0.5) or len(w) > 28)


def sanitize(text: str) -> str:
    """Текст без слов-бессмыслицы. Пусто — в тексте нет ни одного осмысленного слова."""
    words = re.findall(r"[\w-]+|[^\w\s]+|\s+", text)
    kept = [w for w in words if not (re.fullmatch(r"[\w-]+", w) and _gibberish_word(w.lower().replace("ё", "е")))]
    out = re.sub(r"\s{2,}", " ", "".join(kept)).strip(" ,.;")
    return out if re.search(r"[а-яёa-z]{2,}", out.lower()) else ""


# слова, по которым видно, что человек говорит о машине; без них короткое слово вроде «попа» запросом не считается
CAR_WORDS = re.compile(r"машин|авто|тачк|\bcar\b|седан|кроссовер|внедорож|универсал|х[еэ]тч|минив|джип|пикап|купе|кабриолет|катат|ездить|ездит|"
                       r"поезд|дач|семь|дет[ие]|работ|город|трасс|бюджет|млн|тыс|дешев|дорог|мощн|быстр|надеж|эконом|комфорт|багажник|привод|"
                       r"короб|механ|цвет|красн|син|черн|бел|сер|зелен|жел|оранж|подбер|посоветуй|нужн|хочу|ищу|куплю|взять|прокат|такси|рыбал|охот")


def has_meaning(text: str, rules) -> bool:
    """В описании есть хоть что-то про машину: правила что-то нашли, есть слова о машинах или текст достаточно длинный для нейросети."""
    t = text.lower().replace("ё", "е")
    signal = rules.hard.model_dump(exclude_none=True) or rules.nice or rules.color or rules.budget_unknown
    return bool(signal or CAR_WORDS.search(t) or len(re.findall(r"[а-я]{3,}|[a-z]{3,}", t)) >= 4)


def _market_paths() -> list[str]:
    try:
        from . import engine
        return [m["path"] for m in engine.market_models()]
    except Exception:                           # снимка рынка нет — модели в тексте не ищем
        return []


def parse(text: str) -> ProfileDelta:
    text = llm.fix_typos(text)
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
    paths = _market_paths()
    models = find_models(t, named, paths) if named else []
    if not models:                              # «лэнд крузер», «камри», «кашкай»: названо по-русски без марки или с опечаткой
        models = phonetic_models(t, paths, named or None)
    if models:                                  # «хочу X5» — именно X5, а не любая премиальная BMW
        hard["models"] = models
        hard["brands"] = sorted({m.partition("/")[0] for m in models} | set(named))
        if not named and set(hard["brands"]) & PREMIUM:
            nice.setdefault("premium", 0.7)
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


CLARIFY = 'Я не понял, что вы имели в виду. Какая машина вам нужна? Можно назвать хотя бы одно пожелание: марку, бюджет или для чего вы будете ездить.'


def understood(delta: ProfileDelta) -> bool:
    return bool(delta.hard.model_dump(exclude_none=True) or delta.nice or delta.color or delta.budget_unknown or delta.skip_slots)


def rule_goal(delta: ProfileDelta) -> str | None:
    """Пересказ желания по тому, что нашли правила: «Ищу BMW X5, до 10 млн ₽, премиум». Без нейросети — значит, без выдумок."""
    from .profile import chips, empty_profile, merge
    labels = [c["label"] for c in chips(merge(empty_profile(), delta))]
    subj = next((x.split(": ", 1)[1] for x in labels if x.startswith("модель: ")), None) or next((x.split(": ", 1)[1] for x in labels if x.startswith("марка: ")), None)
    rest = [x for x in labels if not x.startswith(("модель: ", "марка: "))]
    if not subj and not rest:
        return None
    if subj:
        return f"Ищу {subj}" + ("".join(f", {x}" for x in rest))
    if len(rest) == 1:
        return rest[0]
    return "Ищу машину: " + ", ".join(rest)


def parse_ex(text: str) -> tuple[ProfileDelta, str]:
    """Описание -> (изменение профиля, чем разобрано). Если установлена нейросеть, она понимает смысл
    и пересказывает цель, а правила перекрывают её числа и жёсткие условия (бюджет, места, коробка), чтобы ничего не выдумать."""
    rules = parse(text)
    res = llm.understand(text.strip()[:600])
    if not res:
        rules.goal = rule_goal(rules)
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
                         goal=None, skip_slots=rules.skip_slots)
    # названа марка или модель — пересказ строим по правилам (нейросеть может его исказить); иначе её пересказ, если он прошёл проверку
    named = bool(hard.get("models") or hard.get("brands"))
    delta.goal = rule_goal(delta) if named else (res["goal"] or rule_goal(delta))
    return delta, "llm+rules"
