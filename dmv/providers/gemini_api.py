"""Alternative reviewer: Google Gemini generateContent over plain HTTPS (no extra packages)."""
import base64
import json
import urllib.error
import urllib.request

from ..keychain import get_key

NAME = "gemini"
DEFAULT_MODEL = "gemini-2.5-pro"  # change in Settings to any vision-capable Gemini model


def available():
    return bool(get_key("gemini"))


def review(req):
    parts = [{"text": req["prompt"] + "\n\nReturn only a JSON object matching this JSON Schema:\n" + json.dumps(req["schema"])}]
    for p in req["images"]:
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(p.read_bytes()).decode()}})
    body = {
        "system_instruction": {"parts": [{"text": req["system"]}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"responseMimeType": "application/json"},
    }
    model = req.get("model") or DEFAULT_MODEL
    r = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        data=json.dumps(body).encode(),
        headers={"x-goog-api-key": get_key("gemini"), "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(r, timeout=900) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Gemini error {e.code}: {e.read().decode()[:300]}")
    return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
