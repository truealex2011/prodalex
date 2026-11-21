# blender -b --disable-autoexec <pack.blend> --python-exit-code 1 -P export_pack.py -- <textures_dir> <out.glb> <max_tex>
# Перепривязывает текстуры к файлам из архива, уменьшает их и экспортирует GLB (WebP + Draco).
import os
import sys

import bpy

argv = sys.argv[sys.argv.index("--") + 1:]
tex_dir, dst, max_tex = argv[0], argv[1], int(argv[2])
files = {f.lower(): os.path.join(tex_dir, f) for f in os.listdir(tex_dir)}
stems = {os.path.splitext(f)[0]: p for f, p in files.items()}  # rooftop.png -> rooftop.jpg

# в .blend пути вида ..\Desktop\Textures\..., а в архиве текстуры переименованы
ALIASES = [
    (("asphalt", "albedo"), "asphalt.png"), (("asphalt", "normal"), "asphaltnormal.png"),
    (("asphalt", "roughness"), "asphaltroughness.png"),
    (("pavement", "albedo"), "pavement.png"), (("pavement", "normal"), "pavementnormal.png"),
    (("pavement", "roughness"), "pavementroughness.png"),
]

missing = []
for img in bpy.data.images:
    if img.source != "FILE" or img.packed_file:
        continue
    base = os.path.basename(img.filepath.replace("\\", "/")).lower()
    target = files.get(base) or stems.get(os.path.splitext(base)[0])
    if not target:
        for keys, name in ALIASES:
            if all(k in base for k in keys) and name in files:
                target = files[name]
                break
    if target:
        img.filepath = target
        img.reload()
    else:
        missing.append(img.name)
print("[pack] missing textures (left untextured):", missing)

scaled = 0
for img in bpy.data.images:
    if img.source == "FILE" and not img.has_data:
        try:
            _ = img.pixels[0]  # Blender грузит картинки лениво — заставляем загрузить
        except Exception:
            continue
    if img.has_data and max(img.size) > max_tex:
        w, h = img.size
        k = max_tex / max(w, h)
        img.scale(max(1, int(w * k)), max(1, int(h * k)))
        scaled += 1
print(f"[pack] scaled {scaled} textures to <= {max_tex}px")

# узлы с отсутствующими картинками дают в glTF пустые текстуры — убираем их из материалов
removed = 0
for mat in bpy.data.materials:
    if not mat.use_nodes:
        continue
    for node in list(mat.node_tree.nodes):
        if node.type == "TEX_IMAGE" and (node.image is None or not node.image.has_data):
            mat.node_tree.nodes.remove(node)
            removed += 1
print(f"[pack] removed {removed} image nodes without data")

bpy.ops.export_scene.gltf(
    filepath=dst, export_format="GLB",
    export_image_format="WEBP", export_image_quality=80,
    export_draco_mesh_compression_enable=True, export_draco_mesh_compression_level=6,
    export_draco_position_quantization=14, export_draco_normal_quantization=10,
    export_draco_texcoord_quantization=12,
    export_animations=False, export_cameras=False, export_lights=False,
    export_apply=True,  # дорога и тротуары собраны модификатором Array — без этого экспортируется один кусок
)
print("[pack] modifiers:", sorted({(o.name, m.type) for o in bpy.data.objects for m in getattr(o, "modifiers", [])})[:40])
print("[pack] exported", dst, os.path.getsize(dst))
