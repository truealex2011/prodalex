"""Шарнирный манекен для 3D-теста на лося из wooden_artist_mannequin.glb (Sketchfab, Smoggybeard).

Модель — один меш из ~100 отдельных деревянных деталей. Делим его на связные куски, раскладываем
по сегментам тела по положению и экспортируем каждый сегмент отдельным мешем с началом координат
в его шарнире (центр шара-сустава). Иерархию и позу собирает moose3d.js.

    blender -b --disable-autoexec -P tools/prepare_mannequin.py -- <in.glb> <out.glb>

В результате (Y вверх, перёд −Z, метры): узлы head, chest, pelvis, upperarm_p/n, forearm_p/n,
hand_p/n, thigh_p/n, shin_p/n, foot_p/n (p — сторона +X, n — −X).
"""
import sys
import bpy
from mathutils import Vector, Matrix

src, dst = sys.argv[sys.argv.index("--") + 1:][:2]
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=src)
meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
for o in meshes:
    o.select_set(True)
bpy.context.view_layer.objects.active = meshes[0]
bpy.ops.object.join() if len(meshes) > 1 else None
body = bpy.context.view_layer.objects.active
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
for o in list(bpy.context.scene.objects):          # пустышки Sketchfab больше не нужны
    if o.type != "MESH":
        bpy.data.objects.remove(o)
# лицом в +Y Блендера (= −Z в three.js, как машина)
body.data.transform(Matrix.Rotation(3.14159265, 4, "Z"))

# шарниры (Blender, Z вверх) — центры шаров-суставов после поворота (x → −x, y → −y)
def side(x): return "p" if x > 0 else "n"
J = {
    "neck": Vector((0, 0, 0.21)), "waist": Vector((0, 0, -0.088)),
}
for sx in (1, -1):
    s = side(sx)
    J[f"shoulder_{s}"] = Vector((0.153 * sx, 0, 0.171))
    J[f"elbow_{s}"] = Vector((0.257 * sx, 0, -0.105))
    J[f"wrist_{s}"] = Vector((0.272 * sx, 0, -0.36))
    J[f"hip_{s}"] = Vector((0.066 * sx, 0, -0.36))
    J[f"knee_{s}"] = Vector((0.066 * sx, 0, -0.787))
    J[f"ankle_{s}"] = Vector((0.066 * sx, 0, -1.207))

def segment(c):
    x, z = c.x, c.z
    s = side(x)
    if abs(x) >= 0.13:                       # руки
        if z >= 0.16: return "chest"         # плечевой шар остаётся на груди
        if z > -0.07: return f"upperarm_{s}"
        if z > -0.375: return f"forearm_{s}"
        return f"hand_{s}"
    if z > 0.2: return "head"
    if z > -0.07: return "chest"
    if z > -0.39: return "pelvis"
    if z > -0.775: return f"thigh_{s}"
    if z > -1.19: return f"shin_{s}"
    return f"foot_{s}"

PIVOT = {"head": "neck", "chest": "waist", "pelvis": "waist"}
for s in "pn":
    PIVOT |= {f"upperarm_{s}": f"shoulder_{s}", f"forearm_{s}": f"elbow_{s}", f"hand_{s}": f"wrist_{s}",
              f"thigh_{s}": f"hip_{s}", f"shin_{s}": f"knee_{s}", f"foot_{s}": f"ankle_{s}"}

bpy.ops.object.select_all(action="DESELECT")
body.select_set(True)
bpy.context.view_layer.objects.active = body
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.separate(type="LOOSE")
bpy.ops.object.mode_set(mode="OBJECT")

groups = {}
for p in [o for o in bpy.context.scene.objects if o.type == "MESH"]:
    vs = [p.matrix_world @ v.co for v in p.data.vertices]
    c = sum(vs, Vector()) / len(vs)
    groups.setdefault(segment(c), []).append(p)

for name, parts in groups.items():
    bpy.ops.object.select_all(action="DESELECT")
    for p in parts:
        p.select_set(True)
    bpy.context.view_layer.objects.active = parts[0]
    if len(parts) > 1:
        bpy.ops.object.join()
    o = bpy.context.view_layer.objects.active
    o.name = o.data.name = name
    piv = J[PIVOT[name]]
    o.data.transform(Matrix.Translation(-piv))
    o.location = piv
    print(f"{name:12s} pieces={len(parts):3d} verts={len(o.data.vertices)}")

bpy.ops.object.select_all(action="SELECT")
bpy.ops.export_scene.gltf(filepath=dst, export_format="GLB", use_selection=True, export_apply=True,
                          export_yup=True, export_extras=False, export_animations=False)
print("saved", dst, sorted(groups))
