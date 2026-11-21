"""Марки, которые клиент называет в описании: по-русски («ролс ройс», «мерс», «бэха») и латиницей.

slug совпадает с адресом марки на Drom (drom.ru/<slug>/) и с первой частью путей снимка рынка («mercedes-benz/s-class»).
"""
import re

# slug: (название, регулярное выражение по тексту в нижнем регистре, ё -> е)
BRANDS = {
    "rolls-royce": ("Rolls-Royce", r"rolls[\s-]*royce|рол+[сз]?[\s-]*ро[йи]+[сз]|роллс|ролс"),
    "bentley": ("Bentley", r"bentley|бентли"),
    "ferrari": ("Ferrari", r"ferrari|феррари|ферари"),
    "lamborghini": ("Lamborghini", r"lamborghini|ламборгини|ламба|ламбо\b"),
    "mclaren": ("McLaren", r"mclaren|макларен"),
    "aston_martin": ("Aston Martin", r"aston\s*martin|астон\s*мартин"),
    "maserati": ("Maserati", r"maserati|мазерати"),
    "bugatti": ("Bugatti", r"bugatti|бугатти"),
    "maybach": ("Maybach", r"maybach|майбах"),
    "porsche": ("Porsche", r"porsche|порше|порш\b"),
    "mercedes-benz": ("Mercedes-Benz", r"mercedes|мерседес|мерс\b|мерина|гелик|гелендваген|амг\b|amg\b"),
    "bmw": ("BMW", r"\bbmw\b|бмв|бэха|бэху|беха|бумер"),
    "audi": ("Audi", r"\baudi\b|ауди"),
    "lexus": ("Lexus", r"lexus|лексус"),
    "land_rover": ("Land Rover", r"land\s*rover|range\s*rover|ленд\s*ровер|рэндж|рендж|ровер"),
    "genesis": ("Genesis", r"genesis|генезис"),
    "infiniti": ("Infiniti", r"infiniti|инфинити"),
    "volvo": ("Volvo", r"volvo|вольво"),
    "jaguar": ("Jaguar", r"jaguar|ягуар"),
    "cadillac": ("Cadillac", r"cadillac|кадиллак|кадилак"),
    "tesla": ("Tesla", r"tesla|тесла"),
    "toyota": ("Toyota", r"toyota|тойота|таета|land\s*cruiser|ленд\s*крузер|крузак|камри|королла|хайлендер"),
    "lada": ("Lada", r"\blada\b|лада\b|ладу\b|ваз\b|жигул"),
    "kia": ("Kia", r"\bkia\b|киа\b"),
    "hyundai": ("Hyundai", r"hyundai|хендай|хундай|хюндай|хёндай"),
    "volkswagen": ("Volkswagen", r"volkswagen|\bvw\b|фольксваген|фольц"),
    "skoda": ("Skoda", r"skoda|шкода"),
    "renault": ("Renault", r"renault|рено\b"),
    "nissan": ("Nissan", r"nissan|ниссан|нисан"),
    "mazda": ("Mazda", r"mazda|мазда|мазду"),
    "honda": ("Honda", r"honda|хонда"),
    "mitsubishi": ("Mitsubishi", r"mitsubishi|митсубиси|мицубиси|митсубиши"),
    "subaru": ("Subaru", r"subaru|субару"),
    "ford": ("Ford", r"\bford\b|форд"),
    "chevrolet": ("Chevrolet", r"chevrolet|шевроле|шеви"),
    "haval": ("Haval", r"haval|хавал|хавейл"),
    "geely": ("Geely", r"geely|джили"),
    "chery": ("Chery", r"chery|чери\b"),
    "exeed": ("Exeed", r"exeed|эксид"),
    "tank": ("Tank", r"\btank\b|танк\s*\d"),
    "lixiang": ("Li Auto", r"li\s*auto|lixiang|лисян|ли\s*сян"),
    "zeekr": ("Zeekr", r"zeekr|зикр"),
    "jeep": ("Jeep", r"\bjeep\b|джип\s+(?:гранд|вранглер|чероки)"),
    "uaz": ("УАЗ", r"\buaz\b|уаз|буханк|патриот"),
}
# сверхдорогие марки: само упоминание — запрос на премиум
LUXURY = {"rolls-royce", "bentley", "ferrari", "lamborghini", "mclaren", "aston_martin", "maserati", "bugatti", "maybach", "porsche"}
PREMIUM = LUXURY | {"mercedes-benz", "bmw", "audi", "lexus", "land_rover", "genesis", "infiniti", "volvo", "jaguar", "cadillac", "tesla"}


def find_brands(text: str) -> list[str]:
    """Марки, названные в тексте (без отрицаний вида «не лада»)."""
    t = text.lower().replace("ё", "е")
    out = []
    for slug, (_, rx) in BRANDS.items():
        for m in re.finditer(rx, t):
            if not re.search(r"(?:\bне|\bкроме|\bбез|\bлишь бы не)\s+$", t[max(0, m.start() - 12):m.start()]):
                out.append(slug)
                break
    if "maybach" in out and "mercedes-benz" not in out:
        out.append("mercedes-benz")          # на рынке Maybach продаётся как Mercedes-Maybach
    return out


def slug_of(name: str) -> str:
    """«Rolls-Royce Cullinan» / «LADA» / «Mercedes-Benz» -> slug марки."""
    t = (name or "").lower()
    for slug, (title, rx) in BRANDS.items():
        if t.startswith(title.lower()) or re.match(rx, t):
            return slug
    return t.split()[0] if t else ""


def title_of(slug: str) -> str:
    return BRANDS.get(slug, (slug.replace("_", " ").title(),))[0]
