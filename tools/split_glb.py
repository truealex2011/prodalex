"""Разбить GLB машины на детали: python tools/split_glb.py машина.glb --out папка [--length-mm 4440]

Пишет <out>/car_parts.glb и <out>/parts.json и печатает отчёт: время, найденные детали, размеры модели.
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.splitter import SplitError, split_glb  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("file", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--length-mm", type=float)
    a = ap.parse_args()
    t0 = time.time()
    try:
        glb, manifest = split_glb(a.file.read_bytes(), a.length_mm / 1000 if a.length_mm else None)
    except SplitError as e:
        sys.exit(f"не удалось разбить: {e}")
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "car_parts.glb").write_bytes(glb)
    (a.out / "parts.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"готово за {time.time() - t0:.2f} с | куски {manifest['split']['pieces']}, размечено {manifest['split']['assigned']} | "
          f"нос {manifest['split']['front_sign']:+d} по оси {manifest['split']['front_axis']}")
    print("размеры модели:", {k: round(v, 3) for k, v in manifest["measured"].items() if v})
    for p in manifest["parts"]:
        print(f"  {p['id']:20} {p['triangles']:>7} треуг.  x[{p['bbox']['min'][0]:+.2f}..{p['bbox']['max'][0]:+.2f}] "
              f"y[{p['bbox']['min'][1]:+.2f}..{p['bbox']['max'][1]:+.2f}] z[{p['bbox']['min'][2]:+.2f}..{p['bbox']['max'][2]:+.2f}]")
