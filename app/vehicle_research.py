"""Internet research with provenance. Missing data stays unknown."""
import json
from urllib.parse import quote
from urllib.request import Request,urlopen
def _get(url):
    with urlopen(Request(url,headers={"User-Agent":"AutoChoice-research/1.0"}),timeout=15) as r:return json.loads(r.read())
def research(make=None,model=None,year=None,vin=None):
    result={"status":"unknown","query":{"make":make,"model":model,"year":year,"vin":vin},"fields":{},"sources":[]}
    if vin:
        url="https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/"+quote(vin)+"?format=json"
        try:
            row=_get(url).get("Results",[{}])[0]; result["sources"].append({"name":"NHTSA vPIC","url":url}); mapping={"Make":"make","Model":"model","ModelYear":"year","BodyClass":"body_class","DriveType":"drive","FuelTypePrimary":"fuel","DisplacementL":"displacement_l","EngineCylinders":"engine_cylinders"}
            for source,key in mapping.items():
                if row.get(source):result["fields"][key]={"value":row[source],"status":"verified","confidence":.98,"source":url}
            result["status"]="verified" if result["fields"] else "unknown"
        except Exception as exc:result["error"]=type(exc).__name__
        return result
    if not make or not model:result["error"]="make_and_model_required";return result
    url="https://vpic.nhtsa.dot.gov/api/vehicles/GetModelsForMakeYear/make/"+quote(make)+("/modelyear/"+str(year) if year else "")+"?format=json"
    try:
        rows=_get(url).get("Results",[]); exact=[r for r in rows if r.get("Model_Name","").casefold()==model.casefold()];result["sources"].append({"name":"NHTSA vPIC","url":url});result["status"]="matched" if exact else "unknown";result["model_match"]=[{"make":r.get("Make_Name"),"model":r.get("Model_Name")} for r in exact]
    except Exception as exc:result["error"]=type(exc).__name__
    return result
