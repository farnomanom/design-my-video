"""Alternative reviewer: a local vision model through Ollama (free, offline, lower quality)."""
import base64
import json
import urllib.error
import urllib.request

NAME = "ollama"
DEFAULT_MODEL = "qwen2.5vl"
URL = "http://localhost:11434"


def available():
    try:
        with urllib.request.urlopen(f"{URL}/api/tags", timeout=1.5):
            return True
    except Exception:
        return False


def review(req):
    body = {
        "model": req.get("model") or DEFAULT_MODEL,
        "stream": False,
        "format": req["schema"],
        "messages": [
            {"role": "system", "content": req["system"]},
            {"role": "user", "content": req["prompt"],
             "images": [base64.b64encode(p.read_bytes()).decode() for p in req["images"][:8]]},
        ],
    }
    r = urllib.request.Request(f"{URL}/api/chat", data=json.dumps(body).encode(),
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=3600) as resp:
            return json.loads(resp.read())["message"]["content"]
    except urllib.error.URLError as e:
        raise RuntimeError(f"Ollama error: {e}")
