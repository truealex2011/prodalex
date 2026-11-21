"""Модель машины, названная в тексте («бмв х5», «bmw x5», «лада веста», «ленд крузер прадо»).

Названная модель — обязательное условие: «хочу X5» не заменяется на X7 только потому, что оба премиальные.
Модели берутся из снимка рынка (пути вида «bmw/x5»); для кодовых названий вне снимка («bmw x7») путь строится по коду.
"""
import re

# похожие по виду русские и латинские буквы: в названиях моделей «х5», «а4», «м5» пишут кириллицей
LOOKALIKE = str.maketrans("хаесорктмвну", "xaecopktmbhy")
# русские названия моделей -> латиница
RU_MODELS = {
    "веста": "vesta", "гранта": "granta", "ларгус": "largus", "калина": "kalina", "приора": "priora", "нива": "niva", "лада": "lada",
    "камри": "camry", "королла": "corolla", "харриер": "harrier", "хариер": "harrier", "крузер": "cruiser", "ленд": "land", "прадо": "prado",
    "рав": "rav", "приус": "prius", "солярис": "solaris", "крета": "creta", "рио": "rio", "октавия": "octavia", "рапид": "rapid",
    "поло": "polo", "тигуан": "tiguan", "пассат": "passat", "дастер": "duster", "логан": "logan", "каптюр": "kaptur", "кашкай": "qashqai",
    "спортейдж": "sportage", "соренто": "sorento", "церато": "cerato", "туксон": "tucson", "джолион": "jolion", "тиго": "tiggo",
    "класс": "class", "класса": "class", "серии": "series", "серия": "series", "икс": "x", "гелик": "g class", "гелендваген": "g class",
    "кроссовер": "", "купе": "coupe", "кросс": "cross",
}
CODE = re.compile(r"(?<![a-z0-9])([a-z]{1,2})[\s-]?(\d{1,3})(?![a-z0-9])")      # x7, rs6, m5, q8, a4


def _variants(text: str) -> list[str]:
    t = text.lower().replace("ё", "е")
    ru = " ".join(RU_MODELS.get(w, w) for w in re.findall(r"[а-яa-z0-9-]+", t))
    return [t, t.translate(LOOKALIKE), ru, ru.translate(LOOKALIKE)]


def _token_rx(tok: str) -> str:
    parts = re.findall(r"[a-z]+|\d+", tok)                       # x5 -> x[\s-]?5
    return r"[\s-]?".join(re.escape(p) for p in parts)


def _model_rx(model: str) -> re.Pattern:
    toks = [x for x in re.split(r"[-_]", model.lower()) if x]
    return re.compile(r"(?<![a-z0-9])" + r"[\s_-]*".join(_token_rx(x) for x in toks) + r"(?![a-z0-9])")


def find_models(text: str, brand_slugs: list[str], paths: list[str]) -> list[str]:
    """Пути моделей («bmw/x5»), названных в тексте. Модель ищется только среди названных марок."""
    if not brand_slugs:
        return []
    found = []                                                   # (длина совпадения, начало, конец, путь)
    for v in _variants(text):
        for path in paths:
            brand, _, model = path.partition("/")
            if brand not in brand_slugs or not model:
                continue
            if re.fullmatch(r"\d{2,5}", model):                  # старые ВАЗы «2107»: только если названы цифрами с маркой
                continue
            m = _model_rx(model).search(v)
            if m:
                found.append((len(m.group(0)), m.start(), m.end(), path))
    out, spans = [], []
    for ln, a, b, path in sorted(found, key=lambda x: -x[0]):    # «land cruiser prado» важнее «land cruiser»
        if path in out or any(a < e and b > s for s, e in spans):
            continue
        out.append(path)
        spans.append((a, b))
    if out or len(brand_slugs) != 1:
        return out[:3]
    brand = brand_slugs[0]                                       # модели нет в снимке рынка: берём кодовое название («bmw x7», «audi rs6»)
    for v in _variants(text)[1:2]:
        m = CODE.search(v)
        if m and brand in ("bmw", "audi", "mercedes-benz", "porsche", "lexus", "volvo", "tesla", "land_rover", "genesis", "infiniti"):
            return [f"{brand}/{m.group(1)}{m.group(2)}"]
    return []


def model_title(path: str) -> str:
    brand, _, model = path.partition("/")
    return model.replace("_", " ").replace("-", " ").upper() if len(model) <= 4 else model.replace("_", " ").replace("-", " ").title()


def title_matches(title: str, path: str) -> bool:
    """Заголовок объявления относится к модели path («BMW X5, 2020 год» ↔ «bmw/x5»)."""
    norm = lambda s: re.sub(r"[^a-z0-9]+", "", s.lower().translate(LOOKALIKE))      # noqa: E731
    model = path.partition("/")[2]
    return bool(model) and norm(model) in norm(title)
