"""«Тест на лося»: объезд и удар.

Источник данных — по приоритету:
1. РЕАЛЬНЫЕ опубликованные результаты (specs.moose_real — скорость прохождения лосиного теста
   km77 / Teknikens Värld; specs.crash_real — рейтинг защиты взрослых Euro NCAP / ANCAP / ARCAP / ...).
2. Если реальных данных нет — упрощённая физическая модель по характеристикам машины.
В ответе всегда указано, что именно использовано («реальный тест» или «оценка модели») и ссылка.

Это не прогноз для конкретной аварии: результат зависит от водителя, шин, дороги и версии машины.
"""
import math

G = 9.81
REF_SPEED = 72.0      # км/ч: порог прохождения шведского лосиного теста; модель калибрована на ~0.9 g
REF_ACCEL = 0.9
# Коэффициенты модели подобраны по 6 реальным тестам из каталога (Sportage, Camry, RAV4, Octavia,
# BMW 3, Cayenne): средняя ошибка ≈ 4,4 км/ч. Пересчёт — tools/calibrate_moose.py.
K_ROLL = 0.75         # доля статического порога опрокидывания, доступная в резкой переставке
K_SCALE = 1.01
CHASSIS = {"basic": 0.95, "modern": 1.0, "premium": 1.1}
CG_BASE = {"sedan": 0.52, "hatch": 0.55, "wagon": 0.60, "suv": 0.66, "minivan": 0.70}
HOOD_BASE = {"sedan": 0.78, "hatch": 0.80, "wagon": 0.85, "suv": 0.95, "minivan": 1.0}
PROTECTION_BY_CHASSIS = {"basic": 0.55, "modern": 0.75, "premium": 0.85}  # если краш-теста нет

DISCLAIMER = ("Реальные данные — результаты профессиональных тестов (часто на соседней версии машины). "
              "Где их нет — оценка упрощённой модели. Это не прогноз для конкретной аварии.")


def _cg_height(car: dict, s: dict) -> float:
    return CG_BASE.get(car["body"], 0.58) + (s["clearance_mm"] - 160) * 0.0008


def model_swerve_limit(car: dict) -> dict:
    """Физическая оценка предельной скорости переставки (если реального теста нет)."""
    s = car["specs"]
    chassis = CHASSIS.get(s.get("chassis"), 1.0)
    h = _cg_height(car, s)
    ssf = (s["track_mm"] / 1000) / (2 * h)            # статический фактор устойчивости
    roll_limit = ssf * K_ROLL * chassis               # при резкой перекладке запас меньше статического
    grip = 0.9 * (1.0 if s["esp"] else 0.85) * chassis
    grip *= max(0.85, min(1.0, 1.05 - (s["weight_kg"] - 1200) / 10000))
    limit = min(grip, roll_limit)
    return {"v_max": K_SCALE * REF_SPEED * math.sqrt(limit / REF_ACCEL), "ssf": ssf, "cg": h,
            "mode": "rollover" if roll_limit < grip else "skid"}


def swerve(car: dict, speed: float) -> dict:
    s = car["specs"]
    model = model_swerve_limit(car)
    real = s.get("moose_real")
    v_lim = real["speed"] if real else model["v_max"]
    margin = v_lim - speed
    mode = model["mode"]
    if margin >= 5:
        verdict, title = "pass", "Объедет уверенно"
        text = f"Запас около {margin:.0f} км/ч: машина уйдёт от лося и вернётся в свою полосу."
    elif margin >= 0:
        verdict, title = "limit", "Объедет, но на пределе"
        text = ("Почти предел машины: её сильно качнёт" +
                (", высокий кузов опасно накренится." if mode == "rollover" else " и поведёт в занос."))
    else:
        verdict, title = "fail", "Не справится"
        text = (f"Скорость на {-margin:.0f} км/ч выше предела: " +
                ("высокий центр тяжести — риск опрокидывания." if mode == "rollover" else
                 "машину сорвёт в занос" + ("" if s["esp"] else ", а ESP, чтобы её поймать, нет") + "."))
    source = ({"basis": "real", "label": real["label"], "url": real["url"]} if real else
              {"basis": "model", "label": "Реального теста нет — оценка по колее, высоте центра тяжести, ESP и массе"})
    factors = [
        f"предел манёвра ≈ {v_lim:.0f} км/ч ({'реальный тест' if real else 'оценка модели'})",
        f"колея {s['track_mm']} мм, центр тяжести ≈ {model['cg']:.2f} м",
        "ESP есть" if s["esp"] else "ESP нет",
    ]
    return {"verdict": verdict, "mode": mode, "title": title, "text": text, "v_max": round(v_lim),
            "source": source, "factors": factors}


# --- удар ---------------------------------------------------------------------------------------
LEVELS = [(0.15, "low", "ушибы"), (0.35, "medium", "травмы средней тяжести"),
          (0.65, "high", "тяжёлые травмы"), (math.inf, "critical", "угроза жизни")]
LEVEL_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def _level(x: float) -> dict:
    for limit, code, text in LEVELS:
        if x < limit:
            return {"level": code, "text": text, "value": round(x, 2)}


def collision(car: dict, speed: float) -> dict:
    s = car["specs"]
    impact = max(0.0, speed - 0.8 * G * 0.4 * 3.6)  # реакция ~1 с + 0,4 с торможения с 0,8 g
    hood = HOOD_BASE.get(car["body"], 0.85) + (s["clearance_mm"] - 180) / 1000
    roof_hit = hood < 0.95  # низкая машина подбивает лосю ноги — туша падает на стекло и крышу

    real = s.get("crash_real")
    p = real["index"] if real else PROTECTION_BY_CHASSIS.get(s.get("chassis"), 0.7) + 0.01 * min(s["airbags"], 8)
    weak = max(0.05, 1.15 - p)                      # чем хуже краш-тест, тем больше нагрузки на людей
    energy = (impact / 56) ** 2                     # 56 км/ч — скорость фронтального краш-теста
    bags = 1 - 0.03 * min(s["airbags"], 8)
    base = energy * weak

    seats = {}
    for seat, k_head, k_chest, k_legs in (
        ("driver", 1.5 if roof_hit else 0.9, 0.95 * bags, 0.7),
        ("passenger", 1.4 if roof_hit else 0.85, 0.9 * bags, 0.65),
        ("rear", 1.0 if roof_hit else 0.5, 0.6, 0.4),
    ):
        seats[seat] = {"head": _level(base * k_head), "chest": _level(base * k_chest), "legs": _level(base * k_legs)}
        seats[seat]["overall"] = max((seats[seat][r] for r in ("head", "chest", "legs")),
                                     key=lambda x: LEVEL_ORDER[x["level"]])

    worst = max((v["overall"] for v in seats.values()), key=lambda x: LEVEL_ORDER[x["level"]])["level"]
    title, text = {
        "low": ("Все выживут, травмы лёгкие", "Ремни и подушки отработают: пристёгнутые люди отделаются ушибами."),
        "medium": ("Выживут, но травмы вероятны", "Удар сильный: у переднего ряда возможны травмы средней тяжести."),
        "high": ("Тяжёлые травмы у переднего ряда", "Кузов примет большую нагрузку — для водителя и пассажира это опасно."),
        "critical": ("Угроза жизни для переднего ряда",
                     "Туша лося около 450 кг давит на стекло и крышу — передний ряд в зоне смертельного риска."),
    }[worst]
    source = ({"basis": "real", "label": real["label"], "url": real["url"]} if real else
              {"basis": "model", "label": "Краш-теста нет — защита оценена по классу машины и числу подушек"})
    factors = [
        f"удар примерно на {impact:.0f} км/ч (водитель успевает чуть затормозить)",
        "капот низкий — лось попадёт в стекло и крышу" if roof_hit else "капот высокий — удар примут бампер и капот",
        f"защита по краш-тесту: {p * 100:.0f}% ({'реальный тест' if real else 'оценка'}), подушек: {s['airbags']}",
    ]
    damaged = ["hood", "windshield", "front_bumper", "grille"] + (["roof"] if roof_hit else [])
    return {"level": worst, "title": title, "text": text, "impact_speed": round(impact), "roof_hit": roof_hit,
            "seats": seats, "source": source, "factors": factors, "damaged_parts": damaged,
            "risk": round(base, 3)}


def moose_test(car: dict, speed: float) -> dict:
    return {"speed": speed, "body": car["body"], "swerve": swerve(car, speed),
            "collision": collision(car, speed), "disclaimer": DISCLAIMER}
