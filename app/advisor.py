"""Анкета подбора: нейросеть читает описание клиента и составляет вопросы именно под него.

Как в проекте «парсинк»: пачка вопросов, после ответов — проверка, нужны ли уточнения. Состав анкеты:
  - custom — собственный вопрос нейросети по сути запроса («что вы имеете в виду под «крутой машиной»?»); каждый вариант
    привязан к пожеланию из закрытого списка (need), поэтому ответ можно честно учесть в подборе;
  - ask — нужные из фиксированных полей (бюджет, места, кузов…) с формулировкой под слова клиента.
Что клиент уже сказал (дорогая машина, для одного), не спрашивается. Без модели — отбор по энтропии, как раньше.
"""
import hashlib
import re

from . import llm
from .brands import LUXURY
from .questions import LIMIT, MAX_TOTAL, SLOTS, question_dict, rank_slots


# Запасные вопросы «по смыслу»: если нейросеть не придумала подходящий собственный вопрос, берём по главному пожеланию.
TAILORED = {
    "premium": ("Что для вас главное в дорогой машине?", [("Скорость и драйв", "powerful"), ("Престиж и статус", "premium"), ("Комфорт и роскошь", "comfort")]),
    "powerful": ("Что для вас значит «крутая» машина?", [("Мощность и разгон", "powerful"), ("Яркий вид и внимание", "premium"), ("Удобство каждый день", "comfort")]),
    "family": ("Как вы будете ездить всей семьёй?", [("Каждый день по городу", "city"), ("В дальние поездки", "comfort"), ("С большим багажом", "big_trunk")]),
    "offroad": ("Какая у вас дорога?", [("Грунт и бездорожье", "offroad"), ("Плохой асфальт", "comfort"), ("В основном город", "city")]),
    "low_consumption": ("Что для вас важнее в экономичности?", [("Малый расход топлива", "low_consumption"), ("Дёшево купить", "cheap"), ("Спокойный характер", "low_power")]),
    "city": ("Где вы будете ездить чаще всего?", [("Пробки и тесные дворы", "city"), ("Город и пригород", "comfort"), ("С большим багажом", "big_trunk")]),
    "first_car": ("Чего вы больше всего опасаетесь в первой машине?", [("Сложно парковаться", "city"), ("Дорого содержать", "low_consumption"), ("Слишком мощная", "low_power")]),
}
EXAMPLE_WORDS = re.compile(r"зимн|рыбал|повышенной проходимости")
BASE_QUESTION = re.compile(r"кузов|цвет|сколько человек|сколько готовы|бюджет|потратить|тип кпп|коробк|сколько мест|сколько нужно мест", re.I)


EXAMPLE_LABELS = {"чтобы не застрять", "тепло и комфорт", "вместительный багажник", "тяга и проходимость", "климат и комфорт салона",
                  "экономичный двигатель"}


def _custom_ok(c: dict, description: str, seen: set) -> bool:
    """Собственный вопрос нейросети годится, если он не копирует пример из подсказки, не дублирует обычные поля анкеты
    (кузов, цвет, бюджет, места, коробка) и варианты ответа осмысленные (не суммы и не слова из примера)."""
    t = c["text"].lower()
    labels = [o["label"].lower().strip() for o in c["options"]]
    return bool(not (EXAMPLE_WORDS.search(t) and not EXAMPLE_WORDS.search(description.lower())) and not BASE_QUESTION.search(t)
                and sum(x in EXAMPLE_LABELS for x in labels) < 2 and not any(re.search(r"\d{3}|₽|руб", x) for x in labels)
                and len(set(labels)) == len(labels)
                and "custom_" + hashlib.sha1(c["text"].encode()).hexdigest()[:8] not in seen)


def _tailored(profile: dict, seen: set) -> dict | None:
    for key, (text, opts) in TAILORED.items():
        if key in profile.get("nice", {}):
            slot = "custom_" + hashlib.sha1(text.encode()).hexdigest()[:8]
            if slot not in seen:
                return {"text": text, "options": [{"label": lbl, "need": need} for lbl, need in opts]}
    return None


def questionnaire(profile: dict, mode: str, asked: list, cars: list[dict], description: str, phase: str = "initial",
                  skipped_all: bool = False) -> dict:
    """{"questions": [...], "ai": bool}. Пустой список — данных достаточно, можно показывать машину."""
    left = MAX_TOTAL[mode] - len(asked)
    ranked = rank_slots(profile, mode, asked, cars)
    if left <= 0 or (phase == "more" and skipped_all):
        return {"questions": [], "ai": llm.available()}
    limit = min(2 if phase == "initial" else 1, left)
    fields = {k: SLOTS[k]["text"][mode] for k in ranked}
    known = "; ".join(f"{k}: {v}" for k, v in _known(profile).items())
    if profile.get("goal"):
        known = f"цель: {profile['goal']}; {known}"
    got = llm.ask_questions(description, known, mode, fields, limit)
    if got is None:                              # модели нет или она не ответила: вопрос под запрос + самый важный общий
        tailored = _tailored(profile, set(asked)) if phase == "initial" else None
        if tailored:
            got = {"custom": [tailored], "ask": [{"field": k, "text": ""} for k in ranked[:1]]}
        else:
            slots = ranked[:LIMIT[mode] if phase == "initial" else 1]
            return {"questions": [question_dict(k, mode, profile=profile) for k in slots], "ai": False}
    out, seen = [], set(asked)
    if phase == "initial":
        luxury = set(profile.get("hard", {}).get("brands") or []) & LUXURY    # для суперкаров вопрос проверенный, а не сочинённый
        customs = ([] if luxury else [c for c in got["custom"] if _custom_ok(c, description, seen)]) or [t for t in [_tailored(profile, seen)] if t]
        for c in customs:
            slot = "custom_" + hashlib.sha1(c["text"].encode()).hexdigest()[:8]
            if slot not in seen:
                opts = [{"label": o["label"], "value": o["need"]} for o in c["options"]] + [{"label": "Другое / не важно", "value": "any"}]
                out.append({"slot": slot, "label": "custom", "text": c["text"], "options": opts, "default_must": False})
    for g in got["ask"]:
        k, text = g["field"], re.sub(r"\s+", " ", g["text"]).strip()
        if k in seen or k not in fields:
            continue
        seen.add(k)
        out.append(question_dict(k, mode, text if text.endswith("?") else None, profile))
    if not out and phase == "initial" and ranked:        # в первом раунде без вопросов не оставляем
        out = [question_dict(ranked[0], mode, profile=profile)]
    return {"questions": out, "ai": True}


def _known(profile: dict) -> dict:
    known = {k: v for k, v in profile.get("hard", {}).items()}
    if profile.get("color"):
        known["color"] = profile["color"]
    known.update({f"хочет:{k}": w for k, w in profile.get("nice", {}).items()})
    return known
