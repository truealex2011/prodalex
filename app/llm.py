"""Локальная нейросеть для понимания описания машины (Qwen2.5-1.5B-Instruct через llama.cpp).

Как в проекте «парсинк»: модель работает на этом же компьютере, без облака и ключей, а её ответ жёстко ограничен
грамматикой (GBNF) — на выходе всегда валидный JSON с заранее разрешёнными полями и значениями.

Нейросеть делает две вещи:
  1) пересказывает желание клиента простыми словами (`goal`) — то, что мы показываем пользователю как «Я понял так…»;
  2) раскладывает описание на профиль подбора (бюджет, места, кузов, коробка, привод, топливо, цвет, пожелания).
Машину она не выбирает и фактов о машинах не придумывает: подбор — детерминированный код (recommender.py),
а числовые ограничения из текста дополнительно проверяются правилами (nlu.py).

Установка модели и llama.cpp: tools/setup_llm.ps1 (≈1,1 ГБ). Без неё сайт работает на правилах (nlu.parse).
"""
import json
import logging
import os
import re
import subprocess
import threading

from . import config  # noqa: F401  (подхватывает .env раньше, чем читаются PARSER_URL, AUTOCHOICE_* и остальные переменные)
from functools import lru_cache
from pathlib import Path

log = logging.getLogger("llm")

HOME = Path(os.getenv("AUTOCHOICE_HOME") or Path.home() / ".cache" / "autochoice")
PARSING_HOME = Path.home() / ".cache" / "avtoradar"  # те же файлы, если уже стоит проект «парсинк»
TIMEOUT = int(os.getenv("AUTOCHOICE_LLM_TIMEOUT", "75"))
_lock = threading.Lock()  # одна генерация за раз: на CPU параллельные запуски только мешают друг другу

BODIES = ["sedan", "hatch", "suv", "wagon", "minivan"]
COLORS = ["red", "white", "black", "blue", "gray", "green", "silver", "yellow", "orange", "brown"]
NEEDS = ["low_consumption", "low_power", "powerful", "big_trunk", "offroad", "city", "family", "comfort", "cheap", "first_car", "premium"]

SYSTEM = (
    "Ты помощник по подбору легковых автомобилей. Заполни профиль только тем, что прямо сказано в описании клиента; "
    "что не сказано — 0, any или []. goal — одно предложение простыми словами о том, чего хочет клиент. "
    "needs — из: low_consumption, low_power, powerful, big_trunk, offroad, city, family, comfort, cheap, first_car, premium (дорогая, статусная, премиум). "
    "Не меняй желание на противоположное. Только JSON."
)
EXAMPLE_IN = "Хочу большой надёжный внедорожник для рыбалки, бюджет до 4 млн"
EXAMPLE_OUT = ('{"goal":"Ищет вместительный надёжный внедорожник для поездок на рыбалку, до 4 млн рублей",'
               '"budget_rub":4000000,"seats_min":0,"body":["suv"],"transmission":"any","drive":"awd","fuel":"any","needs":["offroad","big_trunk"]}')


def _alt(values: list[str]) -> str:
    return " | ".join(f'"\\"{v}\\""' for v in values)


GRAMMAR = rf'''root ::= "{{" ws "\"goal\":" ws str "," ws "\"budget_rub\":" ws int "," ws "\"seats_min\":" ws int "," ws "\"body\":" ws bodies "," ws "\"transmission\":" ws trans "," ws "\"drive\":" ws drive "," ws "\"fuel\":" ws fuel "," ws "\"needs\":" ws needs ws "}}"
str ::= "\"" [^"\\\n]{{12,150}} "\""
int ::= [0-9]{{1,9}}
bodies ::= "[" ws (bodyv (ws "," ws bodyv)*)? ws "]"
bodyv ::= {_alt(BODIES)}
trans ::= {_alt(["auto", "manual", "any"])}
drive ::= {_alt(["awd", "any"])}
fuel ::= {_alt(["any", "petrol", "diesel", "hybrid", "electric"])}
needs ::= "[" ws (needv (ws "," ws needv)*)? ws "]"
needv ::= {_alt(NEEDS)}
ws ::= [ \t\n]*'''


def _first(*paths) -> Path | None:
    return next((p for p in paths if p and Path(p).is_file()), None)


def llama_exe() -> Path | None:
    name = "llama-completion.exe" if os.name == "nt" else "llama-completion"
    return _first(os.getenv("AUTOCHOICE_LLAMA"), HOME / "llama" / name, PARSING_HOME / "llama" / name)


def model_file() -> Path | None:
    return _first(os.getenv("AUTOCHOICE_MODEL"), HOME / "models" / "planner.gguf",
                  PARSING_HOME / "models" / "planner-ollama.gguf", PARSING_HOME / "models" / "planner.gguf")


def available() -> bool:
    return bool(llama_exe() and model_file())


def _prompt(text: str) -> str:
    clean = lambda s: re.sub(r"[<>|]", " ", s)
    return (f"<|im_start|>system\n{SYSTEM}<|im_end|>\n<|im_start|>user\n{clean(EXAMPLE_IN)}<|im_end|>\n"
            f"<|im_start|>assistant\n{EXAMPLE_OUT}<|im_end|>\n<|im_start|>user\n{clean(text[:600])}<|im_end|>\n<|im_start|>assistant\n")


def _run(prompt: str) -> str:
    cmd = [str(llama_exe()), "-m", str(model_file()), "-p", prompt, "--grammar", GRAMMAR, "--no-display-prompt",
           "--no-conversation", "--simple-io", "--no-warmup", "-ngl", "0", "-t", _threads(),
           "-c", "2048", "-n", "360", "--temp", "0", "--seed", "42", "-r", "<|im_end|>"]
    with _lock:
        out = subprocess.run(cmd, capture_output=True, timeout=TIMEOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return out.stdout.decode("utf-8", "replace")


def first_json(text: str, opener: str):
    """Первый JSON-объект/массив из вывода модели; всё после него (служебный текст llama.cpp) игнорируется."""
    i = text.find(opener)
    if i < 0:
        raise ValueError("в ответе нет JSON")
    return json.JSONDecoder().raw_decode(text[i:])[0]



# Маленькая модель иногда подмешивает в ответ содержимое примера из подсказки. Поэтому каждое значение,
# которое она вернула, принимается только если в описании клиента есть признак этого значения (основы слов).
HARD_EVIDENCE = {
    "transmission": r"автомат|механик|акпп|мкпп|вариатор|коробк|переключ|сцеплен",
    "drive": r"привод|4x4|4х4|полн|бездорож|снег|грязь|дач|внедорож|грунт|клиренс",
    "fuel": r"бензин|дизел|гибрид|электро|заправ",
    "seats": r"\d\s*(мест|человек|чел\b|пассаж)|мест|человек|пят|шест|сем|дет|ребен|пассажир|двое|трое|четв|вся |друз|команд|бабуш|дед",
    "budget": r"\d[\d\s.,]*\s*(млн|тыс|руб|₽|к\b|миллион|лям)|бюджет|дешев|недорог|до\s*\d|за\s*\d|млн|тыс|рубл|₽",
}
# кузов принимается только если клиент назвал именно его: «большая семья» кузова не определяет
BODY_EVIDENCE = {
    "sedan": r"седан", "hatch": r"х[еэ]тч|лифтбек", "wagon": r"универсал",
    "suv": r"кроссовер|внедорож|джип|паркетник", "minivan": r"минив[эе]н|микроавтобус|\bвэн",
}
NEED_EVIDENCE = {
    "low_consumption": r"эконом|расход|бензин|топлив|\bест|мало ел|не жр|заправ",
    "low_power": r"спокойн|не мощн|тих|плавн|неспешн|не быстр|новичок|перв",
    "powerful": r"мощн|быстр|динамич|разгон|драйв|спорт|лошад|гонк|резв|крут|эффект",
    "big_trunk": r"багажник|вещ|чемодан|коляск|груз|дач|поклаж|рыбал|вмест|кучей",
    "offroad": r"бездорож|дач|грунт|грязь|снег|лес|рыбал|охот|деревн|клиренс|разбит",
    "city": r"город|пробк|парков|двор|тесн|компакт|офис|работ|такси",
    "family": r"сем|дет|ребен|жен|муж|бабуш|дед|родител|пассажир|пят|шест|вся |друз|мам|пап",
    "comfort": r"комфорт|трасс|дальн|поездк|путешеств|отпуск|плавн|тих|удобн|длинн",
    "cheap": r"дешев|недорог|бюджет|эконом|не дорог|подешев|цен|до \d|не дороже",
    "first_car": r"перв|новичок|права|стаж|учусь|только получил",
    "premium": r"дорог|премиум|люкс|роскош|элит|престиж|статус|суперкар|спорткар|богат|эксклюзив|крут",
}


def _seen(pattern: str, text: str) -> bool:
    return bool(re.search(pattern, text))

def _as_int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _stems(s: str) -> set:
    return {w[:5] for w in re.findall(r"[а-яa-z]{4,}", s.lower().replace("ё", "е"))}


def _goal_ok(goal: str, text: str) -> bool:
    """Пересказ годится, только если опирается на слова клиента и не повторяет пример из подсказки
    («попа» -> «Ищет вместительный надёжный внедорожник для поездок на рыбалку…»)."""
    g, t = _stems(goal), _stems(text)
    leaked = (_stems(EXAMPLE_OUT.split('"budget_rub"')[0]) - t - {"ищет"}) & g
    return bool(g & t) and not leaked


def clean(raw: dict, text: str) -> dict:
    """Приводит ответ модели к безопасному виду: разрешённые значения, разумные числа и подтверждение текстом описания."""
    t = text.lower().replace("ё", "е")
    t = re.sub(r"(?<![а-яa-z0-9])(?:[a-z]{1,3}[\s-]?\d{1,3}|[а-я]\d{1,3})(?![а-яa-z0-9])", " ", t)     # «х5», «x5», «m3» — модели, а не числа
    hard: dict = {}
    budget = _as_int(raw.get("budget_rub"))
    if 100_000 <= budget <= 100_000_000 and re.search(r"\d", text) and _seen(HARD_EVIDENCE["budget"], t):
        hard["budget_max"] = budget
    seats = _as_int(raw.get("seats_min"))
    if 2 <= seats <= 9 and _seen(HARD_EVIDENCE["seats"], t):
        hard["seats_min"] = seats
    bodies = [b for b in raw.get("body") or [] if b in BODIES]
    bodies = [b for b in bodies if _seen(BODY_EVIDENCE[b], t)]
    if bodies:
        hard["body"] = list(dict.fromkeys(bodies))
    if raw.get("transmission") in ("auto", "manual") and _seen(HARD_EVIDENCE["transmission"], t):
        hard["transmission"] = raw["transmission"]
    if raw.get("drive") == "awd" and _seen(HARD_EVIDENCE["drive"], t):
        hard["drive"] = "awd"
    if raw.get("fuel") in ("petrol", "diesel", "hybrid", "electric") and _seen(HARD_EVIDENCE["fuel"], t):
        hard["fuel"] = raw["fuel"]
    nice = {k: 0.7 for k in raw.get("needs") or [] if k in NEEDS and _seen(NEED_EVIDENCE[k], t)}
    if "low_power" in nice:
        nice.pop("powerful", None)
    goal = re.sub(r"\s+", " ", str(raw.get("goal") or "")).strip()[:260]
    if goal and not _goal_ok(goal, text):
        goal = ""
    return {"hard": hard, "nice": nice, "color": None, "goal": goal or None}   # цвет определяют правила (nlu.parse)


# обращения и вежливые слова — не часть запроса («хочу ролс ройс брат» не про модель «Брат»)
_SLANG = re.compile(r"(?<![а-яёa-z])(?:брат(?:ан|ишка|ух\w*|ик)?|бро|братва|пж|плиз|пожалуйста|чувак|друг)(?![а-яёa-z])", re.I)


def _threads() -> str:
    """Потоки нейросети: все ядра по умолчанию (LLM_THREADS переопределяет); процессор делится с браузером парсера, поэтому не больше 8."""
    return str(int(os.getenv("LLM_THREADS") or min(8, os.cpu_count() or 4)))


def strip_slang(text: str) -> str:
    return re.sub(r"\s{2,}", " ", _SLANG.sub(" ", text or "")).strip(" ,.!")


@lru_cache(maxsize=128)
def understand(text: str) -> dict | None:
    """Описание клиента -> {"hard", "nice", "color", "goal"}. None — нейросеть недоступна или не справилась."""
    text = strip_slang(text)
    if not available():
        return None
    try:
        out = _run(_prompt(text))
        return clean(first_json(out, "{"), text)
    except Exception as e:      # таймаут, битый вывод, сбой процесса — подбор продолжается на правилах
        log.warning("нейросеть не ответила: %s", e)
        return None


# ───────────── анкета: какие уточнения нужны и как их сформулировать ─────────────
QA_SYSTEM = (
    "Ты опытный автоконсультант в салоне: ведёшь клиента к выбору, как живой человек, и подстраиваешься под то, насколько он разбирается. "
    "Тебе дают описание клиента и поля анкеты. Правила. "
    "1) Не спрашивай то, что клиент уже назвал (марка, модель, бюджет, цвет, число мест) или что понятно из его слов. "
    "2) ask — 0-2 поля из списка, которые сильнее всего сузят выбор; вопрос переформулируй под слова клиента. "
    "3) custom — ОДИН свой вопрос о главном мотиве клиента, с 3-4 вариантами ответа, каждому варианту задай need из списка. "
    "{mode} Только JSON."
)
QA_MODE = {
    "novice": ("Клиент — НОВИЧОК, в технике не разбирается. Говори простым языком, без терминов (не «привод», «клиренс», «объём двигателя»). "
               "Спрашивай про жизнь: кто ездит, куда и как часто, чего он боится (поломок, дорогого обслуживания, тесной парковки). "
               "Варианты ответов — бытовые, понятные без знаний о машинах."),
    "expert": ("Клиент — ЭКСПЕРТ, разбирается в технике. Говори точно и коротко. Спрашивай про двигатель и топливо, коробку, привод, "
               "динамику, подвеску и клиренс, пробег и состояние. Варианты ответов — технические."),
}
QA_EXAMPLE_IN = {
    "novice": ("Поля: body — Какой кузов вам нравится?; color — Какой цвет кузова вам нравится?\n"
               "Описание: Хочу надёжную машину для поездок на рыбалку зимой\nУже известно: ничего"),
    "expert": ("Поля: drive — Нужен полный привод?; transmission — Какая коробка передач нужна?\n"
               "Описание: Нужен надёжный кроссовер для зимней эксплуатации\nУже известно: ничего"),
}
QA_EXAMPLE_OUT = {
    "novice": ('{"ask":[{"field":"body","text":"Нужен ли вам кузов повышенной проходимости?"}],'
               '"custom":[{"text":"Что для вас главное в зимней поездке?","options":['
               '{"label":"Чтобы не застрять","need":"offroad"},{"label":"Тепло и комфорт","need":"comfort"},'
               '{"label":"Вместительный багажник","need":"big_trunk"}]}]}'),
    "expert": ('{"ask":[{"field":"drive","text":"Нужен ли полный привод для зимней эксплуатации?"}],'
               '"custom":[{"text":"Что приоритетнее в зимней эксплуатации?","options":['
               '{"label":"Тяга и проходимость","need":"offroad"},{"label":"Климат и комфорт салона","need":"comfort"},'
               '{"label":"Экономичный двигатель","need":"low_consumption"}]}]}'),
}


def _qa_grammar(fields: list[str]) -> str:
    return (r'root ::= "{" ws "\"ask\":" ws asks ws "," ws "\"custom\":" ws customs ws "}"' + "\n"
            r'asks ::= "[" ws (ask (ws "," ws ask)?)? ws "]"' + "\n"
            r'ask ::= "{" ws "\"field\":" ws (' + _alt(fields) + r') ws "," ws "\"text\":" ws str ws "}"' + "\n"
            r'customs ::= "[" ws (cust)? ws "]"' + "\n"
            r'cust ::= "{" ws "\"text\":" ws str ws "," ws "\"options\":" ws "[" ws opt (ws "," ws opt){2,3} ws "]" ws "}"' + "\n"
            r'opt ::= "{" ws "\"label\":" ws lstr ws "," ws "\"need\":" ws needv ws "}"' + "\n"
            r'str ::= "\"" [^"\\\n]{10,150} "\""' + "\n"
            r'lstr ::= "\"" [^"\\\n]{3,38} "\""' + "\n"
            r'ws ::= [ \t\n]*' + "\n"
            r'needv ::= ' + _alt(NEEDS))


def ask_questions(description: str, known: str, mode: str, fields: dict[str, str], limit: int) -> dict | None:
    """Анкета под описание: {"ask": [{field, text}], "custom": [{text, options: [{label, need}]}]} или None, если модели нет.
    ask — нужные из фиксированных полей (с формулировкой под слова клиента), custom — собственный вопрос модели по сути запроса."""
    description = strip_slang(description)
    if not available():
        return None
    mode = "expert" if mode == "expert" else "novice"
    system = QA_SYSTEM.format(mode=QA_MODE[mode])
    catalog = "; ".join(f"{k} — {v}" for k, v in fields.items()) or "нет"
    user = (f"Поля: {catalog}\nОписание: {description[:500]}\nУже известно: {known[:400] or 'ничего'}\n"
            f"need — только из: {', '.join(NEEDS)}")
    clean = lambda s: re.sub(r"[<>|]", " ", s)
    prompt = (f"<|im_start|>system\n{system}<|im_end|>\n<|im_start|>user\n{clean(QA_EXAMPLE_IN[mode])}<|im_end|>\n"
              f"<|im_start|>assistant\n{QA_EXAMPLE_OUT[mode]}<|im_end|>\n<|im_start|>user\n{clean(user)}<|im_end|>\n<|im_start|>assistant\n")
    cmd = [str(llama_exe()), "-m", str(model_file()), "-p", prompt, "--grammar", _qa_grammar(list(fields) or ["body"]), "--no-display-prompt",
           "--no-conversation", "--simple-io", "--no-warmup", "-ngl", "0", "-t", _threads(), "-c", "2048",
           "-n", "380", "--temp", "0.2", "--seed", "7", "-r", "<|im_end|>"]
    try:
        with _lock:
            out = subprocess.run(cmd, capture_output=True, timeout=TIMEOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        data = first_json(out.stdout.decode("utf-8", "replace"), "{")
        asks = [{"field": x["field"], "text": x["text"]} for x in data.get("ask", []) if x.get("field") in fields]
        custom = [{"text": c["text"], "options": [{"label": o["label"], "need": o["need"]} for o in c["options"] if o["need"] in NEEDS]}
                  for c in data.get("custom", [])]
        return {"ask": asks[:limit], "custom": [c for c in custom if len(c["options"]) >= 2][:1]}
    except Exception as e:
        log.warning("нейросеть не составила анкету: %s", e)
        return None


# ───────────── планировщик: конкретные модели под описание (как plan() в «парсинке») ─────────────
SUGGEST_SYSTEM = ("Ты эксперт по автомобилям в России. Назови 6 конкретных моделей легковых машин (марка и модель латиницей), "
                  "которые лучше всего подходят под описание клиента и продаются в России новыми или с пробегом. "
                  "Учитывай класс, цену и характер, которых он хочет. Только JSON.")
SUGGEST_IN = "Нужна недорогая надёжная машина для семьи с двумя детьми"
SUGGEST_OUT = '{"models":["Lada Largus","Skoda Octavia","Kia Rio","Hyundai Solaris","Renault Logan","Volkswagen Polo"]}'


_suggest_cache: dict[str, list[str] | None] = {}


def prefetch_suggest(description: str, known: str = "") -> None:
    """Подбор моделей начинается, пока клиент отвечает на вопросы: к его «Показать машину» результат уже готов."""
    key = strip_slang(description).lower()[:300]
    if key and key not in _suggest_cache and available():
        threading.Thread(target=suggest_models, args=(description, known), daemon=True).start()


def suggest_models(description: str, known: str = "") -> list[str] | None:
    """Названия моделей, которые нейросеть считает подходящими; None — модели нет или она не ответила."""
    description = strip_slang(description)
    key = description.lower()[:300]
    if key in _suggest_cache:
        return _suggest_cache[key]
    if not available():
        return None
    grammar = (r'root ::= "{" ws "\"models\":" ws "[" ws item (ws "," ws item){2,5} ws "]" ws "}"' + "\n"
               r'item ::= "\"" [A-Za-z0-9 .\-]{3,40} "\""' + "\n" + r'ws ::= [ \t\n]*')
    clean = lambda s: re.sub(r"[<>|]", " ", s)
    user = f"{description[:500]}" + (f"\nПонято: {known[:300]}" if known else "")
    prompt = (f"<|im_start|>system\n{SUGGEST_SYSTEM}<|im_end|>\n<|im_start|>user\n{SUGGEST_IN}<|im_end|>\n<|im_start|>assistant\n"
              f"{SUGGEST_OUT}<|im_end|>\n<|im_start|>user\n{clean(user)}<|im_end|>\n<|im_start|>assistant\n")
    cmd = [str(llama_exe()), "-m", str(model_file()), "-p", prompt, "--grammar", grammar, "--no-display-prompt", "--no-conversation",
           "--simple-io", "--no-warmup", "-ngl", "0", "-t", _threads(), "-c", "1536", "-n", "120",
           "--temp", "0", "--seed", "42", "-r", "<|im_end|>"]
    try:
        with _lock:
            if key in _suggest_cache:                       # пока ждали очередь, другой поток уже посчитал то же
                return _suggest_cache[key]
            out = subprocess.run(cmd, capture_output=True, timeout=TIMEOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        res = [str(x) for x in first_json(out.stdout.decode("utf-8", "replace"), "{").get("models", [])]
        _suggest_cache[key] = res
        return res
    except Exception as e:
        log.warning("нейросеть не предложила модели: %s", e)
        return None
