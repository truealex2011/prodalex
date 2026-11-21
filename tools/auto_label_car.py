"""Analyze GLB, optionally ask Ollama, and research vehicle data."""
import argparse,json,sys,importlib.util
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
def load_module(name, path):
    spec=importlib.util.spec_from_file_location(name, path); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod
local_model=load_module("local_model",Path(__file__).resolve().parents[1]/"app/local_model.py")
vehicle_research=load_module("vehicle_research",Path(__file__).resolve().parents[1]/"app/vehicle_research.py")
classify_part=local_model.classify_part; research=vehicle_research.research
from split_car import read_glb,collect,classify,write_glb

ap=argparse.ArgumentParser();ap.add_argument("file",type=Path);ap.add_argument("--out",type=Path,required=True);ap.add_argument("--front",default="-Z",choices=["-Z","+Z","+X","-X"]);ap.add_argument("--make");ap.add_argument("--model");ap.add_argument("--year",type=int);ap.add_argument("--vin");ap.add_argument("--ollama",action="store_true",help="compatibility alias; uses local PyTorch model");ap.add_argument("--local-model",action="store_true",help="classify with the local Transformers model");a=ap.parse_args()
if a.out.exists() and any(a.out.iterdir()):ap.error("output directory must be empty or not exist")
j,b=read_glb(a.file);pieces=collect(j,b,a.front);classify(pieces);examples=[json.loads(x) for x in (Path(__file__).with_name("part_examples.jsonl")).read_text(encoding="utf8").splitlines()];decisions=[]
for i,p in enumerate(pieces):
    feature={"position":[round(float(x),3) for x in p["c"]],"relative_size":[round(float(x),3) for x in p["d"]],"triangles":len(p["idx"]),"heuristic_label":p["part"]};decision=classify_part(feature,examples) if (a.ollama or a.local_model) else {"part_id":p["part"],"confidence":.35 if p["part"]=="unclassified" else .7,"reason":"geometry heuristic; local model not requested"};decisions.append({"candidate":i,"features":feature,"decision":decision})
vehicle=research(a.make,a.model,a.year,a.vin);a.out.mkdir(parents=True,exist_ok=True);write_glb(a.out/"car_parts.glb",j,b);(a.out/"vehicle_data.json").write_text(json.dumps(vehicle,ensure_ascii=False,indent=2),encoding="utf8");(a.out/"part_review.json").write_text(json.dumps({"source":a.file.name,"parts":decisions,"policy":"unknown instead of invented facts"},ensure_ascii=False,indent=2),encoding="utf8");(a.out/"review_report.md").write_text('# Review report\n\nGenerated without guessing. `unclassified` and `unknown` require human review.\n',encoding="utf8");print(a.out)
