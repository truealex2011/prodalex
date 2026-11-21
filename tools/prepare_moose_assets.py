"""Prepare the moose and mannequin assets without network tools.

Usage (Blender 4.2):
  blender -b --disable-autoexec -P tools/prepare_moose_assets.py -- \
    C:/Users/1/Downloads/realistic_animated_elk_3d_model.glb \
    C:/Users/1/Downloads/wooden_artist_mannequin.glb \
    app/static/media/moose
"""
import bpy, bmesh, sys, os, math
from mathutils import Vector

def args():
    a=sys.argv[sys.argv.index('--')+1:]
    if len(a)!=3: raise SystemExit('expected elk.glb mannequin.glb output_dir')
    return a

def clean():
    bpy.ops.wm.read_factory_settings(use_empty=True)

def import_glb(path):
    bpy.ops.import_scene.gltf(filepath=os.path.abspath(path))

def export(path, animations=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    bpy.ops.export_scene.gltf(filepath=os.path.abspath(path), export_format='GLB',
        export_animations=animations, export_skins=animations, export_materials='EXPORT',
        export_texture_dir='', export_image_format='AUTO', export_apply=False,
        export_draco_mesh_compression_enable=False)

def prepare_elk(src, out):
    clean(); import_glb(src)
    # Blender's glTF importer translates KHR_materials_pbrSpecularGlossiness
    # to Principled BSDF. Exporting from Blender therefore removes the
    # unsupported extension while retaining the imported appearance.
    arm=next((o for o in bpy.context.scene.objects if o.type=='ARMATURE'),None)
    if not arm: raise RuntimeError('elk armature not found')
    root=next((o for o in bpy.context.scene.objects if o.type=='EMPTY' and o.name=='Elk_Animated'),None)
    if root: root.name='elk_root'
    # The source is already in meters; leave transforms unchanged. Keep all
    # clips because the browser can choose the suitable transition at runtime.
    for o in bpy.context.scene.objects:
        if o.type=='MESH':
            for m in ([o.data.materials[i] for i in range(len(o.data.materials))]):
                if m and m.use_nodes:
                    for n in m.node_tree.nodes:
                        if n.type=='BSDF_PRINCIPLED':
                            n.inputs['Metallic'].default_value=min(1.0,max(0.0,n.inputs['Metallic'].default_value))
                            n.inputs['Roughness'].default_value=max(.35,n.inputs['Roughness'].default_value)
    export(os.path.join(out,'elk.glb'), True)

def loose_parts(obj):
    bpy.context.view_layer.objects.active=obj; obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT'); bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.separate(type='LOOSE'); bpy.ops.object.mode_set(mode='OBJECT')
    return [o for o in bpy.context.selected_objects if o.type=='MESH']

def center(obj):
    return obj.matrix_world @ obj.data.vertices[0].co if obj.data.vertices else Vector()

def bbox(obj):
    pts=[obj.matrix_world @ v.co for v in obj.data.vertices]
    return (Vector((min(p.x for p in pts),min(p.y for p in pts),min(p.z for p in pts))),
            Vector((max(p.x for p in pts),max(p.y for p in pts),max(p.z for p in pts))))

def rename_part(o, name):
    o.name=name; o.data.name=name; o.parent=None
    # Freeze the source mesh in the output's local coordinates while retaining
    # the original global placement.
    o.matrix_parent_inverse.identity()

def prepare_mannequin(src, out):
    clean(); import_glb(src)
    meshes=[o for o in bpy.context.scene.objects if o.type=='MESH']
    if not meshes: raise RuntimeError('mannequin mesh not found')
    obj=meshes[0]; parts=loose_parts(obj)
    # Source axes are Z-up. Work in source coordinates, then make the output
    # explicitly Y-up for three.js with one root transform.
    allpts=[o.matrix_world @ v.co for o in parts for v in o.data.vertices]
    lo=Vector((min(p.x for p in allpts),min(p.y for p in allpts),min(p.z for p in allpts)))
    hi=Vector((max(p.x for p in allpts),max(p.y for p in allpts),max(p.z for p in allpts)))
    height=max(hi.z-lo.z,1e-6); scale=1.75/height
    root=bpy.data.objects.new('mannequin_root',None); bpy.context.collection.objects.link(root); root.rotation_euler=(-math.pi/2,0,0); root.scale=(scale,scale,scale)
    # classify by loose-part center. A part can be a complete limb; the
    # nearest anatomical region is used only for naming, never by material.
    buckets={}
    for o in parts:
        c=(bbox(o)[0]+bbox(o)[1])/2; x,y,z=c.x,c.y,c.z
        zn=(z-lo.z)/height
        side='l' if x<0 else 'r'
        if zn>.88:name='head'
        elif zn>.73:name='chest'
        elif zn>.62:name='pelvis'
        elif x < -.12 or x>.12:
            name=('upperarm_' if zn>.61 else 'upperleg_')+side
        else:name='lower_body'
        buckets.setdefault(name, []).append(o)
    # The mannequin's imported mesh has many disconnected caps and pegs. Join
    # only within the same spatial/anatomical bucket so the exported asset has
    # meaningful segments rather than one node per loose island.
    merged={}
    for name, objs in buckets.items():
        bpy.ops.object.select_all(action='DESELECT')
        for o in objs:o.select_set(True)
        bpy.context.view_layer.objects.active=objs[0]
        if len(objs)>1:bpy.ops.object.join()
        o=objs[0]; rename_part(o,name); o.parent=root; merged[name]=o
    buckets=merged
    # Add named articulation nodes even when source geometry is a single rigid
    # chunk; this provides stable hooks for the hit animation.
    names=['pelvis','chest','neck','head','upperarm_l','forearm_l','hand_l','upperarm_r','forearm_r','hand_r','thigh_l','shin_l','foot_l','thigh_r','shin_r','foot_r']
    for name in names:
        if name in buckets: continue
        e=bpy.data.objects.new(name,None); bpy.context.collection.objects.link(e); e.parent=root
    export(os.path.join(out,'mannequin_rig.glb'), False)
    with open(os.path.join(out,'mannequin_report.txt'),'w',encoding='utf8') as f:
        f.write('Source license: Sketchfab Standard; do not publish this asset without permission.\n')
        f.write('Source axes: Z-up. Output root rotates to Y-up and scales height to 1.75 m.\n')
        f.write('Loose parts: '+', '.join(sorted(buckets))+'\n')

def main():
    elk,man,out=args(); prepare_elk(elk,out); prepare_mannequin(man,out)
if __name__=='__main__': main()
