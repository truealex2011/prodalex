"""Детерминированное ядро подбора. Без сети, без случайности, чистые функции.

1) жёсткий фильтр по обязательным условиям;
2) скоринг по желательным (need_fit, веса 0..1);
3) стабильная сортировка: score убыв., затем id возр.;
4) объяснения шаблонами из данных каталога;
5) если пусто — контрфактуалы: что ослабить, чтобы что-то нашлось.
"""
from typing import Optional

from .brands import slug_of

UNKNOWN = 0.5  # неизвестное значение не штрафуем и не награждаем


def check_hard(car: dict, hard: dict) -> Optional[bool]:
    """True — проходит, False — нарушает, None — не знаем (нет данных)."""
    unknown = False
    checks = {
        "budget_max": ("price", lambda v, lim: v <= lim),
        "seats_min": ("seats", lambda v, lim: v >= lim),
        "body": ("body", lambda v, lim: v in lim),
        "transmission": ("transmission", lambda v, lim: v == lim),
        "drive": ("drive", lambda v, lim: v == lim),
        "fuel": ("fuel", lambda v, lim: v == lim),
        "brands": ("brand", lambda v, lim: slug_of(v) in lim),
    }
    for key, limit in hard.items():
        if key not in checks or limit in (None, []):
            continue
        field, ok = checks[key]
        value = car.get(field)
        if value is None:
            unknown = True
        elif not ok(value, limit):
            return False
    return None if unknown else True


def _norm(cars: list[dict], field: str, value, invert=False) -> float:
    vals = [c[field] for c in cars if c.get(field) is not None]
    if value is None or not vals:
        return UNKNOWN
    lo, hi = min(vals), max(vals)
    if hi == lo:
        return 1.0
    x = (value - lo) / (hi - lo)
    return 1 - x if invert else x


def feature(car: dict, key: str, pool: list[dict]) -> float:
    tags = set(car.get("tags") or [])
    if key == "low_consumption":
        return _norm(pool, "consumption_l100", car.get("consumption_l100"), invert=True)
    if key == "low_power":
        return _norm(pool, "power_hp", car.get("power_hp"), invert=True)
    if key == "powerful":
        return _norm(pool, "power_hp", car.get("power_hp"))
    if key == "big_trunk":
        return _norm(pool, "trunk_l", car.get("trunk_l"))
    if key == "cheap":
        return _norm(pool, "price", car.get("price"), invert=True)
    if key == "premium":                          # чем дороже и статуснее, тем лучше
        return 0.85 * _norm(pool, "price", car.get("price")) + 0.15 * (1.0 if "premium" in tags else 0.0)
    if key == "offroad":
        return 1.0 if car.get("drive") == "awd" or "off-road" in tags else 0.5 if car["body"] == "suv" else 0.0
    if key == "city":
        return 1.0 if "city" in tags else 0.6 if car["body"] in ("hatch", "sedan") else 0.3
    if key == "family":
        return 1.0 if "family" in tags or (car.get("seats") or 0) >= 7 else 0.3
    if key == "comfort":
        return 1.0 if "road-trip" in tags or "comfort" in tags else 0.4
    if key == "first_car":
        return 1.0 if "first-car" in tags else 0.3
    return UNKNOWN


def score(car: dict, profile: dict, pool: list[dict]) -> tuple[float, dict]:
    parts = {k: feature(car, k, pool) for k in profile.get("nice", {})}
    weights = dict(profile.get("nice", {}))
    if profile.get("color"):
        parts["color"] = 1.0 if profile["color"] in (car.get("colors") or []) else 0.0
        weights["color"] = 0.5
    total_w = sum(weights.values())
    if not total_w:
        return 0.5, parts
    s = sum(weights[k] * parts[k] for k in weights) / total_w
    return round(s, 4), parts


REASON_TEXT = {
    "low_consumption": lambda c: f"экономичная: {c['consumption_l100']} л/100 км",
    "low_power": lambda c: f"спокойный мотор {c['power_hp']} л.с.",
    "powerful": lambda c: f"мощный мотор {c['power_hp']} л.с.",
    "big_trunk": lambda c: f"багажник {c['trunk_l']} л",
    "cheap": lambda c: f"доступная цена: от {c['price']:,} ₽".replace(",", " "),
    "premium": lambda c: f"статусная машина: от {c['price']:,} ₽".replace(",", " "),
    "offroad": lambda c: "справится с бездорожьем",
    "city": lambda c: "удобна в городе",
    "family": lambda c: f"подходит для семьи, {c['seats']} мест",
    "comfort": lambda c: "комфортна в дальней дороге",
    "first_car": lambda c: "хороша как первая машина",
    "color": lambda c: "есть в нужном цвете",
}
TRADEOFF_TEXT = {
    "low_consumption": lambda c: f"расход выше, чем хотелось: {c['consumption_l100']} л/100 км",
    "low_power": lambda c: f"мощнее, чем просили: {c['power_hp']} л.с.",
    "powerful": lambda c: f"мотор скромный: {c['power_hp']} л.с.",
    "big_trunk": lambda c: f"багажник небольшой: {c['trunk_l']} л",
    "cheap": lambda c: "среди подходящих — не самая дешёвая",
    "premium": lambda c: "не из премиум-сегмента",
    "offroad": lambda c: "не для серьёзного бездорожья",
    "city": lambda c: "в городе великовата",
    "family": lambda c: "для большой семьи тесновата",
    "comfort": lambda c: "в дальней дороге будет не так комфортно",
    "first_car": lambda c: "для новичка не самый простой вариант",
    "color": lambda c: "в нужном цвете нет",
}
FIELD_NAMES = {"price": "цена", "seats": "число мест", "body": "кузов", "transmission": "коробка",
               "drive": "привод", "fuel": "топливо", "consumption_l100": "расход",
               "trunk_l": "объём багажника", "power_hp": "мощность"}


def explain(car: dict, parts: dict, profile: dict) -> dict:
    hard = profile.get("hard", {})
    reasons = []
    if hard.get("seats_min"):
        reasons.append(f"{car['seats']} мест — как вы просили")
    if hard.get("budget_max") and car.get("price"):
        reasons.append(f"укладывается в бюджет: от {car['price']:,} ₽".replace(",", " "))
    ranked = sorted(parts.items(), key=lambda kv: (-kv[1], kv[0]))
    reasons += [REASON_TEXT[k](car) for k, v in ranked if v >= 0.7][:3]
    tradeoffs = [TRADEOFF_TEXT[k](car) for k, v in ranked if v < 0.4]
    if hard.get("budget_max") and car.get("price") and car["price"] > 0.95 * hard["budget_max"]:
        tradeoffs.append("запаса по бюджету почти нет")
    unknown = [FIELD_NAMES.get(f, f) for f in ("consumption_l100", "trunk_l", "power_hp") if car.get(f) is None]
    if unknown:
        tradeoffs.append("в объявлении не указано: " + ", ".join(unknown))
    return {"reasons": reasons or ["лучшее совпадение среди подходящих"], "tradeoffs": tradeoffs}


def counterfactuals(cars: list[dict], profile: dict, excluded: set) -> list[dict]:
    """Единичные ослабления: что поменять, чтобы что-то нашлось."""
    hard = profile.get("hard", {})
    options = []

    def count(h):
        return sum(1 for c in cars if c["id"] not in excluded and check_hard(c, h) is True)

    if hard.get("budget_max"):
        for pct in (10, 20, 30):
            h = {**hard, "budget_max": int(hard["budget_max"] * (1 + pct / 100))}
            n = count(h)
            if n:
                options.append({"action": {"type": "budget", "value": h["budget_max"]}, "found": n,
                                "text": f"Поднять бюджет на {pct}% — подойдёт моделей: {n}"})
                break
    names = {"seats_min": "условие по местам", "body": "тип кузова", "transmission": "тип коробки",
             "drive": "полный привод", "fuel": "тип топлива", "budget_max": "ограничение бюджета"}
    for key in sorted(hard):
        h = {k: v for k, v in hard.items() if k != key}
        n = count(h)
        if n:
            options.append({"action": {"type": "drop", "key": f"hard.{key}"}, "found": n,
                            "text": f"Убрать {names.get(key, key)} — подойдёт моделей: {n}"})
    options.sort(key=lambda o: (-o["found"], o["text"]))
    return options[:3]


def conflicts(cars: list[dict], profile: dict) -> list[str]:
    """Какое именно условие всё обнуляет."""
    hard = profile.get("hard", {})
    out = []
    for key in sorted(hard):
        if not any(check_hard(c, {key: hard[key]}) is not False for c in cars):
            out.append(f"ни одна машина каталога не проходит условие «{FIELD_NAMES.get(key, key)}»")
    return out


def recommend(cars: list[dict], profile: dict, excluded: list[int] = ()) -> dict:
    excluded_set = set(excluded)
    hard = profile.get("hard", {})
    passed, uncertain = [], []
    for c in cars:
        if c["id"] in excluded_set:
            continue
        r = check_hard(c, hard)
        if r is True:
            passed.append(c)
        elif r is None:
            uncertain.append(c)

    if not passed:
        return {
            "car": None, "alternatives": [], "uncertain": [c["title"] for c in uncertain],
            "empty": True,
            "why_empty": conflicts(cars, profile) or ["вместе эти условия исключают все модели каталога"],
            "counterfactuals": counterfactuals(cars, profile, excluded_set),
        }

    scored = []
    for c in passed:
        s, parts = score(c, profile, passed)
        scored.append((s, c, parts))
    scored.sort(key=lambda x: (-x[0], x[1]["id"]))

    best_s, best, parts = scored[0]
    alternatives = []
    for s, c, _ in scored[1:3]:
        diff = []
        if c.get("price") and best.get("price"):
            diff.append("дешевле" if c["price"] < best["price"] else "дороже")
        if c.get("trunk_l") and best.get("trunk_l"):
            diff.append("багажник больше" if c["trunk_l"] > best["trunk_l"] else "багажник меньше")
        alternatives.append({"id": c["id"], "title": c["title"], "score": s, "diff": ", ".join(diff)})

    return {
        "car": best, "score": best_s, **explain(best, parts, profile),
        "alternatives": alternatives, "uncertain": [c["title"] for c in uncertain],
        "empty": False, "budget_note": "бюджет не учтён" if profile.get("budget_unknown") else None,
    }
