"""Нейросеть-разборщик: проверяем без самой модели — подменяем запуск llama.cpp заранее заданным ответом."""
import json
import os
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)

import pytest  # noqa: E402

from app import llm, nlu  # noqa: E402


@pytest.fixture(autouse=True)
def fake_model(tmp_path, monkeypatch):
    exe, gguf = tmp_path / "llama-completion.exe", tmp_path / "m.gguf"
    exe.write_bytes(b"x")
    gguf.write_bytes(b"x")
    monkeypatch.setenv("AUTOCHOICE_LLAMA", str(exe))
    monkeypatch.setenv("AUTOCHOICE_MODEL", str(gguf))
    llm.understand.cache_clear()
    yield
    llm.understand.cache_clear()


def answer(**kw):
    base = {"goal": "Ищет семейную машину", "budget_rub": 0, "seats_min": 0, "body": [], "transmission": "any",
            "drive": "any", "fuel": "any", "color": "any", "needs": []}
    return "мусор до " + json.dumps({**base, **kw}, ensure_ascii=False) + " мусор после"


def test_grammar_lists_only_allowed_values():
    for word in ("sedan", "minivan", "auto", "awd", "hybrid", "low_consumption", "first_car"):
        assert f'"\\"{word}\\""' in llm.GRAMMAR
    assert llm.GRAMMAR.startswith("root ::=") and "needs ::=" in llm.GRAMMAR


def test_llm_adds_meaning_and_goal(monkeypatch):
    monkeypatch.setattr(llm, "_run", lambda p: answer(body=["minivan"], needs=["family", "big_trunk", "выдумка"], seats_min=7))
    delta, engine = nlu.parse_ex("нужна машина чтобы вся семья с вещами влезла")
    assert engine == "llm+rules"
    assert "для семьи" in delta.goal and "большой багажник" in delta.goal
    assert delta.hard.body is None            # кузов в тексте не назван — догадку модели не принимаем
    assert delta.hard.seats_min == 7
    assert set(delta.nice) == {"family", "big_trunk"}   # значение вне списка отброшено


@pytest.mark.parametrize('text', ['шршщ ззз ыыы', 'asdf qwer', '?!? 4567', 'хочу машину'])
def test_unsupported_goal_does_not_start_selection(monkeypatch, text):
    monkeypatch.setattr(llm, '_run', lambda p: answer(goal='Ищу автомобиль для городских поездок',
                       needs=['city'], seats_min=4, budget_rub=4000000, body=['suv']))
    delta, _ = nlu.parse_ex(text)
    assert not delta.hard.model_dump(exclude_none=True) and not delta.nice
    assert delta.goal is None or delta.goal.startswith('Описание клиента:')


def test_short_supported_request_does_not_inherit_invented_purpose(monkeypatch):
    monkeypatch.setattr(llm, '_run', lambda p: answer(goal='Для поездок по городу', needs=['city']))
    delta, _ = nlu.parse_ex('красная')
    assert nlu.understood(delta) and delta.goal == 'цвет: красный'


def test_rules_override_llm_numbers(monkeypatch):
    monkeypatch.setattr(llm, "_run", lambda p: answer(budget_rub=9_000_000, transmission="manual"))
    delta, _ = nlu.parse_ex("кроссовер на автомате до 2.5 млн")
    assert delta.hard.budget_max == 2_500_000 and delta.hard.transmission == "auto"


def test_llm_cannot_invent_budget_without_digits(monkeypatch):
    monkeypatch.setattr(llm, "_run", lambda p: answer(budget_rub=1_000_000))
    delta, _ = nlu.parse_ex("хочу красивую машину для города")
    assert delta.hard.budget_max is None


def test_fallback_to_rules_when_model_fails(monkeypatch):
    def boom(prompt):
        raise TimeoutError("слишком долго")
    monkeypatch.setattr(llm, "_run", boom)
    delta, engine = nlu.parse_ex("для семьи, красная")
    assert engine == "rules" and delta.color == "red" and delta.goal is None


def test_no_model_installed_means_rules(monkeypatch):
    monkeypatch.setenv("AUTOCHOICE_LLAMA", "")
    monkeypatch.setattr(llm, "HOME", llm.Path("/nonexistent-home"))
    monkeypatch.setattr(llm, "PARSING_HOME", llm.Path("/nonexistent-home"))
    llm.understand.cache_clear()
    assert not llm.available()
    assert nlu.parse_ex("для семьи")[1] == "rules"


def test_llm_cannot_smuggle_values_without_evidence(monkeypatch):
    """Модель подмешала «спокойную, экономичную, автомат, кроссовер» из примера, хотя клиент просил быструю и яркую."""
    monkeypatch.setattr(llm, "_run", lambda p: answer(body=["suv"], transmission="auto", needs=["low_power", "low_consumption", "powerful"]))
    delta, _ = nlu.parse_ex("хочу яркую быструю машину чтобы было весело")
    assert delta.hard.body is None and delta.hard.transmission is None
    assert "low_power" not in delta.nice and "low_consumption" not in delta.nice
    assert "powerful" in delta.nice


def test_body_needs_the_body_word(monkeypatch):
    monkeypatch.setattr(llm, "_run", lambda p: answer(body=["sedan"]))
    assert nlu.parse_ex("нас пятеро плюс бабушка, ездим на дачу")[0].hard.body is None    # семья кузов не определяет
    assert nlu.parse_ex("хочу седан для семьи")[0].hard.body == ["sedan"]
    monkeypatch.setattr(llm, "_run", lambda p: answer(body=["suv"]))
    assert nlu.parse_ex("хочу седан")[0].hard.body == ["sedan"] or nlu.parse_ex("хочу седан")[0].hard.body is None
