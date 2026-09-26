"""Check the local model end to end and print exactly what fails.
    python tools/check_model.py
"""
import json, sys, time, urllib.request, urllib.error
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config

def call(body, label):
    t = time.time()
    req = urllib.request.Request(config.OLLAMA_URL + "/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        from james_core import egress
        with egress.urlopen(req, "local_model", timeout=120) as r:
            out = json.loads(r.read())
        print(f"[{label}] OK in {time.time()-t:.1f}s")
        print("   content :", repr(out["message"].get("content", ""))[:300])
        if out["message"].get("thinking"):
            print("   thinking:", repr(out["message"]["thinking"])[:200])
    except urllib.error.HTTPError as e:
        print(f"[{label}] HTTP {e.code} after {time.time()-t:.1f}s: {e.read().decode()[:300]}")
    except Exception as e:
        print(f"[{label}] {type(e).__name__} after {time.time()-t:.1f}s: {e}")

msgs = [{"role": "system", "content": "Reply with JSON only."},
        {"role": "user", "content": 'Pump 3 is vibrating more than usual. Return {"pump_number": int, "symptoms": [str]}'}]
base = {"model": config.CHAT_MODEL, "stream": False, "messages": msgs, "options": {"temperature": 0}}
print("model:", config.CHAT_MODEL)
call({**base, "format": "json", "think": False}, "json + think off")
call({**base, "format": "json"}, "json")
call({**base, "format": {"type": "object", "properties": {"pump_number": {"type": "integer"},
      "symptoms": {"type": "array", "items": {"type": "string"}}}, "required": ["pump_number", "symptoms"]},
      "think": False}, "schema + think off")
