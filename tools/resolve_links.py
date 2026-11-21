"""Разбор списков ссылок Sketchfab (skfb.ly/...): короткая ссылка -> uid модели -> метаданные из публичного API.

    python tools/resolve_links.py "C:\\Users\\1\\Downloads\\Машины(1).txt" "C:\\Users\\1\\Downloads\\cr.txt" > models_resolved.json

Формат файла: ссылки по одной в строке; после ссылки может стоять название; пустая строка отделяет группы.
"""
import json
import re
import sys
import urllib.request

UA = {"User-Agent": "AutoChoice/1.0 (educational project)"}


def uid_of(url: str) -> str | None:
    """skfb.ly/xxxx редиректит на sketchfab.com/3d-models/<название>-<uid32>."""
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        final = r.geturl()
    m = re.search(r"([a-f0-9]{32})(?:[/?#]|$)", final)
    return m.group(1) if m else None


def meta(uid: str) -> dict:
    req = urllib.request.Request(f"https://api.sketchfab.com/v3/models/{uid}", headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.load(r)
    lic = d.get("license") or {}
    return {"uid": uid, "name": d.get("name"), "author": (d.get("user") or {}).get("displayName") or (d.get("user") or {}).get("username"),
            "license": lic.get("label"), "license_url": lic.get("url"), "downloadable": bool(d.get("isDownloadable")),
            "faces": d.get("faceCount"), "likes": d.get("likeCount"), "url": d.get("viewerUrl") or f"https://sketchfab.com/3d-models/{uid}",
            "age_restricted": bool(d.get("isAgeRestricted"))}


def parse(path: str) -> list[dict]:
    out, group = [], 0
    for line in open(path, encoding="utf-8-sig"):
        line = line.strip()
        if not line:
            group += 1 if out and out[-1].get("group") == group else 0
            continue
        m = re.match(r"(https?://\S+)\s*(.*)$", line)
        if m:
            out.append({"short": m.group(1), "label": m.group(2).strip() or None, "group": group, "file": path})
    return out


if __name__ == "__main__":
    items = [it for p in sys.argv[1:] for it in parse(p)]
    for it in items:
        try:
            it["uid"] = uid_of(it["short"])
            it.update(meta(it["uid"]) if it["uid"] else {})
        except Exception as e:  # битая ссылка не должна ронять весь разбор
            it["error"] = str(e)[:120]
    json.dump(items, sys.stdout, ensure_ascii=False, indent=1)
