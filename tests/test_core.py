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
    assert res["car"]["price"] == max(c["price"] for c in CARS if c["price"])   # импортированные модели без цены в сравнении не участвуют


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


def test_phonetic_model_names_and_gibberish():
    from app import nlu
    d = nlu.parse("хочу лэнд крузер 200")
    assert d.hard.models == ["toyota/land_cruiser"] and d.hard.brands == ["toyota"]
    assert nlu.parse("камера заднего вида").hard.models is None            # «камера» — не Camry
    assert nlu.sanitize("ваорворвораврвроваовра") == "" and nlu.sanitize("asdfgh") == ""
    assert nlu.sanitize("хочу бмв ваорворвор") == "хочу бмв"
    assert nlu.sanitize("хочу внедорожник трёхцилиндровый") == "хочу внедорожник трёхцилиндровый"


def test_low_quality_models_are_rejected():
    from app.splitter import low_quality_reason
    assert low_quality_reason({"triangles": 40_000, "texture_px": None, "textures": 0})
    assert low_quality_reason({"triangles": 300_000, "texture_px": 256, "textures": 3})
    assert low_quality_reason({"triangles": 256_000, "texture_px": 1024, "textures": 5}) is None
    assert low_quality_reason({"triangles": 198_000, "texture_px": None, "textures": 0}) is None


def test_goal_does_not_copy_prompt_example():
    from app import llm, nlu
    ex = "Ищет вместительный надёжный внедорожник для поездок на рыбалку, до 4 млн рублей"
    assert not llm._goal_ok(ex, "попа")                                   # пример из подсказки на пустой запрос
    assert llm._goal_ok("Ищет внедорожник для семьи", "хочу внедорожник для семьи")
    assert not nlu.has_meaning("попа", nlu.parse("попа")) and nlu.has_meaning("хочу кроссовер", nlu.parse("хочу кроссовер"))


def test_slang_bmw_and_model_digits_are_not_seats_or_budget():
    from app import llm, nlu
    d = nlu.parse("хочу беху х5")
    assert d.hard.models == ["bmw/x5"] and d.hard.seats_min is None and d.hard.budget_max is None
    raw = {"seats_min": 5, "budget_rub": 500000, "goal": "Ищу хачу для рыбалки", "needs": [], "body": [], "transmission": "any", "drive": "any", "fuel": "any"}
    cleaned = llm.clean(raw, "хочу беху х5")
    assert cleaned["hard"] == {} and cleaned["goal"] is None                   # «х5» — не 5 мест и не 500 тыс., пересказ выдуманный
    assert nlu.rule_goal(nlu.parse("хочу бмв х5 до 10 млн")) == "Ищу BMW X5, до 10 млн ₽, премиум"


def test_similar_model_must_be_modern_and_not_low_poly():
    from app.sketchfab import era_ok, good_quality
    assert era_ok({"name": "Mercedes-Benz E-Class (W212)"}, 2022) and era_ok({"name": "Audi A7 2019"}, 2016)
    assert not era_ok({"name": "Toyota Corolla (KE80)"}, 2018) and not era_ok({"name": "toyota crown 90s"}, 2016)
    assert not good_quality({"name": "LADA 2107 Low Poly", "faceCount": 200_000}) and good_quality({"name": "BMW X5", "faceCount": 80_000})


def test_damaged_listings_are_detected():
    from app.engine import is_damaged
    assert is_damaged({"title": "Продажа Hyundai Solaris", "description": "седан, б/у, 1.6 литра, не на ходу или битый"})
    assert is_damaged({"title": "x", "description": "", "raw": "Особые отметки требуется ремонт или не на ходу Поколение"})
    assert not is_damaged({"title": "x", "description": "в идеальном состоянии, не битый, без ДТП", "raw": "Поиск ДТП и расчетов ремонтов Проверка наличия залогов"})


def test_relaxed_requests_for_missing_exact_match():
    from app import engine
    steps = engine._relaxed({"hard": {"brands": ["bmw"], "models": ["bmw/x9"], "budget_max": 3_000_000}, "nice": {}})
    assert len(steps) == 2 and "models" not in steps[0][0]["hard"] and "brands" not in steps[1][0]["hard"]
    assert "не найдено" in steps[0][1] and "BMW" in steps[0][1]
    only_budget = engine._relaxed({"hard": {"budget_max": 500_000}, "nice": {}})
    assert len(only_budget) == 1 and "budget_max" not in only_budget[0][0]["hard"] and "дороже" in only_budget[0][1]
