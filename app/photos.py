"""Фото машины для случая, когда 3D-модели нет: Wikimedia Commons (свободные лицензии, автор указывается)."""
import json
import re
import urllib.parse
import urllib.request

from .sketchfab import UA, names_for, normal

API = "https://commons.wikimedia.org/w/api.php"
BAD_TITLE = re.compile(r"\b(engine|interior|dashboard|logo|badge|emblem|wheel|diagram|map|drawing|render|toy|model car|scale|lego|"
                       r"cutaway|crash test|ncap|cockpit|trunk|seat|steering|headlight|taillight|detail|stamp|brochure)\b")
GOOD_EXT = (".jpg", ".jpeg", ".png", ".webp")


def _strip(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or "")).strip()


def _query(q: str, limit: int = 30) -> list[dict]:
    params = urllib.parse.urlencode({
        "action": "query", "generator": "search", "gsrsearch": q, "gsrnamespace": 6, "gsrlimit": limit,
        "prop": "imageinfo", "iiprop": "url|size|extmetadata", "iiurlwidth": 1280, "format": "json"})
    req = urllib.request.Request(f"{API}?{params}", headers=UA)
    with urllib.request.urlopen(req, timeout=12) as r:
        data = json.loads(r.read().decode("utf-8"))
    return list((data.get("query") or {}).get("pages", {}).values())


def find_photos(car: dict, limit: int = 6) -> list[dict]:
    """До `limit` фото: горизонтальные, достаточного размера, со свободной лицензией, название содержит марку и модель."""
    brand = normal(car["brand"])
    model_tokens = [t for t in normal(car["model"]).split() if len(t) > 1 or t.isdigit()]
    trim_token = (normal(car.get("trim") or "").split() or [""])[0]
    found, seen = [], set()
    for name in names_for(car):
        for q in dict.fromkeys([f"{name} {car.get('year') or ''}".strip(), name]):
            try:
                pages = _query(q)
            except Exception:
                continue
            for p in sorted(pages, key=lambda p: p.get("index", 0)):
                title = p.get("title", "")
                info = (p.get("imageinfo") or [{}])[0]
                words = normal(title)
                if (title in seen or not title.lower().endswith(GOOD_EXT) or BAD_TITLE.search(words)
                        or brand.split()[0] not in words
                        or not any(t in words for t in model_tokens + ([trim_token] if len(trim_token) > 2 else []))):
                    continue
                meta = info.get("extmetadata") or {}
                lic = (meta.get("LicenseShortName") or {}).get("value", "")
                if not (lic.upper().startswith("CC") or "public domain" in lic.lower()):
                    continue
                w, h = info.get("width") or 0, info.get("height") or 0
                if w < 900 or w < h * 1.1:
                    continue
                seen.add(title)
                found.append({
                    "url": info.get("thumburl") or info.get("url"), "page": info.get("descriptionurl"),
                    "title": title.removeprefix("File:").rsplit(".", 1)[0],
                    "author": _strip((meta.get("Artist") or {}).get("value", "")) or "Wikimedia Commons",
                    "license": lic, "license_url": (meta.get("LicenseUrl") or {}).get("value", "")})
                if len(found) >= limit:
                    return found
        if found:
            break
    return found
