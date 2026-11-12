"""Ask an AI reviewer to score the film out of 100 and write timecoded suggestions."""
import json
import re
from pathlib import Path

from . import providers
from .schema import Review, rubric_text

SYSTEM = (
    "You are a senior documentary editor and motion designer reviewing a finished edit of a music documentary. "
    "You judge look, sound, pacing and design, and you give practical, specific advice an editor can carry out "
    "in Final Cut Pro. Be honest: a polished but generic film should not score above 80. Ground every point in "
    "the evidence provided (contact-sheet frames, measurements, transcript, timeline). Never invent footage you "
    "cannot see."
)

PROMPT = """Review this film.

## Scoring rubric (100 points)
{rubric}

## How to read the evidence
- Contact sheets show one key frame per detected shot, labelled #shot HH:MM:SS:FF (shot length).
- The digest below has measured pacing, colour, loudness, music sections, beat sync, the title list and the transcript.
- Colour numbers: luma 0..1, warmth = mean(R) - mean(B), saturation 0..1. A jump score above 3 between shots is visible.
- Loudness target for online delivery is about -14 LUFS integrated with peaks at or below -1 dBFS.

## What to return
- overall_score: the sum of the category scores.
- categories: one entry per rubric category with score, max and a short rationale.
- tone_and_mood: how the film feels now.
- strengths: what already works and should be kept.
- suggestions: {n_min} to {n_max} specific, timecoded design improvements (start/end in seconds on the film's timeline),
  ordered by impact. Focus on design elements that make it look cleaner, flow better and feel more aesthetic:
  titles and typography, lower thirds, colour matching, transitions, overlays/textures, music and audio polish,
  pacing trims. For each, give how_in_final_cut steps and the closest automatic action type.

## Digest
{digest}
"""


def _extract_json(text):
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        return fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if start >= 0 else text


def build_request(analysis_dir, model=None, max_images=24):
    d = Path(analysis_dir)
    digest = (d / "digest.md").read_text()
    sheets = sorted((d / "sheets").glob("sheet_*.jpg")) if (d / "sheets").exists() else []
    if len(sheets) > max_images:
        step = len(sheets) / max_images
        sheets = [sheets[int(i * step)] for i in range(max_images)]
    return {
        "system": SYSTEM,
        "prompt": PROMPT.format(rubric=rubric_text(), digest=digest, n_min=8, n_max=20),
        "images": sheets,
        "model": model,
        "schema": Review.model_json_schema(),
    }


def run(analysis_dir, provider="auto", model=None, task=None):
    prov = providers.pick(provider)
    if task:
        task.update(status="running", percent=10, note=f"Reviewing with {providers.LABELS[prov.NAME]}…", force=True)
    req = build_request(analysis_dir, model)
    raw = prov.review(req)
    try:
        review = Review.model_validate_json(_extract_json(raw))
    except Exception as e:
        bad = Path(analysis_dir) / "review_raw.txt"
        bad.write_text(raw)
        raise RuntimeError(f"The reviewer's answer was not in the expected format ({e.__class__.__name__}). Raw reply saved to {bad}.")
    review.overall_score = max(0, min(100, sum(c.score for c in review.categories)))
    out = review.model_dump()
    out["provider"] = prov.NAME
    out["providerLabel"] = providers.LABELS[prov.NAME]
    out["model"] = model or prov.DEFAULT_MODEL
    (Path(analysis_dir) / "review.json").write_text(json.dumps(out, indent=1))
    return out
