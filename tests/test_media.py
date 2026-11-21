"""Выбор 3D-модели Sketchfab и проверка файла: без сети, на заранее заданных данных."""
import json
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

from app.sketchfab import best_model, glb_dims, license_code, license_ok, looks_like_car, names_for, verify_glb  # noqa: E402

UID = lambda n: f"{n:032x}"


def cand(n, name, **kw):
    return {"uid": UID(n), "name": name, "faceCount": 150_000, "likeCount": 10, **kw}


def test_picks_matching_name_and_year():
    pool = [cand(1, "Toyota Camry 2012"), cand(2, "Toyota Camry 2021"), cand(3, "Toyota Corolla 2021")]
    assert best_model(pool, "Toyota Camry", 2021)["uid"] == UID(2)


def test_rejects_parts_and_low_poly_and_wrong_body():
    pool = [cand(1, "Kia Sportage wheels"), cand(2, "Kia Sportage low poly"), cand(3, "Kia Sportage sedan")]
    assert best_model(pool, "Kia Sportage", None, "suv")["uid"] == UID(2)   # low poly лишь штрафуется
    assert best_model([cand(1, "Kia Sportage wheels"), cand(3, "Kia Sportage sedan")], "Kia Sportage", None, "suv") is None


def test_license_rules():
    assert license_ok({"label": "CC Attribution"}, allow_nc=False)
    assert not license_ok({"label": "CC Attribution-NonCommercial"}, allow_nc=False)
    assert license_ok({"label": "CC Attribution-NonCommercial"}, allow_nc=True)
    assert not license_ok({"label": "CC Attribution-NoDerivs"}, allow_nc=True)     # перекрашиваем кузов — нужны изменения
    assert not license_ok({"label": "Standard"}, allow_nc=True)
    assert license_code("https://creativecommons.org/licenses/by/4.0/", "x") == "CC-BY-4.0"
    assert license_code("https://creativecommons.org/publicdomain/zero/1.0/", "x") == "CC0-1.0"


def test_names_for_translit_and_shortening():
    assert names_for({"brand": "УАЗ", "model": "Patriot"}) == ["UAZ Patriot"]
    assert names_for({"brand": "Chery", "model": "Tiggo 7 Pro Max"}) == ["Chery Tiggo 7 Pro Max", "Chery Tiggo 7 Pro"]


def glb(positions, children=None, external=False):
    """Минимальный GLB: один узел-меш с заданным габаритом (min/max позиций)."""
    doc = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
           "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
           "accessors": [{"min": positions[0], "max": positions[1], "count": 8, "componentType": 5126, "type": "VEC3"}],
           "buffers": [{"byteLength": 0, **({"uri": "https://evil.example/x.bin"} if external else {})}]}
    body = json.dumps(doc).encode()
    body += b" " * (-len(body) % 4)
    total = 12 + 8 + len(body)
    return b"glTF" + struct.pack("<II", 2, total) + struct.pack("<I", len(body)) + b"JSON" + body


def test_glb_shape_check():
    assert looks_like_car(glb([[0, 0, 0], [1.8, 1.5, 4.5]]))             # обычная машина
    assert not looks_like_car(glb([[0, 0, 0], [6.8, 2.0, 24.7]]))        # сцена: две машины и дорога
    assert not looks_like_car(glb([[0, 0, 0], [4.0, 1.5, 4.5]]))         # квадратный «постамент»
    assert glb_dims(glb([[0, 0, 0], [1.8, 1.5, 4.5]]))[0] == pytest.approx(4.5)


def test_glb_must_be_self_contained():
    verify_glb(glb([[0, 0, 0], [1, 1, 1]]))
    with pytest.raises(ValueError):
        verify_glb(glb([[0, 0, 0], [1, 1, 1]], external=True))
    with pytest.raises(ValueError):
        verify_glb(b"not a glb at all, definitely")
