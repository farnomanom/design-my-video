"""AI reviewer providers. Each module exposes NAME, DEFAULT_MODEL, available() and review(request) -> str (JSON text).

request = {"system": str, "prompt": str, "images": [Path, ...], "model": str|None, "schema": dict}
Claude is the default; the rest are interchangeable alternatives.
"""
from . import anthropic_api, claude_code, gemini_api, ollama_local, openai_api

ALL = {m.NAME: m for m in (claude_code, anthropic_api, openai_api, gemini_api, ollama_local)}
ORDER = ["claude_code", "anthropic", "openai", "gemini", "ollama"]
LABELS = {
    "claude_code": "Claude (via Claude Code)",
    "anthropic": "Claude (Anthropic API key)",
    "openai": "OpenAI",
    "gemini": "Google Gemini",
    "ollama": "Local model (Ollama)",
}


def pick(preferred="auto"):
    if preferred and preferred != "auto":
        if preferred not in ALL:
            raise ValueError(f"Unknown provider {preferred}. Choose from {', '.join(ORDER)}")
        return ALL[preferred]
    for name in ORDER:
        if ALL[name].available():
            return ALL[name]
    raise RuntimeError(
        "No AI reviewer available. Install and sign in to Claude Code, or add an Anthropic, OpenAI or Gemini "
        "API key in Settings, or run Ollama with a vision model."
    )


def status():
    return {name: {"label": LABELS[name], "available": ALL[name].available(), "defaultModel": ALL[name].DEFAULT_MODEL}
            for name in ORDER}
