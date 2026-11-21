"""Сверка автоматического разбиения с готовой ручной разметкой (parts.json рядом с моделью).

    python tools/split_check.py app/static/media/models/lada_vesta_parts.glb

Для каждой детали, которая есть и в эталоне, и в результате, считается пересечение габаритных коробок (IoU).
Модель, уже разбитая на детали, разбивается заново, как если бы это был «сырой» GLB: так проверяется качество алгоритма.
"""
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.splitter import SplitError, split_glb  # noqa: E402


def iou(a, b):
    lo, hi = np.maximum(a["min"], b["min"]), np.minimum(a["max"], b["max"])
    inter = np.prod(np.maximum(hi - lo, 0))
    union = np.prod(np.array(a["max"]) - a["min"]) + np.prod(np.array(b["max"]) - b["min"]) - inter
    return float(inter / union) if union > 0 else 0.0


if __name__ == "__main__":
    glb = Path(sys.argv[1])
    truth = json.loads(glb.with_suffix(".json").read_text(encoding="utf-8"))
    t0 = time.time()
    try:
        _, ours = split_glb(glb.read_bytes())
    except SplitError as e:
        sys.exit(f"не удалось: {e}")
    print(f"{glb.name}: разбито за {time.time() - t0:.2f} с, деталей {len(ours['parts'])}")
    mine = {p["id"]: p for p in ours["parts"]}
    group = "interior" if "--interior" in sys.argv else "exterior"
    ref = {p["id"]: p for p in truth["parts"] if p.get("group", "exterior") == group}
    # эталон может быть записан в другой системе осей (нос +Z, +X, −X): приводим к каноническому виду, как наш результат
    rot = {"-Z": lambda v: v, "+Z": lambda v: [-v[0], v[1], -v[2]], "+X": lambda v: [-v[2], v[1], v[0]], "-X": lambda v: [v[2], v[1], -v[0]]}[truth.get("front", "-Z")]
    for part in ref.values():
        a_, b_ = rot(part["bbox"]["min"]), rot(part["bbox"]["max"])
        part["bbox"] = dict(min=np.minimum(a_, b_).tolist(), max=np.maximum(a_, b_).tolist())
    # эталон и результат могут быть смещены (разные нули координат): сравниваем в долях габаритов кузова
    def norm(parts):
        lo = np.min([p["bbox"]["min"] for p in parts.values()], 0)
        hi = np.max([p["bbox"]["max"] for p in parts.values()], 0)
        return {k: dict(min=(np.array(p["bbox"]["min"]) - lo) / (hi - lo), max=(np.array(p["bbox"]["max"]) - lo) / (hi - lo)) for k, p in parts.items()}
    n_mine = {k: dict(min=np.array(v["bbox"]["min"]), max=np.array(v["bbox"]["max"])) for k, v in mine.items() if k in ref}
    n_ref = {k: dict(min=np.array(v["bbox"]["min"]), max=np.array(v["bbox"]["max"])) for k, v in ref.items()}   # общая система координат
    scores = {k: iou(n_mine[k], n_ref[k]) for k in n_mine if k in n_ref}
    for k in sorted(scores, key=scores.get):
        print(f"  {k:20} IoU {scores[k]:.2f}")
    print(f"среднее по {len(scores)} общим деталям: {np.mean(list(scores.values())):.2f}; "
          f"нет в результате: {sorted(set(ref) - set(mine))}; лишние: {sorted(set(mine) - set(ref))}")
