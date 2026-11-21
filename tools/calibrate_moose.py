import json
import math
import sys

sys.path.insert(0, sys.argv[1])
import app.moose as m  # noqa: E402

cars = json.load(open(sys.argv[1] + "/data/catalog/cars.json", encoding="utf-8"))
real = [c for c in cars if c["specs"].get("moose_real")]


def model_v(car, k_roll, scale):
    s = car["specs"]
    chassis = m.CHASSIS.get(s.get("chassis"), 1.0)
    h = m._cg_height(car, s)
    ssf = (s["track_mm"] / 1000) / (2 * h)
    roll = ssf * k_roll * chassis
    grip = 0.9 * (1.0 if s["esp"] else 0.85) * chassis * max(0.85, min(1.0, 1.05 - (s["weight_kg"] - 1200) / 10000))
    return scale * m.REF_SPEED * math.sqrt(min(grip, roll) / m.REF_ACCEL)


best = None
for k in [x / 100 for x in range(55, 96)]:
    for sc in [x / 100 for x in range(90, 116)]:
        err = sum((model_v(c, k, sc) - c["specs"]["moose_real"]["speed"]) ** 2 for c in real) / len(real)
        if best is None or err < best[0]:
            best = (err, k, sc)
err, k, sc = best
print(f"best k_roll={k} scale={sc} rmse={math.sqrt(err):.1f} km/h")
for c in real:
    print(f"  {c['model']:12s} real {c['specs']['moose_real']['speed']}  old {model_v(c, 0.62, 1):.0f}  new {model_v(c, k, sc):.0f}")
print("no-real cars, new model:", {c["model"]: round(model_v(c, k, sc)) for c in cars if not c["specs"].get("moose_real")})
