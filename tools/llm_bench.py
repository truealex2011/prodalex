"""Проверка локальной нейросети на реальных запросах: скорость и качество разбора и анкеты.

    python tools/llm_bench.py
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import llm  # noqa: E402
from app.questions import SLOTS  # noqa: E402

TEXTS = ["нас пятеро плюс бабушка, ездим на дачу с кучей вещей, бюджет до 2 млн, автомат",
         "хочу яркую быструю машину чтобы было весело, не дороже 3 млн"]

if __name__ == "__main__":
    print("нейросеть доступна:", llm.available(), "| exe:", llm.llama_exe(), "| модель:", llm.model_file())
    for t in TEXTS:
        t0 = time.time()
        res = llm.understand(t)
        print(f"\n[{time.time() - t0:.1f} c] разбор: «{t}»\n  ", json.dumps(res, ensure_ascii=False))
        fields = {k: SLOTS[k]["text"]["novice"] for k in ("purpose", "seats", "transmission", "color", "body") if "novice" in SLOTS[k]["text"]}
        t0 = time.time()
        qs = llm.ask_questions(t, "", "novice", fields, 3)
        print(f"[{time.time() - t0:.1f} c] анкета:", json.dumps(qs, ensure_ascii=False))
