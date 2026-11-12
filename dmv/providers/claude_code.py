"""Claude through a locally installed, signed-in Claude Code (`claude -p`). Uses the person's Claude plan."""
import json
import os
import shutil
import subprocess
from pathlib import Path

NAME = "claude_code"
DEFAULT_MODEL = None  # Claude Code's own default model

CANDIDATES = ["~/.local/bin/claude", "~/.claude/local/claude", "/usr/local/bin/claude", "/opt/homebrew/bin/claude"]


def _binary():
    found = shutil.which("claude")
    if found:
        return found
    for c in CANDIDATES:
        p = Path(c).expanduser()
        if p.exists():
            return str(p)
    return None


def available():
    return _binary() is not None


def review(req):
    exe = _binary()
    if not exe:
        raise RuntimeError("Claude Code is not installed on this Mac.")
    folder = req["images"][0].parent if req["images"] else Path.cwd()
    listing = "\n".join(f"- {p}" for p in req["images"])
    prompt = (
        f"{req['system']}\n\n"
        + (f"First use the Read tool to look at every one of these contact sheet images:\n{listing}\n\n" if listing else "")
        + req["prompt"]
        + "\n\nReply with only the JSON object, no markdown fences or commentary. It must match this JSON Schema:\n"
        + json.dumps(req["schema"])
    )
    cmd = [exe, "-p", prompt, "--output-format", "json", "--allowedTools", "Read",
           "--add-dir", str(folder), "--max-turns", "60"]
    if req.get("model"):
        cmd += ["--model", req["model"]]
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_CODE_") and k != "CLAUDECODE"}
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(folder), env=env, timeout=1800)
    if r.returncode != 0:
        raise RuntimeError(f"Claude Code failed: {(r.stderr or r.stdout).strip()[:400]}")
    try:
        out = json.loads(r.stdout)
    except json.JSONDecodeError:
        return r.stdout
    if out.get("is_error"):
        raise RuntimeError(f"Claude Code error: {str(out.get('result'))[:400]}")
    return out.get("result", "")
