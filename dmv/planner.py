"""Turn approved review suggestions, or a typed instruction, into a concrete edit plan.

The AI only chooses edits from a fixed menu and names real timeline items by id; the edits
themselves are carried out by dmv.edit, deterministically, and checked before anything is written.
"""
import copy
import json
from pathlib import Path

from . import edit, providers
from .review import _extract_json
from .schema import EditPlan

SYSTEM = (
    "You are an assistant editor working inside Final Cut Pro for a senior editor. You turn design notes into "
    "precise timeline edits chosen from a fixed menu. You only touch the items the notes are about, you never "
    "invent item ids, and you prefer the smallest change that achieves the note. When something cannot be done "
    "with the menu, you say so in not_possible instead of forcing it."
)

MENU = """## Edit menu (fields not listed are left empty)
- set_volume: targets (clips with audio), gain_db (relative change, + or -).
- duck: lower music under a range. targets (the music/ambience clips), gain_db (dB to drop, e.g. 6–10),
  start_seconds/end_seconds (timeline range), under_speech=true to dip only where people speak in that range,
  ramp_seconds (fade into and out of each dip, default 0.4).
- fade_audio: targets, fade_in_seconds and/or fade_out_seconds.
- add_transition: targets (clip ids on a storyline: items marked primary or inSpine), position "start" (edit before the clip)
  or "end" (edit after it), duration_seconds, template = an existing transition item id or name already used in the
  film (preferred, keeps the look consistent) or "Cross Dissolve".
- remove_transition: targets (transition ids).
- add_title: text, start_seconds, duration_seconds, template = id of an existing title whose look to reuse (preferred),
  optional lane. Leave template empty only for a plain Basic Title.
- restyle_title: targets (title ids), template = title id whose font/colour/size to copy, and/or font, font_size,
  font_color (#RRGGBB); copy_position=true also copies the template's position and scale.
- copy_grade: template = clip id whose colour correction to copy, targets = clips to match to it.
- trim: targets (one storyline clip), trim_head_seconds and/or trim_tail_seconds (positive = shorter). Later clips
  in that storyline move up. Use sparingly and only when the note asks for tighter pacing.
- duplicate: template = a connected item (overlay, title, sound effect) to copy, start_seconds, optional duration_seconds, lane.
- set_enabled: targets, enabled true/false (disable to hide without deleting).
- add_marker: start_seconds, text. Use for anything that needs the editor's own judgement.
- cut_range: start_seconds, end_seconds. Removes that section of the film and closes the gap (ripple delete):
  picture, sound, captions and overlays after it all move up together. Use for tightening, removing a sentence,
  a false start or a filler. Cut on word boundaries from the transcript, never mid-word; keep breaths natural.
- move_range: start_seconds, end_seconds, to_seconds. Moves that section so it starts where to_seconds was
  (to_seconds must be outside the section). Use for re-ordering scenes or answers. Move whole sentences/scenes.
- swap_take: targets (one clip id), source_start_seconds (new in-point in that clip's source file, seconds),
  optional source (another media file name). Keeps the clip's place and length but uses a different moment/take.
  Only use in-points that appear in the alternative takes list.
All times in one plan refer to the timeline as it is now, even when several cuts or moves are combined.

Times are seconds on the film's timeline. Item ids come from the timeline list below. Roles in this file may be
mislabelled (music tagged as dialogue); judge music by name, lane (negative lanes are audio below the picture),
audioOnly and duration.
"""

PROMPT = """{task}

{menu}

## Film
Project “{project}”, {duration:.1f}s, {fps:g} fps.

## Review suggestions{which}
{suggestions}

## Speech (transcript or captions, seconds)
{speech}

## Timeline items
id kind "name" start–end lane [flags]
{items}
{takes}

Return an edit plan. Each op's note should be understandable without the timeline in front of you.
"""


def _items_text(inv):
    lines = []
    for r in inv:
        flags = []
        for k in ("primary", "inSpine", "disabled", "audioOnly", "retimed"):
            if r.get(k):
                flags.append(k)
        for k in ("role", "effect", "volume"):
            if r.get(k):
                flags.append(f"{k}={r[k]}")
        if r.get("filters"):
            flags.append("filters=" + ",".join(r["filters"]))
        if r.get("text"):
            flags.append(f'text="{r["text"]}"')
        if r.get("src"):
            flags.append(f'src="{r["src"][0]}"@{r["src"][1]:.2f}s')
        lines.append(f'{r["id"]} {r["kind"]} "{(r.get("name") or "")[:50]}" {r["start"]:.2f}–{r["end"]:.2f} '
                     f'lane {r["lane"]} {" ".join(flags)}')
    return "\n".join(lines)


TAKE_WORDS = ("take", "alternative", "alt ", "delivery", "retake", "better version", "different version", "best version")


def _src_info(doc):
    """Source file and in-point for clips, so the planner can talk about takes."""
    out = {}
    for it in doc.items:
        el = it.el
        ref = el.get("ref")
        if el.tag == "clip":
            inner = next((c for c in el if c.tag in ("video", "asset-clip") and c.get("lane") in (None, "0")), None)
            if inner is None:
                continue
            ref = inner.get("ref")
            a = doc.assets.get(ref)
            if a is None:
                continue
            local = it.local_start - edit.fcp_time(inner.get("offset") or "0s") + edit.fcp_time(inner.get("start") or "0s")
        elif el.tag == "asset-clip":
            a = doc.assets.get(ref)
            if a is None:
                continue
            local = it.local_start
        else:
            continue
        mr = a.find("media-rep")
        name = Path(mr.get("src")).name if mr is not None and mr.get("src") else a.get("name")
        out[it.id] = (name.replace("%20", " "), float(local - edit.fcp_time(a.get("start") or "0s")))
    return out


def alternative_takes(doc, limit=400):
    """Transcript of the source files used in the timeline, from a media log if one exists."""
    import sqlite3
    import urllib.parse
    files = set()
    for a in doc.assets.values():
        mr = a.find("media-rep")
        if mr is not None and mr.get("src"):
            files.add(Path(urllib.parse.unquote(mr.get("src").replace("file://", ""))).name)
    logs = Path.home() / "Movies" / "Design My Video" / "Media Logs"
    lines = []
    for dbp in sorted(logs.glob("*/medialog.db")) if logs.exists() else []:
        try:
            con = sqlite3.connect(str(dbp))
            clips = {cid: name for cid, name in con.execute("select id, name from clips")}
            used = {cid: n for cid, n in clips.items() if n in files}
            for cid, n in used.items():
                for st, en, tx in con.execute("select start, end, text from segments where clip_id=? order by start", (cid,)):
                    lines.append(f"{n} {st:.1f}–{en:.1f}s: {tx[:90]}")
        except Exception:
            continue
    return lines[:limit]


def speech_segments(analysis_dir):
    a = json.loads((Path(analysis_dir) / "analysis.json").read_text())
    segs = ((a.get("transcript") or {}).get("segments")) or []
    if segs:
        return [(s["start"], s["end"], s.get("text", "")) for s in segs]
    caps = (a.get("timeline") or {}).get("captions") or []
    return [(c["start"], c["start"] + c["duration"], c.get("text", "")) for c in caps]


def build_request(doc, analysis_dir, suggestions=None, command=None, model=None):
    review = {}
    rp = Path(analysis_dir) / "review.json"
    if rp.exists():
        review = json.loads(rp.read_text())
    all_s = review.get("suggestions", [])
    chosen = suggestions if suggestions is not None else []
    if command:
        task = ("## The editor's instruction\n" + command.strip() +
                "\n\nCarry out this instruction. Use the review suggestions only as background.")
        shown, which = all_s, " (background)"
    else:
        task = "## Task\nCarry out each of the approved review suggestions below as timeline edits."
        shown, which = chosen, " (approved)"
    sug = "\n".join(
        f'- {s["id"]} [{s["category"]}] {s["start_seconds"]:.1f}–{s["end_seconds"]:.1f}s: {s["suggestion"]} '
        f'(issue: {s["issue"]}; suggested action: {s["action"]})' for s in shown) or "(none)"
    speech = "\n".join(f"{a:.1f}–{b:.1f} {t[:70]}" for a, b, t in speech_segments(analysis_dir)[:400]) or "(none)"
    vbs = sorted(Path(analysis_dir).glob("verbatim_*.json"), key=lambda p: p.stat().st_mtime)
    if vbs:   # word-for-word timings (made by Remove filler words) let cuts land exactly between words
        words = json.loads(vbs[-1].read_text()).get("words") or []
        speech = ("(word start times in seconds; cut between words)\n" +
                  "\n".join(" ".join(f"{w['start']:.2f}:{w['word']}" for w in words[i:i + 14]) for i in range(0, len(words), 14)))
    fps = float(1 / doc.frame) if doc.frame else 25
    inv = doc.inventory()
    words = [r for r in inv if r["kind"] == "title" and (r.get("name") or "").startswith("Word caption")]
    inv = [r for r in inv if r not in words]
    if words:
        inv.append({"id": "-", "kind": "note", "name": f"{len(words)} word-by-word caption titles "
                    f"({words[0]['start']:.0f}–{words[-1]['end']:.0f}s) not listed; cuts and moves carry them along",
                    "start": words[0]["start"], "end": words[-1]["end"], "lane": words[0]["lane"]})
    src = _src_info(doc)
    for r in inv:
        if r["id"] in src:
            r["src"] = src[r["id"]]
    takes = ""
    if command and any(w in command.lower() for w in TAKE_WORDS):
        alt = alternative_takes(doc)
        takes = ("\n## Alternative takes (source file transcript: file start–end in that file)\n" + "\n".join(alt)) if alt else \
            "\n## Alternative takes\n(none logged: build a media log of the source footage first to choose between takes)"
    prompt = PROMPT.format(task=task, menu=MENU, project=doc.project.get("name"),
                           duration=float(edit.fcp_time(doc.seq.get("duration"))), fps=fps,
                           which=which, suggestions=sug, speech=speech, items=_items_text(inv), takes=takes)
    return {"system": SYSTEM, "prompt": prompt, "images": [], "model": model, "schema": EditPlan.model_json_schema()}


def plan(analysis_dir, base_path, suggestions=None, command=None, provider="auto", model=None, task=None):
    prov = providers.pick(provider)
    if task:
        task.update(status="running", percent=15, note=f"Planning edits with {providers.LABELS[prov.NAME]}…", force=True)
    doc = edit.Doc(base_path)
    raw = prov.review(build_request(doc, analysis_dir, suggestions, command, model))
    try:
        p = EditPlan.model_validate_json(_extract_json(raw))
    except Exception as e:
        bad = Path(analysis_dir) / "plan_raw.txt"
        bad.write_text(raw)
        raise RuntimeError(f"The planner's answer was not in the expected format ({e.__class__.__name__}). Raw reply saved to {bad}.")
    out = p.model_dump()
    # Dry run on a throwaway copy so the editor sees which edits will work before approving.
    if task:
        task.update(percent=85, note="Checking each edit against the timeline…", force=True)
    trial = edit.Doc(base_path)
    checks = edit.apply_ops(trial, out["ops"], [(a, b) for a, b, _ in speech_segments(analysis_dir)])
    for op, c in zip(out["ops"], checks):
        op["check"] = c["status"]
        op["detail"] = c["detail"]
    out.update(base=str(edit.resolve_path(base_path)), baseSha=edit.sha1(edit.resolve_path(base_path)),
               command=command, suggestionIds=[s["id"] for s in suggestions or []],
               provider=prov.NAME, providerLabel=providers.LABELS[prov.NAME])
    (Path(analysis_dir) / "plan.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    return out
