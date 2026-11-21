"""Small local Transformers/PyTorch adapter. The model may classify candidates, never facts."""
import json, os
from urllib.request import Request, urlopen

_PIPELINE = None
_PIPELINE_ERROR = None

def classify_part(features, examples=None, model=None, base_url=None):
    model=model or os.getenv("LOCAL_MODEL", "Qwen/Qwen2.5-0.5B-Instruct")
    allowed=["hood","trunk_lid","front_bumper","rear_bumper","roof","windshield","rear_window","mirror_left","mirror_right","door_front_left","door_front_right","door_rear_left","door_rear_right","wheel_front_left","wheel_front_right","wheel_rear_left","wheel_rear_right","body_paint","interior","unclassified"]
    prompt={"instruction":"Classify one car geometry candidate. Return JSON only.","allowed_part_ids":allowed,"rules":["If uncertain use unclassified","confidence is 0..1","do not identify make/model"],"examples":examples or [],"candidate":features,"schema":{"part_id":"unclassified","confidence":0.0,"reason":""}}
    try:
        global _PIPELINE, _PIPELINE_ERROR
        if _PIPELINE is None and _PIPELINE_ERROR is None:
            from transformers import pipeline
            _PIPELINE = pipeline("text-generation", model=model, device=-1)
        if _PIPELINE_ERROR is not None: raise _PIPELINE_ERROR
        text=json.dumps(prompt,ensure_ascii=False)
        answer=_PIPELINE(text, max_new_tokens=120, do_sample=False, return_full_text=False)[0]["generated_text"]
        start=answer.find("{"); end=answer.rfind("}")
        value=json.loads(answer[start:end+1]) if start>=0 and end>start else {}
        part=value.get("part_id") if value.get("part_id") in allowed else "unclassified"; confidence=max(0,min(1,float(value.get("confidence",0))))
        return {"part_id":part,"confidence":confidence,"reason":str(value.get("reason",""))[:500],"model":model}
    except Exception as exc: return {"part_id":"unclassified","confidence":0.0,"reason":"Local Transformers unavailable: "+type(exc).__name__,"model":model}
