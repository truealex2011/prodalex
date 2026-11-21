"""Тесты ядра: чистые функции, БД не нужна."""
import itertools
import json
import os
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)

from app.nlu import parse  # noqa: E402
from app.recommender import check_hard, recommend  # noqa: E402

with open(os.path.join(ROOT, "data", "catalog", "cars.json"), encoding="utf-8") as f:
    CARS = [{**c, "title": f"{c['brand']} {c['model']}"} for c in json.load(f)]


def test_parse_example_from_case():
    d = parse("Хочу для семьи. чтобы красненькая и не мощная сильно была и чтобы бензина мало ела")
    assert d.color == "red"
    assert d.hard.seats_min == 5
    assert {"family", "low_power", "low_consumption"} <= set(d.nice)
    assert "powerful" not in d.nice


def test_parse_budget_and_transmission():
    d = parse("кроссовер на автомате до 2.5 млн")
    assert d.hard.budget_max == 2_500_000
    assert d.hard.transmission == "auto"
    assert d.hard.body == ["suv"]


def test_expensive_is_not_cheap():
    d = parse("хочу супер дорогую машину")
    assert "premium" in d.nice and "cheap" not in d.nice
    assert "cheap" in parse("нужна недорогая машина").nice and "premium" not in parse("нужна недорогая машина").nice
    res = recommend(CARS, {"hard": {}, "nice": {"premium": 1.0}})
    assert res["car"]["price"] == max(c["price"] for c in CARS)


def test_hard_constraints_never_violated():
    budgets = [None, 1_000_000, 2_000_000, 5_000_000]
    seats = [None, 5, 7]
    trans = [None, "auto", "manual"]
    drive = [None, "awd"]
    for b, s, t, d in itertools.product(budgets, seats, trans, drive):
        hard = {k: v for k, v in dict(budget_max=b, seats_min=s, transmission=t, drive=d).items() if v}
        res = recommend(CARS, {"hard": hard, "nice": {"family": 0.5}})
        if res["empty"]:
            assert res["why_empty"]
            continue
        assert check_hard(res["car"], hard) is True


def test_reproducible():
    p = {"hard": {"budget_max": 3_000_000}, "nice": {"low_consumption": 0.8, "city": 0.5}, "color": "red"}
    assert recommend(CARS, p) == recommend(CARS, p)


def test_empty_result_gives_counterfactuals():
    res = recommend(CARS, {"hard": {"budget_max": 900_000, "drive": "awd", "transmission": "auto"}, "nice": {}})
    assert res["empty"] and res["car"] is None
    assert res["counterfactuals"]


def test_excluded_car_not_returned_again():
    p = {"hard": {}, "nice": {"cheap": 1.0}}
    first = recommend(CARS, p)["car"]["id"]
    assert recommend(CARS, p, excluded=[first])["car"]["id"] != first


def test_named_brand_is_hard_and_premium():
    from app import nlu
    from app.recommender import check_hard
    d = nlu.parse("ХОЧУ РОЛС РОЙС БРАТ")
    assert d.hard.brands == ["rolls-royce"] and d.nice.get("premium") == 1.0
    assert check_hard({"brand": "LADA"}, {"brands": ["rolls-royce"]}) is False
    assert check_hard({"brand": "Rolls-Royce"}, {"brands": ["rolls-royce"]}) is True
    assert nlu.parse("не лада, что-нибудь надёжное").hard.brands is None


def test_named_model_is_hard_and_not_confused_with_budget():
    from app import nlu
    from app.models_text import title_matches
    d = nlu.parse("Хочу бмв х5, до 10 млн")
    assert d.hard.models == ["bmw/x5"] and d.hard.budget_max == 10_000_000
    assert title_matches("BMW X5, 2020 год", "bmw/x5") and not title_matches("BMW X7, 2021 год", "bmw/x5")
    assert nlu.parse("хочу мерс").hard.models is None


def test_custom_question_rejects_copied_example_and_money_options():
    from app.advisor import _custom_ok
    copied = {"text": "Что вам важно?", "options": [{"label": "Тепло и комфорт", "need": "comfort"}, {"label": "Вместительный багажник", "need": "big_trunk"}]}
    money = {"text": "Сколько вложите?", "options": [{"label": "100 000", "need": "cheap"}, {"label": "250 000", "need": "premium"}]}
    good = {"text": "Где вы будете ездить чаще всего?", "options": [{"label": "Пробки и дворы", "need": "city"}, {"label": "Трасса и поездки", "need": "comfort"}]}
    assert not _custom_ok(copied, "хочу x5", set()) and not _custom_ok(money, "хочу x5", set()) and _custom_ok(good, "хочу x5", set())
