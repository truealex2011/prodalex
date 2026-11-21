"""Import license-cleared models from the supplied project index into AutoChoice."""
import json, shutil
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SOURCE=Path(r"C:\Users\1\Downloads\New ZIP Other File (VLC)(1)\projct")
MEDIA=ROOT/"app/static/media/models"; CATALOG=ROOT/"data/catalog/cars.json"
idx=json.loads((SOURCE/"index.json").read_text(encoding="utf-8-sig")); cars=json.loads(CATALOG.read_text(encoding="utf-8-sig")); existing={c["id"] for c in cars}; next_id=max(existing)+1
allowed=[]; blocked=[]
for item in idx:
    if item.get("license_class") != "ok": blocked.append({"title":item["title"],"uid":item["uid"],"license_class":item.get("license_class")}); continue
    uid=item["uid"]; src=SOURCE/"data/split"/uid; slug="project_"+uid[:8]; glb_name=slug+".glb"; parts_name=slug+".json"; shutil.copy2(src/"car_parts.glb",MEDIA/glb_name); shutil.copy2(src/"parts.json",MEDIA/parts_name)
    words=item["title"].replace("("," ").replace(")"," ").split(); brand=words[0] if words else "Other"; model=" ".join(words[1:]) or item["title"]
    if any(c.get("model_key")=="models/"+glb_name for c in cars): continue
    cars.append({"id":next_id,"brand":brand,"model":model,"trim":"Imported model","year":None,"body":"suv","seats":None,"trunk_l":None,"power_hp":None,"consumption_l100":None,"drive":None,"transmission":None,"fuel":None,"price":None,"colors":["gray"],"tags":[],"specs":{"moose_real":None,"crash_real":None},"listing_url":None,"image_key":None,"model_key":"models/"+glb_name,"parts_key":"models/"+parts_name,"model_credit":item.get("credit"),"hotspots":{}}); allowed.append({"id":next_id,"title":item["title"],"model_key":"models/"+glb_name,"parts_key":"models/"+parts_name});next_id+=1
CATALOG.write_text(json.dumps(cars,ensure_ascii=False,indent=1)+"\n",encoding="utf-8")
report=ROOT/"data/project_models_import.json"; report.write_text(json.dumps({"allowed":allowed,"not_published":blocked},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps({"added":allowed,"not_published":blocked},ensure_ascii=False,indent=2))
