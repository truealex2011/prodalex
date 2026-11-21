"""Генератор тестового каталога: python tools/gen_catalog.py data/catalog/cars.json

ВНИМАНИЕ: характеристики ориентировочные (тестовые данные для скелета), перед защитой сверить
с официальными источниками. Поля подсказок (hotspots) строятся из этих же данных.
"""
import json
import sys

OUT = sys.argv[1] if len(sys.argv) > 1 else "data/catalog/cars.json"
CREDIT = "«Lada Vesta» — tiunov.se (Sketchfab), CC BY 4.0"
# Собственные разобранные модели: id машины -> (файл, автор/лицензия). Остальным ищем модель на Sketchfab (app/media.py).
OWN_MODELS = {
    2: ("models/lada_vesta_parts.glb", CREDIT),
    10: ("models/kia_sportage_parts.glb", "2023 Kia Sportage GT-Line — Ddiaz Design (Sketchfab), CC BY-NC-SA 4.0"),
}

# id, бренд, модель, комплектация, год, кузов, мест, багажник, л.с., расход, привод, КПП, топливо, цена, цвета, теги
CARS = [
    (1, "LADA", "Granta", "1.6 MT Comfort", 2025, "sedan", 5, 520, 90, 6.8, "fwd", "manual", "petrol", 950_000, ["white", "red", "gray", "blue"], ["first-car", "city"]),
    (2, "LADA", "Vesta", "1.6 CVT Comfort", 2025, "sedan", 5, 480, 106, 7.3, "fwd", "auto", "petrol", 1_550_000, ["white", "red", "gray", "black", "orange"], ["first-car", "city", "family"]),
    (3, "LADA", "Largus", "1.6 MT 7 мест", 2025, "wagon", 7, 560, 90, 8.0, "fwd", "manual", "petrol", 1_650_000, ["white", "gray", "brown"], ["family", "road-trip"]),
    (4, "LADA", "Niva Legend", "1.7 MT 3дв", 2025, "suv", 4, 265, 83, 10.0, "awd", "manual", "petrol", 1_050_000, ["white", "green", "black"], ["off-road"]),
    (5, "LADA", "Niva Travel", "1.7 MT Comfort", 2025, "suv", 5, 315, 80, 9.9, "awd", "manual", "petrol", 1_350_000, ["white", "gray", "blue"], ["off-road", "family"]),
    (6, "УАЗ", "Patriot", "2.7 MT", 2025, "suv", 5, 650, 150, 13.0, "awd", "manual", "petrol", 1_900_000, ["white", "green", "black", "gray"], ["off-road", "road-trip"]),
    (7, "Hyundai", "Solaris", "1.6 AT", 2024, "sedan", 5, 480, 123, 6.6, "fwd", "auto", "petrol", 1_900_000, ["white", "red", "silver", "black"], ["city", "first-car"]),
    (8, "Kia", "Rio X", "1.6 AT", 2024, "hatch", 5, 390, 123, 6.6, "fwd", "auto", "petrol", 1_800_000, ["white", "red", "orange", "gray"], ["city", "first-car"]),
    (9, "Hyundai", "Creta", "1.6 AT", 2024, "suv", 5, 402, 121, 7.0, "fwd", "auto", "petrol", 2_300_000, ["white", "red", "gray", "black"], ["city", "family"]),
    (10, "Kia", "Sportage", "2.0 AT AWD", 2024, "suv", 5, 540, 150, 8.2, "awd", "auto", "petrol", 3_400_000, ["white", "black", "gray", "blue"], ["family", "road-trip"]),
    (11, "Toyota", "Camry", "2.5 AT", 2024, "sedan", 5, 493, 200, 7.8, "fwd", "auto", "petrol", 4_200_000, ["white", "black", "silver"], ["comfort", "road-trip"]),
    (12, "Toyota", "RAV4", "2.5 Hybrid AWD", 2024, "suv", 5, 580, 222, 5.6, "awd", "auto", "hybrid", 4_600_000, ["white", "black", "blue", "gray"], ["family", "road-trip", "comfort"]),
    (13, "Skoda", "Octavia", "1.4 TSI AT", 2023, "hatch", 5, 600, 150, 6.2, "fwd", "auto", "petrol", 3_000_000, ["white", "blue", "gray", "black"], ["family", "road-trip"]),
    (14, "Renault", "Logan", "1.6 MT", 2021, "sedan", 5, 510, 82, 6.9, "fwd", "manual", "petrol", 1_000_000, ["white", "gray", "blue"], ["first-car"]),
    (15, "Haval", "Jolion", "1.5T DCT", 2025, "suv", 5, 337, 143, 7.4, "fwd", "auto", "petrol", 2_100_000, ["white", "red", "blue", "gray"], ["city"]),
    (16, "Geely", "Coolray", "1.5T DCT", 2025, "suv", 5, 330, 150, 7.0, "fwd", "auto", "petrol", 2_300_000, ["white", "red", "gray", "black"], ["city"]),
    (17, "Geely", "Monjaro", "2.0T AWD", 2025, "suv", 5, None, 238, 8.6, "awd", "auto", "petrol", 4_000_000, ["white", "black", "gray", "blue"], ["family", "road-trip", "comfort", "off-road"]),
    (18, "Chery", "Tiggo 7 Pro Max", "1.5T CVT", 2025, "suv", 5, 475, 147, 7.5, "fwd", "auto", "petrol", 2_500_000, ["white", "black", "gray", "red"], ["family", "comfort"]),
    (19, "BMW", "3 Series", "320i AT", 2023, "sedan", 5, 480, 184, 6.5, "rwd", "auto", "petrol", 5_500_000, ["white", "black", "blue", "gray"], ["comfort"]),
    (20, "Porsche", "Cayenne", "3.0 AT", 2023, "suv", 5, 772, 340, 11.0, "awd", "auto", "petrol", 15_000_000, ["white", "black", "gray", "red"], ["comfort", "road-trip"]),
]

# Расширенные характеристики (ориентировочно).
# 0-100 c, макс км/ч, клиренс мм, длина/ширина/высота мм, масса кг, колея мм, бак л, шины,
# фары LED, фонари LED, подушек, ESP, экран дюймы (0 = нет), климат, электро-ручник, электрозеркала, шасси
SPEC_FIELDS = ["accel_0_100", "top_speed", "clearance_mm", "length_mm", "width_mm", "height_mm", "weight_kg",
               "track_mm", "tank_l", "tires", "led_headlights", "led_taillights", "airbags", "esp", "screen_in",
               "climate", "electric_handbrake", "power_mirrors", "chassis"]
SPECS = {
    1: (11.6, 170, 180, 4280, 1700, 1500, 1160, 1430, 50, "185/60 R14", False, False, 2, True, 0, "кондиционер", False, True, "basic"),
    2: (13.1, 172, 178, 4410, 1764, 1497, 1250, 1510, 55, "195/65 R15", False, True, 2, True, 8, "кондиционер", False, True, "modern"),
    3: (15.4, 160, 170, 4470, 1750, 1670, 1370, 1470, 50, "185/65 R15", False, False, 2, True, 0, "кондиционер", False, True, "basic"),
    4: (17.0, 142, 200, 3740, 1680, 1640, 1285, 1430, 42, "185/75 R16", False, False, 1, False, 0, "отопитель", False, False, "basic"),
    5: (19.0, 140, 205, 4100, 1800, 1690, 1450, 1450, 58, "215/65 R16", False, False, 2, True, 0, "кондиционер", False, True, "basic"),
    6: (20.0, 150, 210, 4785, 1900, 2050, 2125, 1600, 72, "225/75 R16", False, False, 2, True, 7, "кондиционер", False, True, "basic"),
    7: (11.2, 183, 160, 4405, 1729, 1470, 1200, 1510, 50, "185/65 R15", False, False, 2, True, 8, "кондиционер", False, True, "modern"),
    8: (11.6, 183, 170, 4240, 1750, 1510, 1210, 1505, 50, "195/60 R16", False, False, 2, True, 8, "кондиционер", False, True, "modern"),
    9: (12.3, 169, 190, 4300, 1790, 1635, 1340, 1570, 55, "205/65 R16", False, True, 2, True, 8, "климат-контроль", False, True, "modern"),
    10: (11.6, 181, 181, 4660, 1865, 1665, 1600, 1615, 54, "235/60 R18", True, True, 6, True, 8, "климат-контроль", True, True, "modern"),
    11: (9.0, 210, 155, 4885, 1840, 1445, 1550, 1580, 60, "235/45 R18", True, True, 7, True, 9, "климат-контроль", True, True, "modern"),
    12: (8.1, 180, 190, 4600, 1855, 1685, 1700, 1600, 55, "225/60 R18", True, True, 7, True, 9, "климат-контроль", True, True, "modern"),
    13: (8.2, 225, 160, 4690, 1830, 1470, 1340, 1540, 50, "205/55 R16", True, True, 6, True, 8, "климат-контроль", True, True, "modern"),
    14: (11.9, 172, 172, 4360, 1730, 1520, 1100, 1480, 50, "185/65 R15", False, False, 2, False, 0, "кондиционер", False, False, "basic"),
    15: (10.6, 175, 183, 4472, 1841, 1574, 1430, 1560, 55, "215/55 R18", True, True, 6, True, 10, "климат-контроль", True, True, "modern"),
    16: (7.9, 190, 196, 4330, 1800, 1609, 1400, 1550, 45, "225/45 R18", True, True, 6, True, 10, "климат-контроль", True, True, "modern"),
    17: (7.7, 215, 200, 4770, 1895, 1689, 1850, 1610, 62, "235/50 R20", True, True, 7, True, 12.3, "климат-контроль", True, True, "modern"),
    18: (10.0, 186, 190, 4500, 1842, 1705, 1550, 1570, 51, "225/60 R18", True, True, 6, True, 10.25, "климат-контроль", True, True, "modern"),
    19: (7.1, 235, 140, 4710, 1827, 1440, 1550, 1580, 59, "225/50 R17", True, True, 6, True, 10.25, "климат-контроль", True, True, "premium"),
    20: (6.2, 245, 210, 4920, 1983, 1698, 2100, 1660, 75, "255/55 R19", True, True, 8, True, 12.3, "климат-контроль", True, True, "premium"),
}

# Реальные опубликованные результаты (собраны вручную, ноябрь 2026). Тесты часто проводились на соседней
# версии (гибрид, универсал, другой мотор) — это указано в label. Где реальных данных нет — модель.
# moose: максимальная скорость прохождения лосиного теста; crash: рейтинг защиты взрослых.
#   index — доля баллов за защиту взрослых (0..1), чтобы сравнивать разные программы (приближённо).
REAL = {
    2: {"crash": {"index": 14.1 / 16, "label": "ARCAP: 4★, 14,1 из 16 баллов",
                  "url": "https://news.drom.ru/lada-vesta-39176.html"}},
    1: {"crash": {"index": 8.4 / 16, "label": "ARCAP: 2★, 8,4 из 16 баллов (Granta ранних лет)",
                  "url": "https://www.kolesa.ru/article/video-krash-test-1-2014-05-13"}},
    6: {"crash": {"index": 2.7 / 16, "label": "ARCAP: 0★, 2,7 из 16 баллов (Patriot ранних лет)",
                  "url": "https://auto.mail.ru/article/39656-dve_zvezdy_i_menshe_samye_skandalnye_krash-testy/"}},
    7: {"crash": {"index": 8.5 / 16, "label": "ARCAP: 2★, 8,5 из 16 баллов (Solaris 1-го поколения)",
                  "url": "https://www.kolesa.ru/article/video-krash-test-1-2014-05-13"}},
    9: {"crash": {"index": 8 / 17, "label": "Global NCAP: 3★, взрослые 8 из 17 (индийская версия)",
                  "url": "https://www.autocarindia.com/car-news/hyundai-creta-scores-3-star-global-ncap-safety-rating-424127"}},
    10: {"moose": {"speed": 80, "label": "km77, Kia Sportage 2022 (1.6 T-GDI)",
                   "url": "https://www.sicurauto.it/news/auto-elettriche-ibride/kia-sportage-2022-nel-test-dellalce-non-si-comporta-come-un-suv/"},
         "crash": {"index": 0.87, "label": "Euro NCAP 2022: 5★, взрослые 87%",
                   "url": "https://cdn.euroncap.com/media/76412/euroncap-2022-kia-sportage-datasheet.pdf"}},
    11: {"moose": {"speed": 73, "label": "km77, Toyota Camry Hybrid 2021",
                   "url": "https://www.motor1.com/news/619353/toyota-camry-hybrid-moose-test/"},
         "crash": {"index": 36.16 / 37 * 0.95, "label": "ANCAP 2017: 5★, 36,16 из 37 баллов",
                   "url": "https://www.ancap.com.au/safety-ratings/toyota/camry/9476aa"}},
    12: {"moose": {"speed": 68, "label": "Teknikens Värld, RAV4 Hybrid 2019 — тест не пройден (порог 72)",
                   "url": "https://www.motor1.com/news/370486/toyota-rav4-moose-test/"},
         "crash": {"index": 0.93, "label": "Euro NCAP 2019: 5★, взрослые 93%",
                   "url": "https://www.euroncap.com/en/results/toyota/rav4/35881"}},
    13: {"moose": {"speed": 70, "label": "km77, Octavia Combi 2020",
                   "url": "https://www.autoevolution.com/news/2020-skoda-octavia-performs-badly-in-moose-test-superb-iv-fails-144319.html"},
         "crash": {"index": 0.92, "label": "Euro NCAP 2019: 5★, взрослые 92%",
                   "url": "https://www.euroncap.com/en/results/skoda/octavia/39136"}},
    14: {"crash": {"index": 11 / 16, "label": "ARCAP: 3★, 11 из 16 баллов (Logan 1-го поколения)",
                   "url": "https://www.kolesa.ru/article/video-krash-test-1-2014-05-13"}},
    15: {"crash": {"index": 0.85, "label": "ANCAP: 5★ (австралийская версия; базовая ЮАР — 2★ Global NCAP)",
                   "url": "https://mybroadband.co.za/news/motoring/653174-top-selling-chinese-car-gets-2-star-safety-rating-in-south-africa.html"}},
    16: {"crash": {"index": 43.64 / 50, "label": "ASEAN NCAP 2020: 5★, взрослые 43,64 из 50",
                   "url": "https://www.autodeal.com.ph/articles/car-news-philippines/geely-coolray-scores-5-star-rating-asean-ncap"}},
    18: {"crash": {"index": 0.88, "label": "ANCAP 2023: 5★, взрослые 88%",
                   "url": "https://www.ancap.com.au/safety-ratings/chery/tiggo-7-pro/73e06f"}},
    19: {"moose": {"speed": 71, "label": "km77, BMW M340d Touring 2023 (3-я серия, универсал)",
                   "url": "https://www.bmwblog.com/2023/04/18/2023-bmw-m340d-touring-moose-test/"},
         "crash": {"index": 0.97, "label": "Euro NCAP 2019: 5★, взрослые 97%",
                   "url": "https://www.motorbeam.com/2019-bm-3-series-euro-ncap-score-is-5-stars/"}},
    20: {"moose": {"speed": 78, "label": "km77, Cayenne Coupe Turbo E-Hybrid",
                   "url": "https://www.formacar.com/en/porsche--cayenne-coupe--62349--article"}},
}

DRIVE = {"fwd": "передний", "rwd": "задний", "awd": "полный"}
FUEL = {"petrol": "бензин", "hybrid": "гибрид", "diesel": "дизель", "electric": "электро"}
BODY = {"sedan": "седан", "hatch": "хэтчбек", "suv": "кроссовер/внедорожник", "wagon": "универсал",
        "minivan": "минивэн"}
COLOR = {"red": "красный", "white": "белый", "black": "чёрный", "blue": "синий", "gray": "серый",
         "green": "зелёный", "silver": "серебристый", "yellow": "жёлтый", "orange": "оранжевый",
         "brown": "коричневый"}


def hotspots(c, s):
    """Ключи = id деталей из parts.json, значение — факт об ЭТОЙ машине (название детали — из parts.json)."""
    _, brand, model, trim, _, body, seats, trunk, hp, cons, drv, tr, fuel, price, colors, tags = c
    auto = tr == "auto"
    head = "светодиодные (LED)" if s["led_headlights"] else "галогенные"
    tail = "светодиодные (LED)" if s["led_taillights"] else "с лампами"
    wheel = f"Шины {s['tires']} · привод {DRIVE[drv]}" + (" · ESP" if s["esp"] else " · без ESP")
    mirror = "С электроприводом и обогревом" if s["power_mirrors"] else "Механическая регулировка"
    h = {
        "body_paint": f"{BODY[body].capitalize()} · {s['weight_kg']} кг · цвета: {', '.join(COLOR[x] for x in colors)}",
        "hood": f"{trim}: {hp} л.с., {FUEL[fuel]} · 0–100 за {s['accel_0_100']} с",
        "grille": f"Бак {s['tank_l']} л · расход {cons} л/100 км",
        "front_bumper": f"Дорожный просвет {s['clearance_mm']} мм",
        "rear_bumper": f"Длина кузова {s['length_mm']} мм",
        "roof": f"Высота {s['height_mm']} мм",
        "windshield": f"Ширина кузова {s['width_mm']} мм",
        "headlight_left": f"Фары {head}", "headlight_right": f"Фары {head}",
        "taillight_left": f"Фонари {tail}", "taillight_right": f"Фонари {tail}",
        "mirror_left": mirror, "mirror_right": mirror,
        "trunk_lid": f"Объём багажника {trunk} л" if trunk else "Объём багажника: нет данных",
        "door_front_left": f"Колея {s['track_mm']} мм — чем шире, тем устойчивее",
        "door_front_right": f"Колея {s['track_mm']} мм — чем шире, тем устойчивее",
        "door_rear_left": f"Мест в салоне: {seats}", "door_rear_right": f"Мест в салоне: {seats}",
        "steering_wheel": f"Подушек безопасности: {s['airbags']}",
        "instrument_cluster": f"Макс. скорость {s['top_speed']} км/ч · 0–100 за {s['accel_0_100']} с",
        "multimedia_screen": f"Экран {s['screen_in']}″" if s["screen_in"] else "Экрана нет — простая магнитола",
        "dashboard": f"Климат: {s['climate']}",
        "air_vents": f"Климат: {s['climate']}",
        "center_console": f"Расход {cons} л/100 км · бак {s['tank_l']} л",
        "gear_shifter": "Коробка: автомат" if auto else "Коробка: механика",
        "handbrake": "Стояночный тормоз электрический" if s["electric_handbrake"] else "Ручной стояночный тормоз",
        "pedals": "2 педали — автомат" if auto else "3 педали — механика, нужно сцепление",
        "seat_driver": f"Мест в салоне: {seats}",
        "seat_passenger": f"Подушек безопасности: {s['airbags']}",
        "seat_rear": f"Мест в салоне: {seats}" + (" (третий ряд)" if seats >= 7 else ""),
    }
    for w in ("wheel_front_left", "wheel_front_right", "wheel_rear_left", "wheel_rear_right"):
        h[w] = wheel
    return h


items = []
for c in CARS:
    (i, brand, model, trim, year, body, seats, trunk, hp, cons, drv, tr, fuel, price, colors, tags) = c
    specs = dict(zip(SPEC_FIELDS, SPECS[i]))
    specs["moose_real"] = REAL.get(i, {}).get("moose")
    specs["crash_real"] = REAL.get(i, {}).get("crash")
    items.append({
        "id": i, "brand": brand, "model": model, "trim": trim, "year": year, "body": body,
        "seats": seats, "trunk_l": trunk, "power_hp": hp, "consumption_l100": cons, "drive": drv,
        "transmission": tr, "fuel": fuel, "price": price, "colors": colors, "tags": tags,
        "specs": specs,
        "listing_url": None, "image_key": None,
        # пока одна 3D-модель на все машины
        # Собственная разобранная 3D-модель есть только у Vesta; для остальных машин модель ищется на Sketchfab
        # автоматически (app/media.py), а если её нет — показываются фото.
        "model_key": OWN_MODELS.get(i, (None,))[0],
        "parts_key": OWN_MODELS[i][0].replace(".glb", ".json") if i in OWN_MODELS else None,
        "model_credit": OWN_MODELS[i][1] if i in OWN_MODELS else None,
        "hotspots": hotspots(c, specs),
    })

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(items, f, ensure_ascii=False, indent=1)
print(len(items), "cars ->", OUT)
