"""Print machine-readable geometry features for local-model review."""
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from split_car import read_glb, collect, classify

ap=argparse.ArgumentParser(); ap.add_argument("file",type=Path); ap.add_argument("--front",default="-Z",choices=["-Z","+Z","+X","-X"]); ap.add_argument("--out",type=Path,required=True); a=ap.parse_args()
j,b=read_glb(a.file); pieces=collect(j,b,a.front); classify(pieces); out=[]
for i,p in enumerate(pieces): out.append({"candidate":i,"source_node":j["nodes"][p["node"]].get("name"),"position":[round(float(x),4) for x in p["c"]],"relative_size":[round(float(x),4) for x in p["d"]],"triangles":int(len(p["idx"])),"heuristic_label":p["part"]})
a.out.write_text(json.dumps({"file":a.file.name,"front":a.front,"candidates":out},ensure_ascii=False,indent=2),encoding="utf8"); print(a.out)
