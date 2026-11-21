import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
path = root / "data/catalog/cars.json"
data = json.loads(path.read_text(encoding="utf-8-sig"))

if not any(car.get("id") == 21 for car in data):
    data.append({
        "id": 21, "brand": "Kia", "model": "Sportage", "trim": "GT-Line",
        "year": 2023, "body": "suv", "seats": 5, "trunk_l": 587,
        "power_hp": 180, "consumption_l100": 8.0, "drive": "awd",
        "transmission": "auto", "fuel": "petrol", "price": 4200000,
        "colors": ["white", "black", "gray", "blue"],
        "tags": ["family", "road-trip", "off-road"],
        "specs": {
            "accel_0_100": 9.5, "top_speed": 201, "clearance_mm": 181,
            "length_mm": 4515, "width_mm": 1865, "height_mm": 1650,
            "weight_kg": 1700, "track_mm": 1620, "tank_l": 54,
            "led_headlights": True, "led_taillights": True, "airbags": 7,
            "esp": True, "screen_in": 12.3, "climate": "двухзонный климат",
            "electric_handbrake": True, "power_mirrors": True, "chassis": "suv",
            "moose_real": {"speed": 80, "label": "Teknikens Värld: лосиный тест Kia Sportage", "url": "https://www.teknikensvarld.se/"},
            "crash_real": {"index": 0.88, "label": "Euro NCAP: 5★ (2022 Kia Sportage)", "url": "https://www.euroncap.com/en/results/kia/sportage/44215"},
        },
        "listing_url": None, "image_key": None,
        "model_key": "models/kia_sportage_parts.glb",
        "parts_key": "models/kia_sportage_parts.json",
        "model_credit": "2023 Kia Sportage GT-Line — Ddiaz Design (Sketchfab), CC BY-NC-SA 4.0",
        "hotspots": {
            "body_paint": "Кроссовер · 5 мест · полный привод",
            "hood": "Бензиновый двигатель · 180 л.с.", "front_bumper": "Клиренс 181 мм",
            "rear_bumper": "Длина 4515 мм", "roof": "Высота 1650 мм",
            "windshield": "Ширина 1865 мм", "trunk_lid": "Багажник 587 л",
            "door_front_left": "Передняя дверь", "door_front_right": "Передняя дверь",
            "door_rear_left": "Задняя дверь", "door_rear_right": "Задняя дверь",
            "mirror_left": "Боковое зеркало", "mirror_right": "Боковое зеркало",
            "wheel_front_left": "Полный привод · ESP", "wheel_front_right": "Полный привод · ESP",
            "wheel_rear_left": "Полный привод · ESP", "wheel_rear_right": "Полный привод · ESP",
        },
    })
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("Added Kia Sportage; catalog size:", len(data))
else:
    print("Kia Sportage already exists; catalog size:", len(data))
