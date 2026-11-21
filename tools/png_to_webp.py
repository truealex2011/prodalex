"""Конвертация PNG в WebP средствами Blender (Pillow в проекте не используется).

    blender -b -P tools/png_to_webp.py -- <in.png> <out.webp> [max_width] [quality]
"""
import sys

import bpy

args = sys.argv[sys.argv.index("--") + 1:]
src, dst = args[0], args[1]
max_w = int(args[2]) if len(args) > 2 else 0
quality = int(args[3]) if len(args) > 3 else 88

img = bpy.data.images.load(src)
if max_w and img.size[0] > max_w:
    h = round(img.size[1] * max_w / img.size[0])
    img.scale(max_w, h)
scene = bpy.context.scene
scene.render.image_settings.file_format = "WEBP"
scene.render.image_settings.color_mode = "RGBA"
scene.render.image_settings.quality = quality
img.save_render(filepath=dst, scene=scene)
print("saved", dst, img.size[:])
