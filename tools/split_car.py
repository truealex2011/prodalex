"""Geometry-only segmentation of static GLB cars. See docs/SPLIT_CAR.md."""
import argparse
import itertools
import json
import re
import struct
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import numpy as np


def read_glb(path):
    raw = path.read_bytes()
    magic, version, size = struct.unpack_from('<4sII', raw)
    if (magic, version, size) != (b'glTF', 2, len(raw)):
        raise ValueError('Expected a glTF 2.0 binary file')
    n, typ = struct.unpack_from('<I4s', raw, 12)
    if typ != b'JSON':
        raise ValueError('Missing GLB JSON chunk')
    j = json.loads(raw[20:20+n]); offset = 20+n
    length, typ = struct.unpack_from('<I4s', raw, offset)
    if typ != b'BIN\0' or offset+8+length != size or len(j['buffers']) != 1 or 'uri' in j['buffers'][0]:
        raise ValueError('Expected one embedded binary buffer')
    if j.get('skins') or j.get('animations') or len(j['scenes']) != 1:
        raise ValueError('Export one static scene without rigs or animations first')
    if any('uri' in image and not image['uri'].startswith('data:') for image in j.get('images', [])):
        raise ValueError('Embed external textures before segmentation')
    if {'KHR_draco_mesh_compression', 'EXT_meshopt_compression', 'EXT_mesh_gpu_instancing'} & set(j.get('extensionsUsed', [])):
        raise ValueError('Decompress and realize mesh instances before segmentation')
    return j, bytearray(raw[offset+8:offset+8+j['buffers'][0]['byteLength']])


def write_glb(path, j, b):
    j['buffers'][0]['byteLength'] = len(b)
    text = json.dumps(j, ensure_ascii=False, separators=(',', ':')).encode(); text += b' '*(-len(text)%4)
    b.extend(b'\0'*(-len(b)%4))
    path.write_bytes(struct.pack('<4sIIII', b'glTF', 2, 28+len(text)+len(b),len(text),0x4E4F534A)+text+struct.pack('<I4s',len(b),b'BIN\0')+b)


def array(j, b, index, dim):
    a = j['accessors'][index]; v = j['bufferViews'][a['bufferView']]
    if a.get('sparse') or a.get('normalized') or a['type'] != ('SCALAR' if dim == 1 else 'VEC3'):
        raise ValueError('Unsupported position/index accessor; re-export standard GLB')
    dt = np.dtype({5121:'u1',5123:'<u2',5125:'<u4',5126:'<f4',5122:'<i2',5120:'i1'}[a['componentType']])
    return np.ndarray((a['count'],dim), dt, b, v.get('byteOffset',0)+a.get('byteOffset',0),
                      strides=(v.get('byteStride',dim*dt.itemsize),dt.itemsize)).copy()


def matrix(n):
    if 'matrix' in n:
        return np.array(n['matrix']).reshape(4,4).T
    x,y,z,w = n.get('rotation',[0,0,0,1]); m=np.eye(4)
    m[:3,:3]=np.array([[1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)],
                       [2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)],
                       [2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]]) @ np.diag(n.get('scale',[1,1,1]))
    m[:3,3]=n.get('translation',[0,0,0]); return m


def islands(points, tri):
    # Join coincident seam vertices only for analysis; keep UVs and normals untouched.
    _, ids=np.unique(np.round(points, decimals=6),axis=0,return_inverse=True)
    t=ids.reshape(-1)[tri]; parent=list(range(int(t.max())+1))
    def root(i):
        while i!=parent[i]:
            parent[i]=parent[parent[i]]; i=parent[i]
        return i
    for x,y,z in t.tolist():
        a=root(x); parent[root(y)]=a; parent[root(z)]=a
    groups=defaultdict(list)
    for k,x in enumerate(t[:,0]): groups[root(x)].append(k)
    return groups.values()


def collect(j,b,front):
    # Analysis coordinates: x right, y up, z toward nose, in world units.
    f=np.array({'-Z':[0,0,-1],'+Z':[0,0,1],'+X':[1,0,0],'-X':[-1,0,0]}[front]); up=np.array([0,1,0]); basis=np.stack([np.cross(f,up),up,f],axis=1)
    pieces=[]; stack=[(i,np.eye(4)) for i in j['scenes'][0]['nodes']]; seen=set()
    while stack:
        i,m=stack.pop(); n=j['nodes'][i]
        if i in seen: raise ValueError('Expected a tree of scene nodes')
        seen.add(i); m=m@matrix(n); stack.extend((k,m) for k in n.get('children',[]))
        if 'mesh' not in n: continue
        for pi,p in enumerate(j['meshes'][n['mesh']]['primitives']):
            if p.get('mode',4)!=4 or p.get('targets'): raise ValueError('Expected static triangle meshes')
            v=array(j,b,p['attributes']['POSITION'],3); w=v@m[:3,:3].T+m[:3,3]; car=w@basis
            indices=array(j,b,p['indices'],1).ravel() if 'indices' in p else np.arange(len(v),dtype=np.uint32)
            tri=indices.reshape(-1,3); span=np.ptp(car,axis=0).max()
            for rows in islands((car-car.min(0))/max(span,1e-12),tri):
                idx=tri[rows]; used=np.unique(idx); a=car[used]; q=w[used]
                pieces.append(dict(node=i,pi=pi,idx=idx,lo=a.min(0),hi=a.max(0),world_lo=q.min(0),world_hi=q.max(0),part='unclassified'))
    if not pieces: raise ValueError('No triangles in the scene')
    return pieces


def classify(pieces):
    lo=np.min([p['lo'] for p in pieces],axis=0); hi=np.max([p['hi'] for p in pieces],axis=0); size=hi-lo
    if np.min(size)<=0: raise ValueError('Degenerate vehicle bounds')
    for p in pieces:
        p['c']=((p['lo']+p['hi'])/2-lo)/size; p['d']=(p['hi']-p['lo'])/size
    anchors={}
    for end,side in itertools.product(('front','rear'),('left','right')):
        candidates=[]
        for p in pieces:
            x,y,z=p['c']; w,h,l=p['hi']-p['lo']
            if (x<.5)!=(side=='left') or (z>.5)!=(end=='front'): continue
            if abs(x-.5)<.28 or abs(z-.5)<.13 or not (.07<y<.38): continue
            if .68<h/max(l,1e-12)<1.4 and .10<w/max(h,1e-12)<.65 and .09<l/size[2]<.26 and p['d'][1]>.20:
                candidates.append((h*l,p))
        if candidates: anchors[end,side]=max(candidates,key=lambda a:a[0])[1]
    # Require a matching axle on both sides, rather than treating any disc as a wheel.
    for end in ('front','rear'):
        a=anchors.get((end,'left')); b=anchors.get((end,'right'))
        if a is None or b is None or np.max(abs(a['c'][1:]-b['c'][1:]))>.055: continue
        for side,anchor in [('left',a),('right',b)]:
            ctr=(anchor['hi']+anchor['lo'])/2; half=(anchor['hi']-anchor['lo'])/2
            for p in pieces:
                if np.all(p['lo']>=ctr-half*[1.32,1.04,1.04]) and np.all(p['hi']<=ctr+half*[1.32,1.04,1.04]): p['part']=f'wheel_{end}_{side}'
    for p in pieces:
        if p['part']!='unclassified': continue
        x,y,z=p['c']; w,h,l=p['d']; side='left' if x<.5 else 'right'
        if abs(x-.5)>.45 and .45<y<.85 and .49<z<.79 and .035<w<.17 and .025<h<.16 and .015<l<.12: label='mirror_'+side
        elif abs(x-.5)<.11 and w>.45 and h<.13 and y>.87 and .20<l<.65: label='roof'
        elif abs(x-.5)<.11 and w>.5 and h<.23 and .40<y<.83 and z>.72 and .12<l<.4: label='hood'
        elif abs(x-.5)<.10 and w>.48 and h<.24 and .46<y<.85 and z<.22 and .07<l<.3: label='trunk_lid'
        elif abs(x-.5)<.10 and w>.56 and .13<h<.39 and .1<l<.34 and y>.70 and (.54<z<.77 or .14<z<.42): label='windshield' if z>.5 else 'rear_window'
        elif abs(x-.5)>.30 and .25<y<.8 and .05<w<.25 and .32<h<.84 and .16<l<.34 and .25<z<.74: label='door_'+('front_' if z>.5 else 'rear_')+side
        elif abs(x-.5)<.1 and w>.63 and .07<h<.42 and .03<l<.22 and .1<y<.48 and (z>.87 or z<.12): label='front_bumper' if z>.5 else 'rear_bumper'
        else: continue
        p['part']=label


def export(j,b,pieces):
    old=j['meshes']; j['meshes']=[]; summary={}; clusters=defaultdict(lambda:defaultdict(list)); names={n.get('name') for n in j['nodes']}
    for p in pieces: clusters[p['node'],p['part']][p['pi']].append(p)
    original={i:n.pop('mesh') for i,n in enumerate(j['nodes']) if 'mesh' in n}
    for (i,label),primitives in clusters.items():
        mesh=old[original[i]]; out=[]; record=summary.setdefault(label,dict(nodes=[],triangles=0,status='review' if label=='unclassified' else 'approximate'))
        for pi,group in primitives.items():
            idx=np.concatenate([p['idx'] for p in group]); short=int(idx.max())<=65535; raw=idx.astype('<u2' if short else '<u4').tobytes(); b.extend(b'\0'*(-len(b)%4)); view=len(j['bufferViews']); j['bufferViews'].append(dict(buffer=0,byteOffset=len(b),byteLength=len(raw),target=34963)); b.extend(raw)
            acc=len(j['accessors']); j['accessors'].append(dict(bufferView=view,componentType=5123 if short else 5125,count=idx.size,type='SCALAR',min=[int(idx.min())],max=[int(idx.max())])); out.append(dict(mesh['primitives'][pi],indices=acc)); record['triangles']+=len(idx)
            low=np.min([p['world_lo'] for p in group],axis=0); high=np.max([p['world_hi'] for p in group],axis=0)
            prev=record.get('bbox',dict(min=low,max=high)); record['bbox']=dict(min=np.minimum(prev['min'],low).tolist(),max=np.maximum(prev['max'],high).tolist())
        name=label; suffix=1
        while name in names: suffix+=1; name=f'{label}_{suffix}'
        names.add(name); record['nodes'].append(name); j['nodes'][i].setdefault('children',[]).append(len(j['nodes'])); j['nodes'].append(dict(name=name,mesh=len(j['meshes']),extras=dict(part_id=label))); j['meshes'].append(dict(mesh,name=name,primitives=out))
    return summary


def lookup(vin, make, model, year):
    base='https://vpic.nhtsa.dot.gov/api/vehicles/'
    if vin: route='DecodeVinValues/'+vin; params={'modelyear':year} if year else {}
    else: route='GetModelsForMakeYear/make/'+quote(make,safe='')+'/modelyear/'+str(year) if year else 'GetModelsForMake/'+quote(make,safe=''); params={}
    url=base+route+'?'+urlencode(dict(params,format='json'))
    try:
        with urlopen(Request(url,headers={'User-Agent':'AutoChoice model preparation/1.0'}),timeout=15) as response: rows=json.load(response)['Results']
        if vin:
            row=rows[0] if rows else {}; fields={k:row[k] for k in ('Make','Model','ModelYear','BodyClass','DriveType','FuelTypePrimary','DisplacementL','EngineCylinders') if row.get(k)}
            return dict(source='NHTSA vPIC',status=('partial' if row.get('ErrorCode')!='0' else 'matched') if fields else 'not_found',data=fields,note=row.get('ErrorText',''))
        matches=[{'make':r['Make_Name'],'model':r['Model_Name']} for r in rows if r['Model_Name'].casefold()==model.casefold() and r['Make_Name'].casefold()==make.casefold()]
        return dict(source='NHTSA vPIC',url=url,status='matched' if matches else 'not_found',matches=matches,requested_year=year)
    except (OSError,ValueError,KeyError) as e:
        return dict(source='NHTSA vPIC',status='unavailable',note=type(e).__name__)


def main():
    ap=argparse.ArgumentParser(description=__doc__); ap.add_argument('file',type=Path); ap.add_argument('--out',type=Path,required=True); ap.add_argument('--front',choices=['-Z','+Z','-X','+X'],default='-Z'); ap.add_argument('--vin'); ap.add_argument('--make'); ap.add_argument('--model'); ap.add_argument('--year',type=int); args=ap.parse_args()
    if args.vin:
        args.vin=args.vin.upper()
        if not re.fullmatch('[A-HJ-NPR-Z0-9]{17}',args.vin): ap.error('VIN must contain 17 VIN characters')
    if bool(args.make)!=bool(args.model) or (args.year and not (args.make or args.vin)): ap.error('Supply --make and --model together, or --vin')
    target=args.out/'car_parts.glb'; manifest=args.out/'parts.json'
    if target.exists() or manifest.exists(): ap.error('Output files already exist; choose a new directory')
    j,b=read_glb(args.file); pieces=collect(j,b,args.front); classify(pieces); parts=export(j,b,pieces)
    if not args.vin and not args.make:
        # Prefer explicit metadata. A short title/file stem is only a catalog
        # search hint; an exact catalog match does not identify the mesh.
        extras=j['asset'].get('extras',{}); extras=extras if isinstance(extras,dict) else {}
        hint=extras.get('title') or args.file.stem
        if all(isinstance(extras.get(k),str) and extras[k].strip() for k in ('make','model')):
            args.make,args.model=extras['make'],extras['model']; origin='asset.extras'
        else:
            words=str(hint).replace('_',' ').split(); origin='title_or_filename_hint'
            if 2<=len(words)<=5 and words[0].isalpha(): args.make,args.model=words[0],' '.join(words[1:])
    else: origin='user_input'
    vehicle=lookup(args.vin,args.make,args.model,args.year) if args.vin or args.make else {'status':'not_requested'}
    vehicle['query_origin']=origin
    args.out.mkdir(parents=True,exist_ok=True); write_glb(target,j,b); manifest.write_text(json.dumps(dict(source_file=args.file.name,front=args.front,up='+Y',vehicle=vehicle,parts=[dict(id=k,**v) for k,v in sorted(parts.items())]),ensure_ascii=False,indent=2),encoding='utf8'); print(f'{target}: {len(parts)-int("unclassified" in parts)} candidate parts; API: {vehicle["status"]}')


if __name__=='__main__':
    try: main()
    except (ValueError,KeyError,OSError,struct.error) as e: sys.exit(f'Cannot process model: {e}')
