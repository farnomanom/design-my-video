"""Alternative reviewer: OpenAI Chat Completions over plain HTTPS (no extra packages)."""
import base64
import json
import urllib.error
import urllib.request

from ..keychain import get_key

NAME = "openai"
DEFAULT_MODEL = "gpt-5"  # change in Settings to any vision-capable model you have access to


def available():
    return bool(get_key("openai"))


def review(req):
    content = [{"type": "text", "text": req["prompt"] + "\n\nReturn only a JSON object matching this JSON Schema:\n"
                + json.dumps(req["schema"])}]
    for p in req["images"]:
        b64 = base64.b64encode(p.read_bytes()).decode()
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    body = {
        "model": req.get("model") or DEFAULT_MODEL,
        "messages": [{"role": "system", "content": req["system"]}, {"role": "user", "content": content}],
        "response_format": {"type": "json_object"},
    }
    r = urllib.request.Request("https://api.openai.com/v1/chat/completions", data=json.dumps(body).encode(),
                               headers={"Authorization": f"Bearer {get_key('openai')}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=900) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"OpenAI error {e.code}: {e.read().decode()[:300]}")
    return data["choices"][0]["message"]["content"]
