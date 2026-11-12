"""Claude through the Anthropic API (official SDK), with schema-validated structured output."""
import base64

from ..keychain import get_key

NAME = "anthropic"
DEFAULT_MODEL = "claude-opus-5-5"


def available():
    import importlib.util

    # find_spec avoids importing the SDK (slow on older Macs) just to check it's installed.
    return importlib.util.find_spec("anthropic") is not None and bool(get_key("anthropic"))


def review(req):
    import anthropic

    from ..schema import Review

    client = anthropic.Anthropic(api_key=get_key("anthropic"))
    content = []
    for p in req["images"]:
        content.append({"type": "text", "text": f"Contact sheet: {p.name}"})
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                    "data": base64.standard_b64encode(p.read_bytes()).decode()}})
    content.append({"type": "text", "text": req["prompt"]})
    try:
        with client.messages.stream(
            model=req.get("model") or DEFAULT_MODEL,
            max_tokens=32000,
            system=req["system"],
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            output_format=Review,
            messages=[{"role": "user", "content": content}],
        ) as stream:
            msg = stream.get_final_message()
    except anthropic.AuthenticationError:
        raise RuntimeError("The Anthropic API key was rejected. Update it in Settings.")
    except anthropic.RateLimitError:
        raise RuntimeError("Anthropic rate limit reached. Try again in a minute.")
    except anthropic.APIConnectionError:
        raise RuntimeError("Could not reach the Anthropic API. Check the internet connection.")
    if msg.stop_reason == "refusal":
        raise RuntimeError("Claude declined this request.")
    if msg.stop_reason == "max_tokens":
        raise RuntimeError("The review was cut off (too long). Try again.")
    return "".join(b.text for b in msg.content if b.type == "text")
