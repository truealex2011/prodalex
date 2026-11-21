"""Фон для экранов до 3D-экскурсии: рендер той же улицы (street.glb), что и в сцене.

    blender -b --disable-autoexec -P tools/render_backdrop.py -- <street.glb> <out.jpg> [width height samples]
        [car.glb:x:z:yaw:#rrggbb:length ...]

Машины ставятся в координатах three.js (x, z — точка на дороге, yaw — градусы вокруг вертикали,
0 = мордой к +X, как в экскурсии), цвет — на материалы кузова, длина — нормировка модели, м.

Камера стоит на нашей полосе и смотрит вдоль улицы в глубину квартала; свет — низкое солнце
(небо Nishita), чтобы стеклянные панели интерфейса было на чём «читать».
Координаты камеры заданы в системе three.js сцены (метры, Y вверх, масштаб улицы ×5).
"""
import math
import sys

import bpy
from mathutils import Vector

args = sys.argv[sys.argv.index("--") + 1:]
src, dst = args[0], args[1]
W, H, SAMPLES = (int(args[2]), int(args[3]), int(args[4])) if len(args) >= 5 else (1920, 1080, 64)
CARS = args[5:]
PAINT = ("kuzov", "paint", "carpaint", "carpaint_sensor")

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=src)
SCALE = 5.0
tops = [o for o in bpy.context.scene.objects if o.parent is None]
for o in tops:
    o.scale = [s * SCALE for s in o.scale]
    o.location = o.location * SCALE
# как в viewer.js: низ квартала — на нуле, тогда асфальт на y≈0
bpy.context.view_layer.update()
low = min((o.matrix_world @ Vector(c)).z for o in bpy.context.scene.objects if o.type == "MESH" for c in o.bound_box)
for o in tops:
    o.location.z -= low


def three(x, y, z):
    """Точка сцены three.js → Blender (Z вверх)."""
    return Vector((x, -z, y))


def add_car(spec):
    path, x, z, yaw, color, length = spec.rsplit(":", 5)
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    new = [o for o in bpy.data.objects if o not in before]
    root = bpy.data.objects.new("car_root", None)
    bpy.context.scene.collection.objects.link(root)
    for o in new:
        if o.parent is None:
            o.parent = root
    bpy.context.view_layer.update()
    pts = [o.matrix_world @ Vector(c) for o in new if o.type == "MESH" for c in o.bound_box]
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    k = float(length) / max(hi.x - lo.x, hi.y - lo.y)
    centre = Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z))
    for o in new:
        if o.parent is root:
            o.location = (o.location - centre) * k
            o.scale = [s * k for s in o.scale]
    # модель смотрит в −Z three.js (= +Y Блендера); yaw 0 — мордой к +X, как машина в экскурсии
    root.rotation_euler = (0, 0, math.radians(-90 + float(yaw)))
    root.location = three(float(x), 0.0, float(z))
    rgb = tuple(int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    lin = tuple(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb)
    for o in new:
        for slot in getattr(o, "material_slots", []):
            m = slot.material
            if m and m.name.lower().split(".")[0] in PAINT and m.use_nodes:
                m = m.copy(); slot.material = m
                for n in m.node_tree.nodes:
                    if n.type == "BSDF_PRINCIPLED":
                        n.inputs["Base Color"].default_value = (*lin, 1)
                        n.inputs["Metallic"].default_value = 0.6
                        n.inputs["Roughness"].default_value = 0.22
                        n.inputs["Coat Weight"].default_value = 1.0


hero = None
for spec in CARS:
    add_car(spec)
    if hero is None:
        _, hx, hz, *_ = spec.rsplit(":", 5)
        hero = three(float(hx), 0.7, float(hz))


scene = bpy.context.scene
cam_data = bpy.data.cameras.new("cam")
cam_data.lens = 30
cam_data.dof.use_dof = True
cam_data.dof.focus_distance = 14
cam_data.dof.aperture_fstop = 2.8
cam = bpy.data.objects.new("cam", cam_data)
scene.collection.objects.link(cam)
CAM = [float(v) for v in __import__("os").environ.get("BD_CAM", "17,1.35,4.2,-30,2.6,2.4").split(",")]
cam.location = three(*CAM[:3])
target = three(*CAM[3:])
cam_data.dof.focus_distance = float(__import__("os").environ.get("BD_FOCUS", "14"))
cam.rotation_euler = (target - cam.location).to_track_quat("-Z", "Y").to_euler()
scene.camera = cam

# небо и солнце: тёплый вечер
world = bpy.data.worlds.new("sky")
scene.world = world
world.use_nodes = True
nt = world.node_tree
nt.nodes.clear()
sky = nt.nodes.new("ShaderNodeTexSky")
sky.sky_type = "NISHITA"
sky.sun_elevation = math.radians(9)
sky.sun_rotation = math.radians(250)
sky.altitude = 0
sky.air_density = 1.2
sky.dust_density = 2.2
bg = nt.nodes.new("ShaderNodeBackground")
bg.inputs["Strength"].default_value = 0.32
out = nt.nodes.new("ShaderNodeOutputWorld")
nt.links.new(sky.outputs["Color"], bg.inputs["Color"])
nt.links.new(bg.outputs["Background"], out.inputs["Surface"])

sun_data = bpy.data.lights.new("sun", "SUN")
sun_data.energy = 3.2
sun_data.angle = math.radians(1.2)
sun_data.color = (1.0, 0.82, 0.62)
sun = bpy.data.objects.new("sun", sun_data)
sun.rotation_euler = (math.radians(90 - 9), 0, math.radians(250 - 90))
scene.collection.objects.link(sun)

# заполняющий свет на первую машину — она в тени домов, а это главный объект кадра
if hero is not None:
    key_data = bpy.data.lights.new("key", "AREA")
    key_data.energy = 900
    key_data.size = 4
    key_data.color = (1.0, 0.93, 0.85)
    key = bpy.data.objects.new("key", key_data)
    key.location = cam.location + Vector((-1.5, 3.0, 1.8))
    key.rotation_euler = (hero - key.location).to_track_quat("-Z", "Y").to_euler()
    scene.collection.objects.link(key)

# стекло пакета без данных и двусторонние полигоны — как в three.js
for m in bpy.data.materials:
    m.use_backface_culling = False

scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.samples = SAMPLES
scene.cycles.use_denoising = True
scene.cycles.max_bounces = 6
scene.render.resolution_x, scene.render.resolution_y = W, H
scene.render.resolution_percentage = 100
scene.view_settings.view_transform = "AgX"
scene.view_settings.look = "AgX - Medium High Contrast"
scene.render.image_settings.file_format = "JPEG"
scene.render.image_settings.quality = 84
scene.render.filepath = dst
bpy.ops.render.render(write_still=True)
print("saved", dst)
