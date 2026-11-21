"""Автоматическое разбиение 3D-модели машины на детали (быстро, без рендера и без нейросети).

Вход — GLB любой машины (в т.ч. скачанный с Sketchfab: сотни кусков, названных по материалам, а не по деталям).
Выход — новый GLB, где каждая деталь отдельным узлом (node.name = id детали), и parts.json для просмотрщика
(подписи, сноски, подсветка): id, label_ru, bbox, anchor — в системе «нос −Z, верх +Y, водитель слева (−X)».

Алгоритм (numpy, десятки миллисекунд на 100 тыс. полигонов):
  1. разбор GLB, мировые координаты вершин, материалы -> роли (стекло, резина, свет, салон, хром, краска);
  2. куски делятся на связные «острова» (шины, окна и фары, склеенные в одном меше, станут отдельными);
  3. колёса: куски-диски с горизонтальной осью, четыре штуки в виде прямоугольника -> задают поперечную и продольную оси,
     колёсную базу и колею (реальные размеры модели — для физики теста на лося);
  4. где перед: голосование по профилю высоты (капот ниже багажника), положению макушки крыши и названиям ламп;
  5. поворот в канонический вид (корневой узел с матрицей) и классификация кусков по положению и форме;
  6. запись GLB без копирования вершин: новые индексы в тот же буфер.
Если колёса найти не удалось, возвращается ошибка: лучше не размечать, чем разметить неверно.
"""
import json
import re
import struct
import time

import numpy as np

PARTS_RU = {
    "body_paint": ("Кузов", "exterior"), "hood": ("Капот", "exterior"), "trunk_lid": ("Крышка багажника", "exterior"),
    "roof": ("Крыша", "exterior"), "front_bumper": ("Передний бампер", "exterior"), "rear_bumper": ("Задний бампер", "exterior"),
    "grille": ("Решётка радиатора", "exterior"), "windshield": ("Лобовое стекло", "exterior"), "rear_window": ("Заднее стекло", "exterior"),
    "side_glass_left": ("Боковые стёкла слева", "exterior"), "side_glass_right": ("Боковые стёкла справа", "exterior"),
    "headlight_left": ("Левая передняя фара", "exterior"), "headlight_right": ("Правая передняя фара", "exterior"),
    "taillight_left": ("Левый задний фонарь", "exterior"), "taillight_right": ("Правый задний фонарь", "exterior"),
    "mirror_left": ("Левое зеркало", "exterior"), "mirror_right": ("Правое зеркало", "exterior"),
    "door_front_left": ("Передняя левая дверь", "exterior"), "door_front_right": ("Передняя правая дверь", "exterior"),
    "door_rear_left": ("Задняя левая дверь", "exterior"), "door_rear_right": ("Задняя правая дверь", "exterior"),
    "fender_front_left": ("Переднее левое крыло", "exterior"), "fender_front_right": ("Переднее правое крыло", "exterior"),
    "wheel_front_left": ("Переднее левое колесо", "exterior"), "wheel_front_right": ("Переднее правое колесо", "exterior"),
    "wheel_rear_left": ("Заднее левое колесо", "exterior"), "wheel_rear_right": ("Заднее правое колесо", "exterior"),
    "seat_driver": ("Сиденье водителя", "interior"), "seat_passenger": ("Сиденье пассажира", "interior"),
    "seat_rear": ("Задний диван", "interior"), "steering_wheel": ("Руль", "interior"), "dashboard": ("Передняя панель", "interior"),
    "fender_rear_left": ("Заднее левое крыло", "exterior"), "fender_rear_right": ("Заднее правое крыло", "exterior"),
    "sill_left": ("Левый порог", "exterior"), "sill_right": ("Правый порог", "exterior"),
    "pillar_a_left": ("Левая стойка A", "exterior"), "pillar_a_right": ("Правая стойка A", "exterior"),
    "pillar_c_left": ("Левая стойка C", "exterior"), "pillar_c_right": ("Правая стойка C", "exterior"),
    "window_front_left": ("Стекло передней левой двери", "exterior"), "window_front_right": ("Стекло передней правой двери", "exterior"),
    "window_rear_left": ("Стекло задней левой двери", "exterior"), "window_rear_right": ("Стекло задней правой двери", "exterior"),
    "handle_front_left": ("Ручка передней левой двери", "exterior"), "handle_front_right": ("Ручка передней правой двери", "exterior"),
    "handle_rear_left": ("Ручка задней левой двери", "exterior"), "handle_rear_right": ("Ручка задней правой двери", "exterior"),
    "plate_front": ("Передний номер", "exterior"), "plate_rear": ("Задний номер", "exterior"),
    "badge_front": ("Эмблема спереди", "exterior"), "badge_rear": ("Эмблема сзади", "exterior"),
    "wipers": ("Дворники", "exterior"), "exhaust": ("Выхлопная труба", "exterior"),
    "fog_light_left": ("Левая противотуманная фара", "exterior"), "fog_light_right": ("Правая противотуманная фара", "exterior"),
    **{f"{k}_{e}_{sd}": (f"{ru} ({'переднее' if e == 'front' else 'заднее'} {'левое' if sd == 'left' else 'правое'} колесо)", "exterior")
       for k, ru in (("tire", "Шина"), ("rim", "Диск"), ("brake", "Тормоз")) for e in ("front", "rear") for sd in ("left", "right")},
}
PARTS_RU.update({
    "instrument_cluster": ("Приборная панель", "interior"), "multimedia_screen": ("Экран мультимедиа", "interior"),
    "center_console": ("Центральная консоль", "interior"), "gear_shifter": ("Рычаг КПП", "interior"),
    "handbrake": ("Стояночный тормоз", "interior"), "pedals": ("Педали", "interior"), "door_panels": ("Обивка дверей", "interior"),
    "floor_carpet": ("Пол и ковры", "interior"), "headliner": ("Потолок салона", "interior"),
})
# стёкла остаются в модели, но деталью не считаются: на них не наводятся и не подписывают
NOT_PICKABLE = re.compile(r"^(windshield|rear_window|window_|side_glass_)")
# мелкая деталь -> узел-группа (колесо целиком нужно тесту на лося и туру камеры)
PARENT = {f"{k}_{e}_{sd}": f"wheel_{e}_{sd}" for k in ("tire", "rim", "brake") for e in ("front", "rear") for sd in ("left", "right")}
ROLES = [
    # названия материалов бывают русскими, английскими и русскими латиницей (steklo, Zerkalo, Fari, hrom, kuzov)
    ("glass", r"glass|glas\b|window|windshield|windscreen|stekl|стекл"), ("rubber", r"gum|tire|tyre|rubber|rezin|shin\b|шин|резин"),
    ("light", r"light|lamp|lens|\bled\b|\bfar|fari|fonar|stopar|svet|фар|фонар|stop|signal|blinker|indicator"),
    ("mirror", r"mirror|zerkal|зеркал"), ("plate", r"plate|number|nomer|номер"),
    ("interior", r"leather|interior|seat|dash|salon|kover|carpet|cabin|cloth|fabric|alcantara|steer|салон|кож"),
    ("chrome", r"chrome|hrom|khrom|metal|silver|alu|steel|rim|disk|disc|хром|диск"),
    ("paint", r"body|paint|lacquer|kuzov|кузов|exterior|colou?r"),
]
HEAD, TAIL = re.compile(r"head|front|фар"), re.compile(r"tail|rear|stop|brake|фонар|back")


# Деталь подписывается, только если мы в ней уверены (и для неё есть описание). Остальное остаётся частью модели без подписи.
MIN_CONF = 0.55
# имя узла/меша/материала -> деталь (сторона и перед/зад — по положению). Самый надёжный признак: так деталь назвал автор модели.
NAME_HINTS = [
    ("steering_wheel", r"steer|руль|\brul\b|s_?wheel"),
    ("pedals", r"pedal|педал"),
    ("handbrake", r"hand_?brake|parking_?brake|ручник"),
    ("gear_shifter", r"gear_?(?:lever|stick|shift|knob)|shifter|shift_?knob|\bkpp\b|кпп|selector"),
    ("multimedia_screen", r"screen|display|monitor|\bnavi|multimedia|infotain|экран"),
    ("instrument_cluster", r"gauge|cluster|speedo|tacho|instrument|приборн"),
    ("dashboard", r"\bdash|torpedo|торпед"),
    ("seat", r"\bseat|сиден|кресл"),
    ("tire", r"\btire|\btyre|покрыш|\bшин"),
    ("brake", r"brake|caliper|суппорт|тормоз"),
    ("rim", r"\brims?\b|wheel_?rim|\bдиск"),
    ("hood", r"\bhood|bonnet|капот|kapot"),
    ("trunk_lid", r"\btrunk|boot_?lid|tailgate|багажн|bagazh"),
    ("bumper", r"bumper|бампер|bamper"),
    ("grille", r"grill|решет"),
    ("handle", r"door_?handle|handle|ручк"),
    ("mirror", r"mirror|зеркал|zerkal"),
    ("door", r"\bdoors?\b|door_|двер|dver"),
    ("fender", r"fender|\bwing\b|крыл"),
    ("roof", r"\broof\b|крыш"),
    ("wipers", r"wiper|дворник|dvornik"),
    ("exhaust", r"exhaust|muffler|tail_?pipe|глушит|выхлоп"),
    ("plate", r"licen[cs]e|number_?plate|\bplate\b|номер"),
    ("badge", r"\blogo|badge|emblem|эмблем|значок"),
    ("fog_light", r"\bfog|противотум"),
    ("headlight", r"head_?l(?:ight|amp)|фара\b|\bfari\b"),
    ("taillight", r"tail_?l(?:ight|amp)|rear_?l(?:ight|amp)|\bstop_?l|фонар|stopar"),
]
NAME_HINTS = [(k, re.compile(rx)) for k, rx in NAME_HINTS]


def _stamp(pieces, conf_of):
    """Уверенность для деталей, назначенных последним проходом."""
    for p in pieces:
        if p["part"] and p.get("conf") is None:
            p["conf"] = conf_of(p)


def classify_by_names(pieces, lo_c, hi_c, wheel_info, D):
    """Проход 0: детали, которые автор модели назвал сам («SteeringWheel», «Seat_FL», «Door_RR», «Bonnet»)."""
    W, H, L = hi_c - lo_c
    x0 = (lo_c[0] + hi_c[0]) / 2
    fw = min(w["c"][2] for w in wheel_info)
    rw = max(w["c"][2] for w in wheel_info)
    wb = rw - fw
    for p in pieces:
        name = re.sub(r"[_.\-]+", "_", p["name"])
        if re.fullmatch(r"[\s_]*(object|mesh|material|mat|default|node|group|polysurface|cube|plane|cylinder)?[_\s\d]*", name):
            continue
        hit = next((k for k, rx in NAME_HINTS if rx.search(name)), None)
        if not hit:
            continue
        c = (p["lo_c"] + p["hi_c"]) / 2
        side = "left" if c[0] < x0 else "right"
        front = c[2] < (lo_c[2] + hi_c[2]) / 2
        u = (c[2] - fw) / wb if wb > 0 else 0.5
        part = None
        if hit in ("tire", "rim", "brake"):
            w = min(wheel_info, key=lambda w: np.linalg.norm(c - w["c"]))
            if np.linalg.norm(c - w["c"]) < 0.9 * D:
                part = f"{hit}_{w['part'][len('wheel_'):]}"
        elif hit == "seat":
            part = "seat_rear" if u > 0.67 else f"seat_{'driver' if side == 'left' else 'passenger'}"
        elif hit == "bumper":
            part = "front_bumper" if front else "rear_bumper"
        elif hit in ("mirror",):
            part = f"mirror_{side}" if abs(c[0] - x0) > 0.3 * W / 2 else None      # салонное зеркало — не наружное
        elif hit == "door":
            if p["role"] == "interior" or re.search(r"trim|card|panel|inner|обивк", name):
                part = "door_panels"
            elif abs(c[0] - x0) > 0.5 * W / 2:
                part = f"door_{'front' if u < 0.5 else 'rear'}_{side}"
        elif hit == "handle":
            part = f"handle_{'front' if u < 0.5 else 'rear'}_{side}" if abs(c[0] - x0) > 0.7 * W / 2 else None
        elif hit == "fender":
            part = f"fender_{'front' if front else 'rear'}_{side}"
        elif hit in ("plate", "badge"):
            part = f"{hit}_{'front' if front else 'rear'}"
        elif hit in ("fog_light", "headlight", "taillight"):
            part = f"{hit}_{side}"
        else:
            part = hit
        if part:
            p["part"], p["conf"] = part, 0.95


class SplitError(Exception):
    pass


# ───────────── GLB ─────────────
def read_glb(b: bytes):
    if len(b) < 20 or b[:4] != b"glTF":
        raise SplitError("не GLB")
    n = struct.unpack_from("<I", b, 12)[0]
    j = json.loads(b[20:20 + n])
    off = 20 + n
    blen, typ = struct.unpack_from("<I4s", b, off)
    if typ != b"BIN\0":
        raise SplitError("нет бинарного блока")
    return j, b[off + 8:off + 8 + blen]


_DT = {5120: "i1", 5121: "u1", 5122: "<i2", 5123: "<u2", 5125: "<u4", 5126: "<f4"}
_NC = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}


def accessor(j, binary, i):
    a = j["accessors"][i]
    if a.get("sparse"):
        raise SplitError("sparse-акцессоры не поддерживаются")
    v = j["bufferViews"][a["bufferView"]]
    nc, dt = _NC[a["type"]], np.dtype(_DT[a["componentType"]])
    stride = v.get("byteStride") or nc * dt.itemsize
    off = v.get("byteOffset", 0) + a.get("byteOffset", 0)
    arr = np.ndarray((a["count"], nc), dt, binary, off, (stride, dt.itemsize))
    return arr.astype(np.float32) if a["componentType"] == 5126 else arr.astype(np.int64)


def node_matrix(n):
    if "matrix" in n:
        return np.array(n["matrix"], np.float64).reshape(4, 4).T
    x, y, z, w = n.get("rotation", [0, 0, 0, 1])
    m = np.eye(4)
    m[:3, :3] = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                          [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                          [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]) @ np.diag(n.get("scale", [1, 1, 1]))
    m[:3, 3] = n.get("translation", [0, 0, 0])
    return m


def material_roles(j):
    roles = []
    for m in j.get("materials", []):
        name = (m.get("name") or "").lower()
        role = next((r for r, rx in ROLES if re.search(rx, name)), None)
        see_through = m.get("alphaMode") == "BLEND" or m.get("extensions", {}).get("KHR_materials_transmission", {}).get("transmissionFactor", 0) > 0.3
        if see_through and role is None and not re.search(r"seat|icon|display|sw_|st_|inner|logo|text|decal|screen|dash|gauge|needle|shadow", name):
            role = "glass"                       # прозрачный материал без понятного имени — вероятно стекло
        roles.append(role)
    return roles


# ───────────── куски: примитивы -> связные острова ─────────────
def components(n, e):
    """Связные компоненты графа (вектор рёбер e: (m,2)) без scipy: распространение минимальной метки с прыжками по указателям."""
    lab = np.arange(n)
    while True:
        a, b = lab[e[:, 0]], lab[e[:, 1]]
        new = lab.copy()
        mn = np.minimum(a, b)
        np.minimum.at(new, a, mn)
        np.minimum.at(new, b, mn)
        while True:
            nn = new[new]
            if (nn == new).all():
                break
            new = nn
        if (new == lab).all():
            return lab
        lab = new


def islands(pos, tri, span):
    """Метка острова для каждого треугольника; совпадающие вершины (швы UV/нормалей) склеиваются по координате."""
    q = np.round((pos - pos.min(0)) / (span * 2e-5)).astype(np.int64)
    key = (q[:, 0] << 42) | (q[:, 1] << 21) | q[:, 2]
    _, inv = np.unique(key, return_inverse=True)
    t = inv[tri]
    edges = np.concatenate([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]])
    return components(int(inv.max()) + 1, edges)[t[:, 0]]


def collect(j, binary, roles):
    """Список кусков: dict(node, mesh, prim, mat, tri=(n,3) индексы в примитиве, lo, hi, area, role, name)."""
    pieces, prims = [], []
    stack = [(i, np.eye(4)) for i in j["scenes"][j.get("scene", 0)]["nodes"]]
    seen = set()
    while stack:
        i, parent = stack.pop()
        if i in seen:
            raise SplitError("узлы образуют не дерево")
        seen.add(i)
        n = j["nodes"][i]
        m = parent @ node_matrix(n)
        stack.extend((c, m) for c in n.get("children", []))
        if "mesh" not in n:
            continue
        for pi, p in enumerate(j["meshes"][n["mesh"]]["primitives"]):
            if p.get("mode", 4) != 4 or "POSITION" not in p["attributes"]:
                continue
            v = accessor(j, binary, p["attributes"]["POSITION"])
            w = (v @ m[:3, :3].T.astype(np.float32)) + m[:3, 3].astype(np.float32)
            idx = accessor(j, binary, p["indices"]).ravel() if "indices" in p else np.arange(len(v))
            ours = "part_id" in (n.get("extras") or {})        # имя записал сам разбивщик — подсказкой не считаем
            prims.append(dict(node=i, mesh=n["mesh"], prim=pi, mat=p.get("material"), pos=w, tri=idx[: len(idx) // 3 * 3].reshape(-1, 3),
                              name="" if ours else f"{n.get('name', '')} {j['meshes'][n['mesh']].get('name', '')}".lower()))
    if not prims:
        raise SplitError("в модели нет треугольников")
    allv = np.concatenate([p["pos"] for p in prims])
    lo, hi = allv.min(0), allv.max(0)
    span = float((hi - lo).max())
    for k, p in enumerate(prims):
        lab = islands(p["pos"], p["tri"], span) if len(p["tri"]) > 300 else np.zeros(len(p["tri"]), np.int64)
        role = roles[p["mat"]] if p["mat"] is not None and p["mat"] < len(roles) else None
        mats = j.get("materials") or []
        mat_name = (mats[p["mat"]].get("name") or "").lower() if p["mat"] is not None and p["mat"] < len(mats) else ""
        a, b, c = (p["pos"][p["tri"][:, i]] for i in range(3))
        area = np.linalg.norm(np.cross(b - a, c - a), axis=1) * 0.5
        for lbl in np.unique(lab):
            sel = np.nonzero(lab == lbl)[0]
            tri = p["tri"][sel]
            pts = p["pos"][tri.ravel()]
            pieces.append(dict(prim=k, node=p["node"], mesh=p["mesh"], pi=p["prim"], tri_ids=sel, lo=pts.min(0), hi=pts.max(0),
                               area=float(area[sel].sum()), role=role, name=f'{p["name"]} {mat_name}', n=len(sel), part=None))
    return prims, pieces, lo, hi


# ───────────── колёса и ориентация ─────────────
def find_wheels(pieces, lo, hi, up):
    """Четыре колеса прямоугольником: поперечная ось (тонкая ось диска), продольная ось, индексы кусков."""
    hor = [a for a in range(3) if a != up]
    L = max(hi[a] - lo[a] for a in hor)
    cands = []
    for k, p in enumerate(pieces):
        d = p["hi"] - p["lo"]
        thin = min(hor, key=lambda a: d[a])
        other = [a for a in range(3) if a != thin]
        D = max(d[other])
        if (0.085 * L < D < 0.24 * L and 0.14 < d[thin] / D < 0.62 and d[other].min() / D > 0.78 and p["n"] > 40
                and (p["lo"][up] - lo[up]) < 0.2 * D):
            cands.append((k, thin, D, (p["lo"] + p["hi"]) / 2))
    if len(cands) < 4:
        return None
    for lat in {c[1] for c in cands}:
        lng = next(a for a in hor if a != lat)
        group = [c for c in cands if c[1] == lat]
        if len(group) < 4:
            continue
        med = float(np.median([c[2] for c in group]))
        group = [c for c in group if 0.8 * med < c[2] < 1.25 * med]
        mid_lat = np.median([c[3][lat] for c in group])
        side = {s: [c for c in group if (c[3][lat] < mid_lat) == s] for s in (True, False)}
        if min(len(side[True]), len(side[False])) < 2:
            continue
        best = None
        for l in side[True]:
            for r in side[False]:
                if abs(l[3][lng] - r[3][lng]) > 0.04 * L:
                    continue
                for l2 in side[True]:
                    for r2 in side[False]:
                        if abs(l2[3][lng] - r2[3][lng]) > 0.04 * L or abs(l2[3][lng] - l[3][lng]) < 0.4 * L or abs(l[2] - l2[2]) > 0.2 * med:
                            continue
                        wb, track = abs(l2[3][lng] - l[3][lng]), abs(r[3][lat] - l[3][lat])
                        if track < 0.25 * L or not 0.5 * L < wb < 0.8 * L:
                            continue
                        score = l[2] + r[2] + l2[2] + r2[2] - 10 * (abs(l[3][up] - r[3][up]) + abs(l2[3][up] - r2[3][up]))
                        if best is None or score > best[0]:
                            best = (score, [l, r, l2, r2], lat, lng)
        if best:
            return best[2], best[3], [c[0] for c in best[1]], [c[3] for c in best[1]], float(np.median([c[2] for c in best[1]]))
    return None


def vote_front(pieces, lo, hi, up, lng, centers):
    """+1 — нос в сторону +lng, −1 — в сторону −lng. Голосование: профиль высоты, макушка крыши, названия фар."""
    mid = (lo[lng] + hi[lng]) / 2
    L = hi[lng] - lo[lng]
    votes = 0.0
    outer = [p for p in pieces if p["role"] not in ("interior",) and p["n"] > 20]
    def top(sign):
        pts = [p["hi"][up] for p in outer if sign * ((p["lo"][lng] + p["hi"][lng]) / 2 - mid) > 0.28 * L]
        return float(np.percentile(pts, 90)) if pts else 0.0
    h = hi[up] - lo[up]
    votes += np.sign(top(-1) - top(+1)) * (1.0 if abs(top(-1) - top(+1)) > 0.03 * h else 0.4)   # нос ниже кормы
    roof = max(outer, key=lambda p: p["hi"][up])
    votes += -1.0 * np.sign(((roof["lo"][lng] + roof["hi"][lng]) / 2) - mid) if abs(((roof["lo"][lng] + roof["hi"][lng]) / 2) - mid) > 0.04 * L else 0
    for p in pieces:
        c = (p["lo"][lng] + p["hi"][lng]) / 2 - mid
        if HEAD.search(p["name"]) and p["role"] == "light":
            votes += 1.5 * np.sign(c)
        if TAIL.search(p["name"]) and p["role"] == "light":
            votes -= 1.5 * np.sign(c)
    return 1 if votes >= 0 else -1


def orientation(up, lat, lng, front_sign):
    """Матрица M (3x3): канонические координаты = M @ p; нос −Z, верх +Y, вправо +X."""
    f = np.zeros(3); f[lng] = front_sign
    u = np.zeros(3); u[up] = 1
    r = np.cross(f, u)
    return np.stack([r, u, -f])


# ───────────── классификация (два прохода: стёкла и свет -> опорные размеры -> панели кузова) ─────────────
def features(p, lo_c, hi_c):
    W, H, L = hi_c - lo_c
    c = (p["lo_c"] + p["hi_c"]) / 2
    d = p["hi_c"] - p["lo_c"]
    return ((c[0] - (lo_c[0] + hi_c[0]) / 2) / (W / 2), (c[1] - lo_c[1]) / H, (c[2] - lo_c[2]) / L, d[0] / W, d[1] / H, d[2] / L)


def classify_early(pieces, lo_c, hi_c):
    """Проход 1: стёкла, фары и фонари, салон — то, что опознаётся по материалу и грубому положению."""
    for p in pieces:
        if p["part"]:
            continue
        x, y, t, dx, dy, dz = features(p, lo_c, hi_c)
        side, role = ("left" if x < 0 else "right"), p["role"]
        if role == "glass":
            if abs(x) < 0.55 and dx > 0.4 and 0.12 < t < 0.5:
                p["part"] = "windshield"
            elif abs(x) < 0.55 and dx > 0.3 and t > 0.6:
                p["part"] = "rear_window"
            elif abs(x) > 0.5 and 0.15 < t < 0.9:
                p["part"] = f"side_glass_{side}"
            elif t < 0.15 and y < 0.75:
                p["part"] = f"headlight_{side}"
            elif t > 0.88:
                p["part"] = f"taillight_{side}"
        elif role == "light" or (t < 0.14 and y < 0.62 and abs(x) > 0.3 and dx < 0.3 and dy < 0.22 and role in ("chrome", None)):
            if t < 0.2 and abs(x) > 0.2:
                p["part"] = f"headlight_{side}"
            elif t > 0.8 and abs(x) > 0.2:
                p["part"] = f"taillight_{side}"


def body_context(pieces, lo_c, hi_c, wheel_info, D, doors=4):
    """Опорные размеры из уже найденных деталей этой модели: лобовое и заднее стекло, окна, колёса."""
    def box(part):          # самое большое стекло этого типа: люк в крыше не должен сдвигать границы лобового и заднего
        ps = [p for p in pieces if p["part"] == part and p["n"] > 30]
        big = max(ps, key=lambda p: p["area"]) if ps else None
        return (big["lo_c"], big["hi_c"]) if big else None
    ws, rg = box("windshield"), box("rear_window")
    W, H, L = hi_c - lo_c
    sides = [p for p in pieces if p["part"] and p["part"].startswith("side_glass") and p["n"] > 30]
    ctx = dict(
        ws_base=ws[0][2] if ws else lo_c[2] + 0.34 * L, ws_top=ws[1][2] if ws else lo_c[2] + 0.47 * L,
        ws_y=ws[0][1] if ws else lo_c[1] + 0.62 * H, rg_top=rg[0][2] if rg else hi_c[2] - 0.3 * L,
        rg_base=rg[1][2] if rg else hi_c[2] - 0.12 * L, rg_y=rg[0][1] if rg else lo_c[1] + 0.6 * H,
        belt=min([p["lo_c"][1] for p in sides] or [lo_c[1] + 0.58 * H]),
        fw=[w["c"][2] for w in wheel_info if "front" in w["part"]][0], rw=[w["c"][2] for w in wheel_info if "rear" in w["part"]][0],
        R=D / 2, x0=(lo_c[0] + hi_c[0]) / 2, have_ws=bool(ws), have_rg=bool(rg))
    gaps = []                                    # B-стойка: самый широкий зазор между боковыми окнами; одно окно — две двери
    for side in ("left", "right"):
        zs = sorted((p["lo_c"][2], p["hi_c"][2]) for p in sides if p["part"] == f"side_glass_{side}")
        gaps += [(b[0] - a[1], (a[1] + b[0]) / 2) for a, b in zip(zs, zs[1:])]
    ctx["pillar_b"] = max(gaps)[1] if gaps and max(gaps)[0] > 0.01 * L else None
    if ctx["pillar_b"] is None and doors >= 4:   # окна склеены в одно: стойку ставим по середине между колёсными арками
        ctx["pillar_b"] = (ctx["fw"] + ctx["rw"]) / 2 + 0.02 * L
    return ctx


def classify_body(pieces, lo_c, hi_c, ctx):
    """Проход 2: панели кузова по опорам: верх капота = низ лобового, багажник привязан к заднему стеклу, зеркало — у основания лобового."""
    W, H, L = hi_c - lo_c
    for p in pieces:
        if p["part"] or p["role"] in ("rubber", "interior", "glass"):
            continue
        x, y, t, dx, dy, dz = features(p, lo_c, hi_c)
        side = "left" if x < 0 else "right"
        z = (p["lo_c"][2] + p["hi_c"][2]) / 2
        top = p["hi_c"][1]
        small = dx < 0.12 and dy < 0.1 and dz < 0.1
        if p["role"] == "plate" or (0.15 < dx < 0.36 and dy < 0.12 and dz < 0.05 and abs(x) < 0.15 and (t < 0.04 or t > 0.96) and y < 0.55):
            p["part"] = "plate_front" if t < 0.5 else "plate_rear"
        elif small and abs(x) < 0.12 and (t < 0.06 or t > 0.94) and 0.3 < y < 0.75 and p["role"] in ("chrome", None):
            p["part"] = "badge_front" if t < 0.5 else "badge_rear"
        elif small and abs(x) > 0.8 and dx < 0.06 and dy < 0.05 and ctx["fw"] + ctx["R"] < z < ctx["rw"] - ctx["R"] \
                and abs(p["hi_c"][1] - ctx["belt"]) < 0.16 * H:
            p["part"] = f"handle_{'front' if (ctx['pillar_b'] is None or z < ctx['pillar_b']) else 'rear'}_{side}"
        elif dy < 0.06 and dz < 0.12 and 0.15 < dx < 0.6 and abs(z - ctx["ws_base"]) < 0.05 * L and abs(p["lo_c"][1] - ctx["ws_y"]) < 0.08 * H:
            p["part"] = "wipers"
        elif small and t > 0.85 and y < 0.3 and 0.2 < abs(x) < 0.9 and p["role"] in ("chrome", None):
            p["part"] = "exhaust"
        elif small and t < 0.1 and y < 0.4 and 0.45 < abs(x) < 0.95 and p["role"] in ("light", "chrome", None) and p["lo_c"][1] < lo_c[1] + 0.35 * H:
            p["part"] = f"fog_light_{side}"
        elif (p["role"] == "mirror" or p["role"] is None or p["role"] in ("paint", "chrome")) and abs(x) > 0.8 and dx < 0.14 and dz < 0.1 and dy < 0.12 \
                and abs(z - ctx["ws_base"]) < 0.14 * L and abs((p["lo_c"][1] + p["hi_c"][1]) / 2 - ctx["belt"]) < 0.14 * H:
            p["part"] = f"mirror_{side}"
        elif p["role"] == "mirror":
            continue
        elif abs(x) < 0.45 and dx > 0.4 and p["lo_c"][1] > ctx["belt"] - 0.03 * H and top > hi_c[1] - 0.12 * H and ctx["ws_top"] - 0.03 * L < z < ctx["rg_top"] + 0.03 * L:
            p["part"] = "roof"
        elif abs(x) < 0.5 and dx > 0.4 and dz > 0.05 and z < ctx["ws_base"] + 0.02 * L and z > lo_c[2] + 0.06 * L \
                and top <= ctx["ws_y"] + 0.07 * H and p["lo_c"][1] > lo_c[1] + 0.3 * H:
            p["part"] = "hood"
        elif abs(x) < 0.5 and dx > 0.35 and dz > 0.04 and z > ctx["rg_base"] - 0.04 * L and z < hi_c[2] - 0.03 * L \
                and top <= ctx["rg_y"] + 0.1 * H and p["lo_c"][1] > lo_c[1] + 0.3 * H:
            p["part"] = "trunk_lid"
        elif t < 0.13 and abs(x) < 0.4 and dx > 0.5 and y < 0.55 and dz < 0.2:
            p["part"] = "front_bumper"
        elif t > 0.87 and abs(x) < 0.4 and dx > 0.5 and y < 0.6 and dz < 0.2:
            p["part"] = "rear_bumper"
        elif t < 0.1 and abs(x) < 0.3 and 0.25 < y < 0.6 and 0.2 < dx < 0.7 and p["role"] in ("chrome", None, "black"):
            p["part"] = "grille"
        elif abs(x) > 0.6 and dx < 0.25 and 0.08 < dz < 0.34 and dy > 0.28 and p["lo_c"][1] > lo_c[1] + 0.12 * H \
                and ctx["fw"] + ctx["R"] < z < ctx["rw"] - ctx["R"]:
            pillar = ctx["pillar_b"]
            p["part"] = f"door_{'front' if (pillar is None or z < pillar) else 'rear'}_{side}"
        elif abs(x) > 0.6 and dx < 0.3 and z < ctx["fw"] + ctx["R"] and z > lo_c[2] + 0.08 * L and y > 0.3 and p["role"] in ("paint", None):
            p["part"] = f"fender_front_{side}"
        elif abs(x) > 0.6 and dx < 0.3 and z > ctx["rw"] - ctx["R"] and z < hi_c[2] - 0.08 * L and y > 0.3 and p["role"] in ("paint", None):
            p["part"] = f"fender_rear_{side}"


def refine_shell(pieces, prims, R, lo_c, hi_c, ctx):
    """Склеенный кузов (одним куском) делим по треугольникам теми же опорными размерами."""
    W, H, L = hi_c - lo_c
    x0 = ctx["x0"]
    out = []
    for p in pieces:
        shell = p["part"] is None and p["role"] in ("paint", None, "chrome") and p["n"] > 1500 and ((p["hi_c"] - p["lo_c"]) / np.array([W, H, L])).max() > 0.4
        if not shell:
            out.append(p)
            continue
        pr = prims[p["prim"]]
        tri = pr["tri"][p["tri_ids"]]
        v = pr["pos"][tri] @ R.T.astype(np.float32)               # (n,3,3) в канонических координатах
        c = v.mean(1)
        nrm = np.cross(v[:, 1] - v[:, 0], v[:, 2] - v[:, 0])
        nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
        ax = np.abs(c[:, 0] - x0) / (W / 2)
        y, z = c[:, 1], c[:, 2]
        up, sidef = nrm[:, 1] > 0.35, np.abs(nrm[:, 0]) > 0.5
        lab = np.full(len(tri), "body_paint", dtype=object)
        lab[(y > hi_c[1] - 0.14 * H) & up & (z > ctx["ws_top"] - 0.02 * L) & (z < ctx["rg_top"] + 0.02 * L) & (ax < 0.8)] = "roof"
        lab[up & (z < ctx["ws_base"]) & (y > lo_c[1] + 0.42 * H) & (y < ctx["ws_y"] + 0.07 * H) & (ax < 0.68)] = "hood"
        lab[up & (z > ctx["rg_base"] - 0.04 * L) & (y > lo_c[1] + 0.42 * H) & (y < ctx["rg_y"] + 0.1 * H) & (ax < 0.72)] = "trunk_lid"
        lab[(z < lo_c[2] + 0.1 * L) & (y < lo_c[1] + 0.52 * H)] = "front_bumper"
        lab[(z > hi_c[2] - 0.1 * L) & (y < lo_c[1] + 0.6 * H)] = "rear_bumper"
        door_lo, door_hi = ctx["fw"] + 1.05 * ctx["R"], ctx["rw"] - 1.05 * ctx["R"]
        in_door = sidef & (ax > 0.6) & (y > lo_c[1] + 0.28 * H) & (y < ctx["belt"] + 0.03 * H) & (z > door_lo) & (z < door_hi)
        left = c[:, 0] < x0
        for sd, mask in (("left", left), ("right", ~left)):
            if ctx["pillar_b"] is None:
                lab[in_door & mask] = f"door_front_{sd}"
            else:
                lab[in_door & mask & (z < ctx["pillar_b"])] = f"door_front_{sd}"
                lab[in_door & mask & (z >= ctx["pillar_b"])] = f"door_rear_{sd}"
            fender = sidef & (ax > 0.6) & (y > lo_c[1] + 0.3 * H) & (z < door_lo) & (z > lo_c[2] + 0.1 * L) & mask
            lab[fender & (lab == "body_paint")] = f"fender_front_{sd}"
            rear_f = sidef & (ax > 0.6) & (y > lo_c[1] + 0.3 * H) & (y < ctx["belt"] + 0.03 * H) & (z > door_hi) & (z < hi_c[2] - 0.1 * L) & mask
            lab[rear_f & (lab == "body_paint")] = f"fender_rear_{sd}"
            sill = (ax > 0.7) & (y > lo_c[1] + 0.08 * H) & (y < lo_c[1] + 0.3 * H) & (z > door_lo) & (z < door_hi) & mask
            lab[sill & (lab == "body_paint")] = f"sill_{sd}"
            above = (ax > 0.45) & (y > ctx["belt"] + 0.02 * H) & mask & (lab == "body_paint")
            lab[above & (z > ctx["ws_base"] - 0.02 * L) & (z < ctx["ws_top"] + 0.02 * L)] = f"pillar_a_{sd}"
            lab[above & (z > ctx["rg_top"] - 0.02 * L) & (z < ctx["rg_base"] + 0.02 * L)] = f"pillar_c_{sd}"
        front_face = nrm[:, 2] < -0.5
        lab[front_face & (z < lo_c[2] + 0.07 * L) & (y > lo_c[1] + 0.3 * H) & (y < lo_c[1] + 0.56 * H) & (ax < 0.38)] = "grille"
        for name in np.unique(lab):
            sel = np.nonzero(lab == name)[0]
            pts = v[sel].reshape(-1, 3)
            out.append(dict(p, tri_ids=p["tri_ids"][sel], lo_c=pts.min(0), hi_c=pts.max(0), n=len(sel), part=str(name),
                            area=p["area"] * len(sel) / p["n"]))
    return out


# зоны салона в долях: u — от оси передних колёс до задней (колёсная база), ax — от середины к борту (−1 — сторона водителя),
# h — доля высоты машины. Подобраны по эталонной разметке и годятся для любой модели, т.к. всё в относительных единицах.
CABIN_RULES = [
    ("pedals", (0.06, 0.22), (-0.62, -0.12), (0.18, 0.42)),
    ("gear_shifter", (0.31, 0.43), (-0.09, 0.09), (0.28, 0.47)),
    ("handbrake", (0.42, 0.56), (-0.09, 0.09), (0.26, 0.42)),
    ("instrument_cluster", (0.21, 0.285), (-0.54, -0.16), (0.55, 0.69)),
    ("multimedia_screen", (0.23, 0.31), (-0.13, 0.13), (0.5, 0.67)),
    ("steering_wheel", (0.27, 0.41), (-0.6, -0.14), (0.44, 0.73)),
    ("headliner", (0.1, 1.2), (-0.8, 0.8), (0.86, 1.0)),
    ("door_panels", (0.18, 0.96), None, (0.24, 0.8)),            # None: |ax| > 0.66
    ("seat_driver", (0.36, 0.67), (-0.67, -0.1), (0.18, 0.86)),
    ("seat_passenger", (0.36, 0.67), (0.1, 0.67), (0.18, 0.86)),
    ("seat_rear", (0.67, 1.06), (-0.7, 0.7), (0.18, 0.83)),
    ("center_console", (0.08, 0.63), (-0.2, 0.2), (0.18, 0.53)),
    ("dashboard", (-0.05, 0.34), (-0.8, 0.8), (0.28, 0.86)),
    ("floor_carpet", (0.0, 1.3), (-0.75, 0.75), (0.12, 0.3)),
]


def _cabin_label(u, ax, h):
    lab = np.full(len(u), None, dtype=object)
    for name, (u0, u1), axr, (h0, h1) in reversed(CABIN_RULES):      # первые правила важнее: пишем их последними
        m = (u > u0) & (u < u1) & (h > h0) & (h < h1)
        m &= (np.abs(ax) > 0.66) & (np.abs(ax) < 0.9) if axr is None else (ax > axr[0]) & (ax < axr[1])
        lab[m] = name
    return lab


def refine_interior(pieces, prims, R, lo_c, hi_c, ctx):
    """Салон любой модели: всё нераспознанное внутри кузова (или с материалом салона) делится по зонам салона.
    Маленькие предметы (рычаг, руль, педали) целиком идут туда, где лежит большая их часть; большие куски — по треугольникам."""
    W, H, L = hi_c - lo_c
    fw, rw, x0 = ctx["fw"], ctx["rw"], ctx["x0"]
    wb = rw - fw
    if wb <= 0:
        return pieces
    out = []
    for p in pieces:
        c = (p["lo_c"] + p["hi_c"]) / 2
        ax_lo, ax_hi = (p["lo_c"][0] - x0) / (W / 2), (p["hi_c"][0] - x0) / (W / 2)
        uc, hc = (c[2] - fw) / wb, (c[1] - lo_c[1]) / H
        inside = (p["part"] is None and p["role"] not in ("glass", "rubber", "light", "plate")
                  and -0.1 < uc < 1.25 and 0.12 < hc < 0.97 and ax_lo > -0.92 and ax_hi < 0.92)
        if not (inside or (p["part"] is None and p["role"] == "interior")):
            out.append(p)
            continue
        pr = prims[p["prim"]]
        v = pr["pos"][pr["tri"][p["tri_ids"]]] @ R.T.astype(np.float32)
        tc = v.mean(1)
        lab = _cabin_label((tc[:, 2] - fw) / wb, (tc[:, 0] - x0) / (W / 2), (tc[:, 1] - lo_c[1]) / H)
        size = p["hi_c"] - p["lo_c"]
        if size[0] < 0.5 * W and size[2] < 0.35 * wb and size[1] < 0.65 * H:     # предмет целиком
            names, counts = np.unique(lab[lab != None], return_counts=True)  # noqa: E711
            part = str(names[counts.argmax()]) if len(names) else None
            conf = 0.45
            if part == "steering_wheel" and size[0] < 0.3 * W and size[1] < 0.3 * H and size[2] < 0.12 * wb \
                    and abs(size[0] - size[1]) < 0.5 * max(size[0], size[1]):
                conf = 0.65                                  # круглый предмет перед водителем — руль
            elif part in ("seat_driver", "seat_passenger") and 0.15 * W < size[0] < 0.5 * W and 0.25 * H < size[1] < 0.65 * H \
                    and 0.12 * wb < size[2] < 0.35 * wb:
                conf = 0.6                                   # отдельный предмет размером с кресло
            out.append(dict(p, part=part, conf=conf))
            continue
        for name in set(lab.tolist()):
            sel = np.nonzero(lab == name)[0] if name is not None else np.nonzero(lab == None)[0]  # noqa: E711
            pts = v[sel].reshape(-1, 3)
            out.append(dict(p, tri_ids=p["tri_ids"][sel], lo_c=pts.min(0), hi_c=pts.max(0), n=len(sel), part=name,
                            area=p["area"] * len(sel) / p["n"], conf=0.3))
    return out


def refine_wheels(pieces, prims, R, wheel_info, D, x0):
    """Колесо -> шина (внешнее кольцо или резина), диск (наружная сторона) и тормоз (глубже, у ступицы)."""
    centers = {w["part"]: w["c"] for w in wheel_info}
    out = []
    for p in pieces:
        wc = centers.get(p["part"])
        if wc is None:
            out.append(p)
            continue
        end_side = p["part"][len("wheel_"):]
        if p["role"] == "rubber":
            out.append(dict(p, part=f"tire_{end_side}", conf=0.9))
            continue
        pr = prims[p["prim"]]
        v = pr["pos"][pr["tri"][p["tri_ids"]]] @ R.T.astype(np.float32)
        c = v.mean(1)
        r = np.hypot(c[:, 1] - wc[1], c[:, 2] - wc[2]) / (D / 2)
        outward = np.abs(c[:, 0] - x0) - abs(wc[0] - x0)          # >0 — к наружной стороне колеса
        lab = np.where(r > 0.76, f"tire_{end_side}", np.where(outward > -0.07 * D, f"rim_{end_side}", f"brake_{end_side}"))
        for name in np.unique(lab):
            sel = np.nonzero(lab == name)[0]
            pts = v[sel].reshape(-1, 3)
            out.append(dict(p, tri_ids=p["tri_ids"][sel], lo_c=pts.min(0), hi_c=pts.max(0), n=len(sel), part=str(name),
                            area=p["area"] * len(sel) / p["n"], conf={"tire": 0.75, "rim": 0.7}.get(str(name).split("_")[0], 0.4)))
    return out


# ───────────── основной вход ─────────────
def split_glb(src: bytes, length_m: float | None = None) -> tuple[bytes, dict]:
    """GLB -> (GLB с деталями-узлами, manifest parts.json). Бросает SplitError, если разметить надёжно нельзя."""
    t0 = time.time()
    j, binary = read_glb(src)
    ext = set(j.get("extensionsUsed") or [])
    if ext & {"KHR_draco_mesh_compression", "EXT_meshopt_compression"}:
        raise SplitError("сжатые меши не поддерживаются")
    prims, pieces, lo, hi = collect(j, binary, material_roles(j))
    found = next((f + (up,) for up in (1, 2, 0) if (f := find_wheels(pieces, lo, hi, up))), None)   # glTF — Y вверх, иногда Z
    if not found:
        raise SplitError("не удалось найти четыре колеса")
    lat, lng, _, wcenters, D, up = found
    front = vote_front(pieces, lo, hi, up, lng, wcenters)
    R = orientation(up, lat, lng, front)

    def corners(a, b):
        return np.array([[x, y, z] for x in (a[0], b[0]) for y in (a[1], b[1]) for z in (a[2], b[2])]) @ R.T

    for p in pieces:
        cc = corners(p["lo"], p["hi"])
        p["lo_c"], p["hi_c"] = cc.min(0), cc.max(0)
    cc = corners(lo, hi)
    lo_c, hi_c = cc.min(0), cc.max(0)
    centers = [R @ c for c in wcenters]
    order = sorted(range(4), key=lambda i: centers[i][2])                 # нос −Z: передние колёса с меньшим z
    names = {}
    for grp, end in ((order[:2], "front"), (order[2:], "rear")):
        a, b = sorted(grp, key=lambda i: centers[i][0])                    # слева (−X) — водительская сторона
        names[a], names[b] = f"wheel_{end}_left", f"wheel_{end}_right"
    wheel_info = [{"c": centers[i], "half": np.array([D * 0.42, D * 0.56, D * 0.56]), "part": names[i]} for i in range(4)]
    classify_by_names(pieces, lo_c, hi_c, wheel_info, D)
    for p in pieces:                                                        # всё внутри цилиндра колеса: покрышка, диск, тормоза
        c = (p["lo_c"] + p["hi_c"]) / 2
        for w in wheel_info:
            inside = np.all(p["lo_c"] >= w["c"] - w["half"] * 1.25) and np.all(p["hi_c"] <= w["c"] + w["half"] * 1.25)
            if np.all(np.abs(c - w["c"]) < w["half"]) and inside and (p["hi_c"] - p["lo_c"]).max() < 1.35 * D:
                if not (p["part"] and re.match(r"(tire|rim|brake)_", p["part"])):
                    p["part"], p["conf"] = w["part"], 0.9
                break
    classify_early(pieces, lo_c, hi_c)
    _stamp(pieces, lambda p: 0.85 if p["role"] in ("glass", "light") else 0.5)
    ctx = body_context(pieces, lo_c, hi_c, wheel_info, D)
    classify_body(pieces, lo_c, hi_c, ctx)
    _stamp(pieces, lambda p: 0.9 if p["role"] == "plate" else 0.85 if p["role"] == "mirror" and p["part"].startswith("mirror")
           else 0.7 if re.match(r"(hood|trunk_lid|roof|front_bumper|rear_bumper|door_|fender_|mirror_)", p["part"]) else 0.45)
    pieces = refine_shell(pieces, prims, R, lo_c, hi_c, ctx)
    _stamp(pieces, lambda p: 0.6 if p["part"] in ("hood", "roof", "trunk_lid", "front_bumper", "rear_bumper", "body_paint") else 0.4)
    pieces = refine_wheels(pieces, prims, R, wheel_info, D, ctx["x0"])
    pieces = refine_interior(pieces, prims, R, lo_c, hi_c, ctx)
    for p in pieces:                                     # не уверены — не подписываем: кусок остаётся частью модели
        if p["part"] and (p.get("conf") or 0) < MIN_CONF:
            p["part"] = None
    for p in pieces:                                     # боковые стёкла — по дверям (граница — стойка B)
        if p["part"] and p["part"].startswith("side_glass_"):
            sd = p["part"].rsplit("_", 1)[1]
            z = (p["lo_c"][2] + p["hi_c"][2]) / 2
            p["part"] = f"window_{'front' if (ctx['pillar_b'] is None or z < ctx['pillar_b']) else 'rear'}_{sd}"
    return _export(j, binary, prims, pieces, R, lo_c, hi_c, wheel_info, D, length_m, time.time() - t0, front, (lat, lng, up))


def _export(j, binary, prims, pieces, R, lo_c, hi_c, wheel_info, D, length_m, took, front, axes):
    new_bin = bytearray(binary)
    groups: dict[tuple, list] = {}
    for p in pieces:
        groups.setdefault((p["node"], p["part"] or "unclassified"), {}).setdefault(p["prim"], []).append(p["tri_ids"])
    old_meshes = j["meshes"]
    node_mesh = {i: n.pop("mesh") for i, n in enumerate(j["nodes"]) if "mesh" in n}
    j["meshes"] = []
    taken = {n.get("name") for n in j["nodes"]}
    base_nodes = len(j["nodes"])
    group_nodes, group_names, node_names = {}, {}, {}
    for (node, part), per_prim in groups.items():
        out = []
        for k, tri_lists in per_prim.items():
            pr = prims[k]
            idx = pr["tri"][np.concatenate(tri_lists)].ravel()
            short = int(idx.max()) <= 65535
            raw = idx.astype("<u2" if short else "<u4").tobytes()
            new_bin.extend(b"\0" * (-len(new_bin) % 4))
            view = len(j["bufferViews"])
            j["bufferViews"].append(dict(buffer=0, byteOffset=len(new_bin), byteLength=len(raw), target=34963))
            new_bin.extend(raw)
            acc = len(j["accessors"])
            j["accessors"].append(dict(bufferView=view, componentType=5123 if short else 5125, count=int(idx.size), type="SCALAR",
                                       min=[int(idx.min())], max=[int(idx.max())]))
            prim = dict(old_meshes[pr["mesh"]]["primitives"][pr["prim"]], indices=acc)
            out.append(prim)
        name = part
        s = 1
        while name in taken:
            s += 1
            name = f"{part}_{s}"
        taken.add(name)
        holder = node
        if part in PARENT:                                   # узел-группа «колесо» под тем же исходным узлом
            key = (node, PARENT[part])
            if key not in group_nodes:
                group_nodes[key] = len(j["nodes"])
                j["nodes"][node].setdefault("children", []).append(len(j["nodes"]))
                gname = PARENT[part] if PARENT[part] not in taken else f"{PARENT[part]}_{len(group_nodes)}"
                taken.add(gname)
                j["nodes"].append(dict(name=gname, extras=dict(part_id=PARENT[part])))
                group_names.setdefault(PARENT[part], []).append(gname)
            holder = group_nodes[key]
        node_names.setdefault(part, []).append(name)
        j["nodes"][holder].setdefault("children", []).append(len(j["nodes"]))
        j["nodes"].append(dict(name=name, mesh=len(j["meshes"]), extras=dict(part_id=part)))
        j["meshes"].append(dict(name=name, primitives=out))
    # корневой узел поворота в канонический вид: нос −Z, верх +Y
    M = np.eye(4)
    M[:3, :3] = R
    root = len(j["nodes"])
    j["nodes"].append(dict(name="car_orientation", matrix=M.T.reshape(-1).tolist(), children=j["scenes"][j.get("scene", 0)]["nodes"]))
    j["scenes"][j.get("scene", 0)]["nodes"] = [root]
    j["buffers"][0]["byteLength"] = len(new_bin)
    text = json.dumps(j, ensure_ascii=False, separators=(",", ":")).encode()
    text += b" " * (-len(text) % 4)
    new_bin.extend(b"\0" * (-len(new_bin) % 4))
    glb = struct.pack("<4sIII", b"glTF", 2, 28 + len(text) + len(new_bin), len(text)) + b"JSON" + text + struct.pack("<I4s", len(new_bin), b"BIN\0") + bytes(new_bin)

    size = hi_c - lo_c
    scale = (length_m / size[2]) if length_m else None
    by_part: dict[str, dict] = {}
    for p in pieces:
        part = p["part"]
        if not part:
            continue
        for pid in (part, PARENT.get(part)):
            if not pid:
                continue
            r = by_part.setdefault(pid, dict(lo=p["lo_c"].copy(), hi=p["hi_c"].copy(), tri=0, nodes=set()))
            r["lo"], r["hi"] = np.minimum(r["lo"], p["lo_c"]), np.maximum(r["hi"], p["hi_c"])
            r["tri"] += p["n"]
    parts = []
    for part, r in sorted(by_part.items()):
        ru, group = PARTS_RU.get(part, (part, "exterior"))
        c = (r["lo"] + r["hi"]) / 2
        anchor = [float(c[0]), float(r["hi"][1] - 0.15 * (r["hi"][1] - r["lo"][1])), float(c[2])]
        parts.append(dict(id=part, label_ru=ru, group=group, nodes=group_names.get(part) or node_names.get(part) or [part], status="auto",
                          triangles=int(r["tri"]), bbox=dict(min=r["lo"].tolist(), max=r["hi"].tolist()), anchor=anchor, notes="",
                          **({"parent": PARENT[part]} if part in PARENT else {}), **({"pick": False} if NOT_PICKABLE.match(part) else {})))
    fl = next(w for w in wheel_info if w["part"] == "wheel_front_left")
    fr = next(w for w in wheel_info if w["part"] == "wheel_front_right")
    rl = next(w for w in wheel_info if w["part"] == "wheel_rear_left")
    manifest = dict(front="-Z", up="+Y", driver_side="-X", note="канонические координаты после поворота корневого узла",
                    parts=parts, split=dict(seconds=round(took, 2), pieces=len(pieces), assigned=sum(1 for p in pieces if p["part"]),
                                            front_axis=int(axes[1]), front_sign=int(front)),
                    measured=dict(length=float(size[2]), width=float(size[0]), height=float(size[1]),
                                  wheelbase=float(abs(fl["c"][2] - rl["c"][2])), track=float(abs(fl["c"][0] - fr["c"][0])),
                                  wheel_diameter=float(D), scale_to_m=scale))
    return glb, manifest
