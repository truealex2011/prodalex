"""Уточняющие вопросы.

Модель разбирает описание в профиль, а КАКОЙ вопрос задать дальше, решают правила:
берём незаполненный слот, ответ на который сильнее всего разделит оставшиеся машины
(максимальная энтропия). Так опрос короткий и воспроизводимый.
"""
import math
from collections import Counter

from .profile import NICE_KEYS
from .recommender import check_hard

LIMIT = {"novice": 3, "expert": 5}          # вопросов в одном раунде анкеты
MAX_TOTAL = {"novice": 5, "expert": 8}      # всего за подбор (первый раунд + уточнения)

SLOTS = {
    "purpose": {
        "modes": ("novice", "expert"),
        "text": {"novice": "Для чего в основном нужна машина?",
                 "expert": "Основной сценарий эксплуатации?"},
        "options": [("Город и работа", "city"), ("Семья и дети", "family"),
                    ("Дальние поездки", "comfort"), ("Дача и бездорожье", "offroad")],
        "filled": lambda p: any(k in p["nice"] for k in ("city", "family", "comfort", "offroad", "premium")) or "purpose" in p.get("skipped", []),
        "attr": lambda c: (c.get("body"), "awd" if c.get("drive") == "awd" else "2wd"),
    },
    "seats": {
        "modes": ("novice", "expert"),
        "text": {"novice": "Сколько человек обычно будет ездить?",
                 "expert": "Минимальное число посадочных мест?"},
        "options": [("1–2", 2), ("3–5", 5), ("6–7", 7)],
        "filled": lambda p: "seats_min" in p["hard"] or "seats" in p.get("skipped", []),
        "attr": lambda c: c.get("seats"),
    },
    "budget": {
        "modes": ("novice", "expert"),
        "text": {"novice": "Сколько примерно готовы потратить?",
                 "expert": "Максимальный бюджет?"},
        "options": [("до 1 млн", 1_000_000), ("до 2 млн", 2_000_000),
                    ("до 3 млн", 3_000_000), ("до 5 млн", 5_000_000), ("Не знаю", "unknown")],
        "filled": lambda p: "budget_max" in p["hard"] or p.get("budget_unknown"),
        "attr": lambda c: (c.get("price") or 0) // 1_000_000,
    },
    "transmission": {
        "modes": ("novice", "expert"),
        "text": {"novice": "Умеете ездить на механике или нужен автомат?",
                 "expert": "Тип КПП?"},
        "options": [("Только автомат", "auto"), ("Механика", "manual"), ("Без разницы", "any")],
        "filled": lambda p: "transmission" in p["hard"],
        "attr": lambda c: c.get("transmission"),
    },
    "drive": {
        "modes": ("expert",),
        "text": {"expert": "Нужен полный привод?"},
        "options": [("Да, обязательно", "awd"), ("Не важно", "any")],
        "filled": lambda p: "drive" in p["hard"] or "offroad" in p["nice"],
        "attr": lambda c: c.get("drive"),
    },
    "body": {
        "modes": ("novice", "expert"),
        "text": {"novice": "Какой кузов вам нравится?", "expert": "Тип кузова?"},
        "options": [("Седан", "sedan"), ("Кроссовер", "suv"), ("Хэтчбек", "hatch"), ("Универсал", "wagon"),
                    ("Минивэн", "minivan"), ("Без разницы", "any")],
        "filled": lambda p: "body" in p["hard"],
        "attr": lambda c: c.get("body"),
    },
    "color": {
        "modes": ("novice", "expert"),
        "text": {"novice": "Какой цвет кузова вам нравится?", "expert": "Предпочтение по заводскому цвету?"},
        "options": [("Красный", "red"), ("Белый", "white"), ("Чёрный", "black"), ("Синий", "blue"),
                    ("Серый", "gray"), ("Любой", "any")],
        "filled": lambda p: bool(p.get("color")),
        "attr": lambda c: (c.get("colors") or [None])[0],
    },
    "fuel": {
        "modes": ("expert",),
        "text": {"expert": "Какой тип двигателя рассматриваете?"},
        "options": [("Бензин", "petrol"), ("Дизель", "diesel"), ("Гибрид", "hybrid"), ("Электро", "electric"),
                    ("Любой", "any")],
        "filled": lambda p: "fuel" in p["hard"],
        "attr": lambda c: c.get("fuel"),
    },
    "power": {
        "modes": ("expert",),
        "text": {"expert": "Приоритет по динамике?"},
        "options": [("Экономичность важнее", "low_power"), ("Нужна динамика", "powerful"),
                    ("Баланс", "any")],
        "filled": lambda p: "low_power" in p["nice"] or "powerful" in p["nice"],
        "attr": lambda c: (c.get("power_hp") or 0) // 50,
    },
}


def _entropy(values) -> float:
    counts = Counter(values)
    n = sum(counts.values())
    return -sum(v / n * math.log2(v / n) for v in counts.values()) if n else 0.0


def rank_slots(profile: dict, mode: str, asked: list, cars: list[dict]) -> list[str]:
    """Незаполненные поля, отсортированные по тому, насколько ответ сужает выбор (энтропия); без пустых."""
    pool = [c for c in cars if check_hard(c, profile.get("hard", {})) is not False]
    if len(pool) <= 2:
        return []
    gains = []
    for slot, s in SLOTS.items():
        if mode in s["modes"] and slot not in asked and slot not in profile.get("skipped", []) and not s["filled"](profile):
            g = _entropy([s["attr"](c) for c in pool])
            if g > 0:
                gains.append((-g, list(SLOTS).index(slot), slot))   # индекс — стабильный тай-брейк
    return [slot for _, _, slot in sorted(gains)]


PREMIUM_BUDGET = [("До 6 млн", 6_000_000), ("До 10 млн", 10_000_000), ("До 20 млн", 20_000_000), ("Без ограничений", "unknown")]


def question_dict(slot: str, mode: str, text: str | None = None, profile: dict | None = None) -> dict:
    s = SLOTS[slot]
    options = s["options"]
    if slot == "budget" and profile and "premium" in profile.get("nice", {}):   # для «дорогой» машины потолок в 5 млн не годится
        options = PREMIUM_BUDGET
    return {"slot": slot, "label": slot, "text": text or s["text"][mode],
            "options": [{"label": lbl, "value": v} for lbl, v in options],
            "default_must": slot in ("seats", "budget", "transmission", "drive", "body", "fuel")}


def next_question(profile: dict, mode: str, asked: list, cars: list[dict]):
    if len(asked) >= LIMIT[mode]:
        return None
    pool = [c for c in cars if check_hard(c, profile.get("hard", {})) is not False]
    if len(pool) <= 2:
        return None
    best, best_gain = None, 0.0
    for slot, s in SLOTS.items():  # порядок словаря = стабильный тай-брейк
        if mode not in s["modes"] or slot in asked or s["filled"](profile):
            continue
        gain = _entropy([s["attr"](c) for c in pool])
        if gain > best_gain:
            best, best_gain = slot, gain
    if best is None:
        return None
    s = SLOTS[best]
    return {
        "slot": best,
        "text": s["text"][mode],
        "options": [{"label": lbl, "value": v} for lbl, v in s["options"]],
        "progress": {"current": len(asked) + 1, "max": LIMIT[mode]},
        "default_must": best in ("seats", "budget", "transmission", "drive"),
    }


def apply_answer(profile: dict, slot: str, value, must: bool) -> dict:
    p = {**profile, "hard": dict(profile["hard"]), "nice": dict(profile["nice"])}
    if value in (None, "any"):
        p.setdefault("skipped", []).append(slot)
        return p
    if slot == "budget" and value == "unknown":
        p["budget_unknown"] = True
        return p
    target = {"seats": "seats_min", "budget": "budget_max", "transmission": "transmission",
              "drive": "drive"}
    if slot in target:
        if must:
            p["hard"][target[slot]] = value
        elif slot == "budget":
            p["nice"]["cheap"] = 0.8
        elif slot == "seats" and value >= 5:
            p["nice"]["family"] = 0.7
    elif slot in ("purpose", "power"):
        p["nice"][value] = 1.0 if must else 0.8
    elif slot.startswith("custom_"):                 # собственный вопрос нейросети: вариант привязан к пожеланию (need)
        if value in NICE_KEYS:
            p["nice"][value] = 0.9
    elif slot == "color":
        p["color"] = value
    elif slot == "body" and must:
        p["hard"]["body"] = [value]
    elif slot == "fuel" and must:
        p["hard"]["fuel"] = value
    return p
