"""Профиль пользователя и его схема.

Профиль — единственный вход в подбор. Его заполняет наша модель (или fallback),
ответы на вопросы и замечания. Модель НЕ выбирает машину, она только пишет профиль.
"""
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .brands import title_of
from .models_text import model_title

Body = Literal["sedan", "hatch", "suv", "wagon", "minivan"]
NiceKey = Literal[
    "low_consumption", "low_power", "powerful", "big_trunk", "offroad",
    "city", "family", "comfort", "cheap", "first_car", "premium",
]
Color = Literal["red", "white", "black", "blue", "gray", "green", "silver", "yellow", "orange", "brown"]

BODY_RU = {"sedan": "седан", "hatch": "хэтчбек", "suv": "кроссовер", "wagon": "универсал", "minivan": "минивэн"}
NICE_KEYS = list(NiceKey.__args__)  # type: ignore[attr-defined]


class Hard(BaseModel):
    """Обязательные условия: машина без них в выдачу не попадает никогда."""

    budget_max: Optional[int] = Field(default=None, ge=100_000, le=100_000_000)
    seats_min: Optional[int] = Field(default=None, ge=2, le=9)
    body: Optional[list[Body]] = None
    transmission: Optional[Literal["auto", "manual"]] = None
    drive: Optional[Literal["awd"]] = None
    fuel: Optional[Literal["petrol", "diesel", "hybrid", "electric"]] = None
    brands: Optional[list[str]] = Field(default=None, max_length=5)   # марки, которые клиент назвал сам
    models: Optional[list[str]] = Field(default=None, max_length=5)    # модели («bmw/x5»), которые клиент назвал сам


class ProfileDelta(BaseModel):
    """Ровно то, что модели разрешено вернуть. Всё остальное отбрасывается."""

    hard: Hard = Hard()
    nice: dict[NiceKey, float] = {}
    color: Optional[Color] = None
    budget_unknown: bool = False
    skip_slots: list[str] = []  # вопросы анкеты, ответ на которые уже понятен из описания
    goal: Optional[str] = None  # пересказ желания клиента нейросетью (только для показа, на подбор не влияет)


def empty_profile() -> dict:
    return {"hard": {}, "nice": {}, "color": None, "budget_unknown": False, "skipped": []}


def merge(profile: dict, delta: ProfileDelta) -> dict:
    """Новое дополняет старое, но не стирает уже заданные обязательные условия."""
    p = {**empty_profile(), **profile}
    hard = dict(p["hard"])
    for k, v in delta.hard.model_dump(exclude_none=True).items():
        hard[k] = v
    nice = dict(p["nice"])
    for k, w in delta.nice.items():
        nice[k] = round(max(0.0, min(1.0, float(w))), 2)
    p["hard"], p["nice"] = hard, nice
    if delta.skip_slots:
        p["skipped"] = list(dict.fromkeys([*p.get("skipped", []), *delta.skip_slots]))
    if delta.goal:
        p["goal"] = delta.goal
    if delta.color:
        p["color"] = delta.color
    if delta.budget_unknown:
        p["budget_unknown"] = True
        hard.pop("budget_max", None)
    return p


def chips(profile: dict) -> list[dict]:
    """Сводка «вот что я понял» — чипы для фронта, каждый можно удалить."""
    labels_hard = {
        "budget_max": lambda v: f"до {v / 1e6:.1f} млн ₽".replace(".0 ", " "),
        "seats_min": lambda v: f"от {v} мест",
        "body": lambda v: "кузов: " + ", ".join(BODY_RU.get(x, x) for x in v),
        "transmission": lambda v: "автомат" if v == "auto" else "механика",
        "drive": lambda v: "полный привод",
        "fuel": lambda v: f"топливо: {v}",
        "brands": lambda v: "марка: " + ", ".join(title_of(x) for x in v),
        "models": lambda v: "модель: " + ", ".join(f"{title_of(x.partition('/')[0])} {model_title(x)}" for x in v),
    }
    labels_nice = {
        "low_consumption": "мало ест", "low_power": "спокойная", "powerful": "мощная",
        "big_trunk": "большой багажник", "offroad": "для бездорожья", "city": "для города",
        "family": "для семьи", "comfort": "комфорт в дороге", "cheap": "подешевле",
        "first_car": "первая машина", "premium": "премиум",
    }
    out = [{"key": f"hard.{k}", "label": labels_hard[k](v), "must": True}
           for k, v in profile.get("hard", {}).items() if k in labels_hard]
    out += [{"key": f"nice.{k}", "label": labels_nice[k], "must": False}
            for k in profile.get("nice", {}) if k in labels_nice]
    if profile.get("color"):
        ru = {"red": "красный", "white": "белый", "black": "чёрный", "blue": "синий", "gray": "серый",
              "green": "зелёный", "silver": "серебристый", "yellow": "жёлтый", "orange": "оранжевый",
              "brown": "коричневый"}
        out.append({"key": "color", "label": f"цвет: {ru.get(profile['color'], profile['color'])}", "must": False})
    if profile.get("budget_unknown"):
        out.append({"key": "budget_unknown", "label": "бюджет не знаю", "must": False})
    return out


def remove_chip(profile: dict, key: str) -> dict:
    p = {**empty_profile(), **profile, "hard": dict(profile.get("hard", {})),
         "nice": dict(profile.get("nice", {}))}
    group, _, name = key.partition(".")
    if group == "hard":
        p["hard"].pop(name, None)
    elif group == "nice":
        p["nice"].pop(name, None)
    elif key == "color":
        p["color"] = None
    elif key == "budget_unknown":
        p["budget_unknown"] = False
    return p
