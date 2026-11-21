"""Тест на лося: модель детерминирована и ведёт себя физически разумно."""
import copy
import json
import os
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)

from app.moose import collision, model_swerve_limit, moose_test, swerve  # noqa: E402

with open(os.path.join(ROOT, "data", "catalog", "cars.json"), encoding="utf-8") as f:
    CARS = {c["id"]: c for c in json.load(f)}


def test_every_car_has_result_on_slider_range():
    for car in CARS.values():
        for v in range(30, 95, 5):
            r = moose_test(car, v)
            assert r["swerve"]["verdict"] in ("pass", "limit", "fail")
            assert r["collision"]["level"] in ("low", "medium", "high", "critical")
            assert set(r["collision"]["seats"]) == {"driver", "passenger", "rear"}
            assert r["swerve"]["source"]["basis"] in ("real", "model")
            assert r["disclaimer"]


def test_real_data_is_used_when_available():
    sportage = moose_test(CARS[10], 60)
    assert sportage["swerve"]["source"]["basis"] == "real"
    assert sportage["swerve"]["v_max"] == 80                  # km77
    assert sportage["collision"]["source"]["basis"] == "real"  # Euro NCAP 2022
    granta = moose_test(CARS[1], 60)
    assert granta["swerve"]["source"]["basis"] == "model"      # реального лосиного теста нет
    assert granta["swerve"]["source"]["label"]


def test_real_failure_is_reported():
    rav4 = moose_test(CARS[12], 70)  # Teknikens Värld: 68 км/ч
    assert rav4["swerve"]["verdict"] == "fail"


def test_reproducible():
    car = CARS[2]
    assert moose_test(car, 60) == moose_test(car, 60)


def test_faster_is_never_easier():
    order = {"pass": 0, "limit": 1, "fail": 2}
    for car in CARS.values():
        for a, b in ((30, 50), (50, 60), (60, 90)):
            assert order[swerve(car, b)["verdict"]] >= order[swerve(car, a)["verdict"]]
            assert collision(car, b)["risk"] >= collision(car, a)["risk"]


def test_better_crash_rating_means_lower_risk():
    car = copy.deepcopy(CARS[1])
    worse = collision(car, 60)["risk"]
    car["specs"]["crash_real"] = {"index": 0.95, "label": "test", "url": ""}
    assert collision(car, 60)["risk"] < worse


def test_esp_helps():  # проверяем модель (у Granta реального лосиного теста нет)
    car = copy.deepcopy(CARS[1])
    with_esp = swerve(car, 60)["v_max"]
    car["specs"]["esp"] = False
    assert swerve(car, 60)["v_max"] <= with_esp


def test_wider_track_is_more_stable():
    # берём машину, у которой предел — опрокидывание (а не сцепление шин)
    car = copy.deepcopy(next(c for c in CARS.values() if model_swerve_limit(c)["mode"] == "rollover"))
    base = model_swerve_limit(car)["v_max"]
    car["specs"]["track_mm"] += 200
    assert model_swerve_limit(car)["v_max"] > base


def test_low_hood_means_roof_hit():
    assert collision(CARS[1], 60)["roof_hit"] is True      # седан
    assert collision(CARS[6], 60)["roof_hit"] is False     # высокий внедорожник
