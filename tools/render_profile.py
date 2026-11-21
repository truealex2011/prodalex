"""Боковой вид машины для лоадера и баннеров: рендер реальной 3D-модели, а не рисунок.

    blender -b --disable-autoexec -P tools/render_profile.py -- <car.glb> <outdir> [width samples]

Получаем в <outdir>:
  body.png  — кузов без колёс, прозрачный фон, нос машины справа;
  wheel.png — одно колесо крупным планом (его в вёрстке крутят отдельно);
  layout.json — где стоят колёса на кузове (доли от ширины/высоты картинки) и размер колеса.
"""
import json
import math
import os
import sys

import bpy
from mathutils import Vector

args = sys.argv[sys.argv.index("--") + 1:]
src, out = args[0], args[1]
W = int(args[2]) if len(args) > 2 else 1600
SAMPLES = int(args[3]) if len(args) > 3 else 96
PAINT = ("kuzov", "paint", "carpaint", "carpaint_sensor")

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=src)
scene = bpy.context.scene
bpy.context.view_layer.update()


def subtree(o):
    yield o
    for c in o.children:
        yield from subtree(c)


def world_box(objs):
    pts = [o.matrix_world @ Vector(c) for o in objs if o.type == "MESH" for c in o.bound_box]
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return lo, hi


wheels = {o.name: list(subtree(o)) for o in bpy.data.objects if o.name.startswith("wheel_")}
assert len(wheels) == 4, f"нужно 4 колеса, найдено {list(wheels)}"
wheel_objs = {id(o) for objs in wheels.values() for o in objs}
all_mesh = [o for o in bpy.data.objects if o.type == "MESH"]
car_lo, car_hi = world_box([o for o in all_mesh])
centres = {n: (lambda b: (b[0] + b[1]) / 2)(world_box(objs)) for n, objs in wheels.items()}
near = sorted(wheels, key=lambda n: -centres[n].x)[:2]   # колёса со стороны камеры (камера на +X)
front_near = max(near, key=lambda n: centres[n].y)       # нос машины — +Y
lo_b, hi_b = world_box(wheels[front_near])
wheel_d = hi_b.z - lo_b.z

# краска кузова: тёмный графит с лаком
lin = lambda c: c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
rgb = tuple(lin(int("23262c"[i:i + 2], 16) / 255) for i in (0, 2, 4))
for o in all_mesh:
    for slot in o.material_slots:
        m = slot.material
        if m and m.name.lower().split(".")[0] in PAINT and m.use_nodes:
            m = m.copy()
            slot.material = m
            for n in m.node_tree.nodes:
                if n.type == "BSDF_PRINCIPLED":
                    n.inputs["Base Color"].default_value = (*rgb, 1)
                    n.inputs["Metallic"].default_value = 0.55
                    n.inputs["Roughness"].default_value = 0.22
                    n.inputs["Coat Weight"].default_value = 1.0
for m in bpy.data.materials:
    m.use_backface_culling = False

# свет: мягкий верхний софтбокс, холодная заполняющая, красный контровой с кормы
world = bpy.data.worlds.new("w")
scene.world = world
world.use_nodes = True
bg = world.node_tree.nodes["Background"]
bg.inputs["Color"].default_value = (0.02, 0.022, 0.03, 1)
bg.inputs["Strength"].default_value = 1.0


def area(name, loc, target, energy, size, color):
    d = bpy.data.lights.new(name, "AREA")
    d.energy, d.size, d.color = energy, size, color
    o = bpy.data.objects.new(name, d)
    o.location = loc
    scene.collection.objects.link(o)
    o.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()


c = (car_lo + car_hi) / 2
L = car_hi.y - car_lo.y
area("top", (c.x + 1.5, c.y, car_hi.z + L * 0.9), c, 1500, L * 0.9, (1, 1, 1))
area("fill", (c.x + L, c.y + L * 0.9, c.z), c, 700, L * 0.6, (0.75, 0.85, 1.0))
area("rim", (c.x + L * 0.4, car_lo.y - L * 0.7, c.z + 0.4), c, 2600, L * 0.35, (1.0, 0.27, 0.22))
area("nose", (c.x + L * 0.5, car_hi.y + L * 0.6, c.z + 0.2), c, 600, L * 0.3, (1, 1, 1))

scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.samples = SAMPLES
scene.cycles.use_denoising = True
scene.render.film_transparent = True
scene.view_settings.view_transform = "AgX"
scene.render.image_settings.file_format = "PNG"
scene.render.image_settings.color_mode = "RGBA"

cam_data = bpy.data.cameras.new("cam")
cam_data.type = "ORTHO"
cam = bpy.data.objects.new("cam", cam_data)
scene.collection.objects.link(cam)
scene.camera = cam
cam.rotation_euler = (math.radians(90), 0, math.radians(90))  # смотрит вдоль −X, вверх Z, вправо +Y


def shoot(centre_y, centre_z, scale, w, h, path):
    cam.location = (car_hi.x + 20, centre_y, centre_z)
    cam_data.ortho_scale = scale
    scene.render.resolution_x, scene.render.resolution_y = w, h
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)


os.makedirs(out, exist_ok=True)

# 1) кузов без колёс
for objs in wheels.values():
    for o in objs:
        o.hide_render = True
pad = 1.06
scale = L * pad
H = int(W * (car_hi.z - car_lo.z + 0.2) / scale)
cy, cz = c.y, (car_lo.z + car_hi.z) / 2 + 0.0
shoot(cy, cz, scale, W, H, os.path.join(out, "body.png"))

# 2) одно колесо крупно
for o in all_mesh:
    o.hide_render = id(o) not in {id(x) for x in wheels[front_near]}
wc = centres[front_near]
shoot(wc.y, wc.z, wheel_d * 1.1, 512, 512, os.path.join(out, "wheel.png"))

# где колёса на кузове (в долях картинки; y вниз)
def px(n):
    p = centres[n]
    return [round((p.y - cy) / scale + 0.5, 4), round(0.5 * H / W * 1.0 + 0 - (p.z - cz) / scale, 4)]

layout = {"w": W, "h": H, "wheel_d": round(wheel_d / scale, 4),
          "wheels": {n: px(n) for n in near}, "front": front_near}
json.dump(layout, open(os.path.join(out, "layout.json"), "w"), indent=1)
print("LAYOUT", layout)
