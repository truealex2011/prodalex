from app import part_facts

SPEC = {
    "Масса, кг": "1280", "Время разгона 0-100 км/ч, с": "10.9", "Объем топливного бака, л": "55",
    "Габариты кузова (Д x Ш x В), мм": "4410 x 1764 x 1512", "Передние колеса": "195/55 R16 Отзывы о шинах 195/55 R16",
    "Передние тормоза": "Дисковые", "Тип рулевого управления": "Шестерня-рейка", "Климат-контроль": "—",
    "Окраска кузова металлик": "опция", "Датчик дождя": "✓",
}


def test_parse_spec_numbers():
    s = part_facts.parse_spec(SPEC)
    assert s["weight_kg"] == 1280 and s["accel_0_100"] == 10.9 and s["tank_l"] == 55
    assert (s["length_mm"], s["width_mm"], s["height_mm"]) == (4410, 1764, 1512)


def test_part_texts_only_real_data():
    car = {"title": "Lada Vesta", "power_hp": 122, "year": 2019, "price": 900000, "drive": "fwd", "seats": 5, "trunk_l": 480,
           "specs": part_facts.parse_spec(SPEC)}
    t = part_facts.part_texts(car, {"engine": "1.8 л"}, SPEC)
    assert "122 л.с." in t["hood"] and "1280 кг" in t["body_paint"]
    assert t["tire_front_left"].startswith("Шины 195/55 R16") and "Отзывы" not in t["tire_front_left"]
    assert "Дисковые" in t["brake_front_left"]
    assert "Климат" not in t.get("dashboard", "")            # «—» — опции нет
    assert t["wipers"] == "Датчик дождя"
    assert "металлик" not in t.get("body_paint", "")         # «опция» — у экземпляра может не быть
