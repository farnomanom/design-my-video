"""Quality checks on a Final Cut timeline (FCPXML) before export. Read-only: never changes the file.

  tools/envs/graphics/bin/python tools/qc/qc_timeline.py TIMELINE.fcpxml [REPORT.json]

Checks: structure (DTD, length, frame grid), media (every file present), edit (gaps, flash frames,
disabled items), captions (position, overlaps, row jumps, spelling variants, reading speed),
graphics (overlaps, spacing, density), transitions, audio (roles, gain above 0 dB), colour layer, fonts.
Progress goes to the Claude Code jobs band (~/.claude/progress)."""
import json
import os
import re
import sys
import urllib.parse
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, os.path.expanduser("~/.claude/progress"))
from dmv.edit import Doc  # noqa: E402
from dmv.markers import validate  # noqa: E402
from dmv.timecode import fcp_time  # noqa: E402

try:
    import bgjob
except ImportError:
    bgjob = None

CURSOR = "█"
MAX_LEN = Fraction(600)            # 10:00 brief limit
FLASH = 3                          # frames: shorter video pieces are flash frames
MIN_GAP_GRAPHICS = 2.0             # seconds between two graphics before it counts as a clash
GRAPHIC = re.compile(r"^0[1-8]_")


def fsec(t):
    return round(float(t), 2)


def tc(t):
    t = float(t)
    return f"{int(t // 60)}:{t % 60:05.2f}"


class Report:
    def __init__(self):
        self.sections = {}

    def add(self, name, status, summary, details=None):
        self.sections[name] = {"status": status, "summary": summary, "details": details or []}


def caption_parts(el):
    tx = el.find("text")
    if tx is None:
        return "", ""
    vis, hid, seen = [], [], False
    for r in tx.findall("text-style"):
        if (r.text or "") == CURSOR:
            seen = True
            continue
        (hid if seen else vis).append(r.text or "")
    return " ".join("".join(vis).split()), " ".join("".join(hid).split())


def check_structure(doc, path, rep):
    ok, msg = validate(path)
    dur = sum((i.dur for i in doc.primary()), Fraction(0))
    end = fcp_time(doc.seq.get("duration"))
    audio_only = lambda i: (doc.assets.get(i.el.get("ref")) is not None and doc.assets[i.el.get("ref")].get("hasVideo") != "1") or i.lane < 0
    off = [i for i in doc.items if i.kind != "transition" and i.parent.tag in ("spine",) and not audio_only(i)
           and (i.tl / doc.frame).denominator != 1]
    st = "pass" if ok and end <= MAX_LEN and not off else ("fail" if not ok else "warn")
    rep.add("Structure", st, f"DTD {'valid' if ok else 'INVALID'}; length {tc(end)} (limit 10:00); "
            f"{len(off)} items off the frame grid",
            ([msg[:300]] if not ok else []) + [f"off-grid: {i.el.get('name')} at {tc(i.tl)}" for i in off[:10]])
    return dur


def check_media(doc, rep):
    missing, n = [], 0
    for a in doc.assets.values():
        for mr in a.iter("media-rep"):
            n += 1
            src = mr.get("src") or ""
            p = urllib.parse.unquote(src[7:]) if src.startswith("file://") else src
            if p and not os.path.exists(p):
                missing.append(f"{a.get('name')}: {p}")
    rep.add("Media", "pass" if not missing else "fail", f"{n} media files referenced, {len(missing)} missing", missing[:20])


def check_edit(doc, rep):
    prim = doc.primary()
    gaps = [i for i in prim if i.kind == "gap"]
    flash = [i for i in doc.items if i.kind in ("asset-clip", "clip", "video", "ref-clip") and i.enabled
             and i.dur < FLASH * doc.frame and i.lane >= 0]
    disabled = [i for i in doc.items if i.el.get("enabled") == "0"]
    det = [f"gap {tc(g.tl)}–{tc(g.end)} ({fsec(g.dur)} s, black unless covered by a connected clip)" for g in gaps]
    det += [f"flash frame: {i.el.get('name') or i.kind} lane {i.lane} at {tc(i.tl)} ({round(i.dur / doc.frame)} f)" for i in flash]
    dis = Counter((i.el.get("name") or i.kind)[:40] for i in disabled)
    det += [f"disabled: {n} × {k}" for k, n in dis.most_common(12)]
    # gaps not covered by any enabled picture above them
    uncovered = []
    for g in gaps:
        cov = [i for i in doc.items if i.lane > 0 and i.enabled and i.kind in ("asset-clip", "clip", "video", "ref-clip")
               and i.tl <= g.tl and i.end >= g.end]
        if not cov:
            uncovered.append(g)
    def covered(x):
        return any(i.lane > 0 and i.enabled and i.kind in ("asset-clip", "clip", "video", "ref-clip") and i.tl <= x.tl
                   and i.end >= x.end for i in doc.items)
    visible_flash = [f for f in flash if not covered(f)]
    det = [d + ("" if not d.startswith("flash") else "") for d in det]
    det.append(f"{len(flash) - len(visible_flash)} of the short pieces are hidden under a full clip above them (not visible)")
    st = "warn" if (uncovered or visible_flash) else "pass"
    rep.add("Edit", st, f"{len(prim)} primary items; {len(gaps)} gaps ({len(uncovered)} not fully covered); "
            f"{len(visible_flash)} visible flash frames (<{FLASH} f; {len(flash)} short pieces in all); "
            f"{len(disabled)} disabled items", det[:40])


def check_captions(doc, rep):
    caps = sorted([i for i in doc.items if (i.el.get("name") or "").startswith("Word caption")], key=lambda i: i.tl)
    det = []
    pos = Counter()
    for c in caps:
        at = c.el.find("adjust-transform")
        pos[at.get("position") if at is not None else "none"] += 1
    if len(pos) > 1:
        det += [f"position {p}: {n} steps" for p, n in pos.items()]
    en = [c for c in caps if c.enabled]
    overl = [(a, b) for a, b in zip(en, en[1:]) if b.tl < a.end - doc.frame / 2]
    det += [f"overlap: {tc(b.tl)} '{caption_parts(b.el)[0][-40:]}' starts before the previous step ends ({tc(a.end)})"
            for a, b in overl[:15]]
    # rows: a step continues a row when its visible words extend the previous step's
    rows, cur = [], None
    for c in en:
        v = caption_parts(c.el)[0].split()
        prev = caption_parts(cur[-1].el)[0].split() if cur else None
        if cur and len(v) >= len(prev) and v[:len(prev)] == prev and c.tl <= cur[-1].end + doc.frame:
            cur.append(c)
        else:
            cur = [c]
            rows.append(cur)
    jumps = []
    for row in rows:
        target = caption_parts(row[-1].el)[0].split()
        for c in row:
            v, h = caption_parts(c.el)
            if (v + " " + h).split() != target and (v.split() + h.split()) != target:
                jumps.append(c)
    det += [f"row jump: {tc(c.tl)} shown '{caption_parts(c.el)[0][-30:]}' hidden '{caption_parts(c.el)[1][:30]}'" for c in jumps[:15]]
    # reading speed per finished row (characters per second while on screen)
    fast, longest = [], 0
    for row in rows:
        text = caption_parts(row[-1].el)[0]
        longest = max(longest, len(text))
        secs = float(row[-1].end - row[0].tl)
        if secs > 0 and len(text) / secs > 20:
            fast.append((row[0].tl, text, len(text) / secs))
    det += [f"fast row: {tc(t)} '{x[:50]}' {r:.0f} chars/s (broadcast guide ≈ 13–17)" for t, x, r in fast[:12]]
    # spelling variants: same word, different spelling (case/apostrophes/1-letter differences), from final rows
    words = Counter()
    for row in rows:
        for w in caption_parts(row[-1].el)[0].split():
            words[w.strip(".,!?\"“”‘’:;()")] += 1
    by_low = defaultdict(set)
    for w in words:
        if w:
            by_low[w.lower().replace("’", "'")].add(w)
    variants = [sorted(v) for k, v in by_low.items() if len(v) > 1 and not all(x[:1].isupper() != y[:1].isupper() and x[1:] == y[1:] for x in v for y in v if x != y)]
    caps_words = [w for w in words if len(w) > 3 and w.isupper()]
    near = []
    keys = sorted({w for w in words if len(w) >= 4 and w[:1].isupper()})
    for a in keys:
        for b in keys:
            if a < b and len(a) == len(b) - 1 and b.startswith(a) and b[-1] == a[-1]:
                near.append(f"{a} ({words[a]}) / {b} ({words[b]})")
    det += [f"spelling variants: {' / '.join(v)}" for v in variants[:12]] + [f"near-duplicate: {n}" for n in near[:12]]
    st = "fail" if (len(pos) > 1 or overl) else ("warn" if (jumps or fast or near) else "pass")
    rep.add("Captions", st, f"{len(caps)} steps ({len(caps) - len(en)} disabled) in {len(rows)} rows; positions "
            f"{dict(pos)}; {len(overl)} overlaps; {len(jumps)} row jumps; {len(fast)} rows faster than 20 chars/s; "
            f"longest row {longest} chars; {len(near)} near-duplicate spellings", det[:60])
    return rows


def graphic_kind(i):
    n = i.el.get("name") or ""
    if n.startswith("01_"):
        return "opening quote"
    if n.startswith("02_"):
        return "title card"
    if n.startswith("03_"):
        return "lower third"
    if n.startswith("04_"):
        return "song banner"
    if n.startswith("07_"):
        return "pull quote"
    if n.startswith("08_"):
        return "outro"
    if i.kind == "ref-clip":
        return "chapter card"
    return None


def check_graphics(doc, rep, rows):
    gs = [i for i in doc.items if i.enabled and graphic_kind(i)]
    # merge pieces of one graphic split by the user (same name, touching)
    merged = []
    for g in sorted(gs, key=lambda i: i.tl):
        n = g.el.get("name")
        if merged and merged[-1][0] == n and g.tl <= merged[-1][2] + Fraction(1, 2):
            merged[-1][2] = max(merged[-1][2], g.end)
        else:
            merged.append([n, g.tl, g.end, graphic_kind(g)])
    det, clashes = [], []
    for a, b in zip(merged, merged[1:]):
        gap = float(b[1] - a[2])
        if gap < MIN_GAP_GRAPHICS:
            clashes.append((a, b, gap))
            det.append(f"clash: {a[3]} '{a[0]}' ({tc(a[1])}–{tc(a[2])}) → {b[3]} '{b[0]}' at {tc(b[1])}: "
                       + (f"overlaps {-gap:.2f} s" if gap < 0 else f"only {gap:.2f} s apart"))
    # pull quote right after a chapter card with the same words
    for a, b in zip(merged, merged[1:]):
        if a[3] == "chapter card" and b[3] == "pull quote" and float(b[1] - a[2]) < 6:
            wa = set(re.findall(r"[a-z]+", (a[0] or "").lower())) - {"1", "2", "3"}
            wb = set(re.findall(r"[a-z]+", (b[0] or "").lower()))
            if len(wa & wb) >= 2:
                det.append(f"repeat: chapter '{a[0]}' then quote '{b[0]}' {float(b[1] - a[2]):.1f} s later (same words)")
    span = float(merged[-1][1] - merged[0][1]) if len(merged) > 1 else 0
    every = span / (len(merged) - 1) if len(merged) > 1 else 0
    # caption steps shown while a full-width quote / opening quote is on screen
    busy = []
    for n, a, b, k in merged:
        if k in ("pull quote", "opening quote", "title card", "chapter card"):
            on = [r for r in rows if r[0].tl < b and r[-1].end > a]
            if on:
                busy.append(f"{k} '{n}' {tc(a)}–{tc(b)} shares the screen with {len(on)} caption row(s)")
    det += busy[:20]
    det.append("order: " + " | ".join(f"{tc(a)} {k}" for _, a, _, k in merged))
    st = "warn" if clashes else "pass"
    rep.add("Graphics", st, f"{len(merged)} graphics; one every {every:.0f} s on average; {len(clashes)} closer than "
            f"{MIN_GAP_GRAPHICS:.0f} s or overlapping; {len(busy)} share the screen with captions", det)


def check_transitions(doc, rep):
    tr = [i for i in doc.items if i.kind == "transition"]
    by = Counter()
    det = []
    for t in tr:
        fx = t.el.find("filter-video")
        name = (fx.get("name") if fx is not None else None) or t.el.get("name") or "?"
        frames = round(t.dur / doc.frame)
        by[(name, frames)] += 1
    det += [f"{n} × {name} {f} f" for (name, f), n in sorted(by.items())]
    odd = [k for k in by if k[0] not in ("Cross Dissolve", "Fade To Color")]
    rep.add("Transitions", "warn" if odd else "pass", f"{len(tr)} transitions; types {sorted({k[0] for k in by})}", det)


def check_audio(doc, rep):
    roles = Counter()
    hot = []
    for i in doc.items:
        inner = [s.get("role") for s in i.el.findall("audio-role-source") + i.el.findall("audio-channel-source") if s.get("role")]
        r = inner[0] if inner else i.el.get("audioRole")
        if r and i.enabled:
            roles[r.split(".")[0]] += 1
        v = i.el.find("adjust-volume")
        if v is not None and i.enabled:
            amt = v.get("amount", "0dB")
            try:
                db = float(amt.replace("dB", ""))
            except ValueError:
                continue
            if db > 0:
                hot.append(f"{i.el.get('name')} at {tc(i.tl)}: {amt}")
    old = []   # roles are the user's choice; listed, not judged
    det = [f"role {r}: {n} enabled items" for r, n in roles.most_common()] + [f"gain above 0 dB: {h}" for h in hot[:15]]
    det.append("Loudness / true peak: NOT measured — waived by the user for this film (12 Nov 2026).")
    rep.add("Audio", "info", f"roles {dict(roles)}; {len(hot)} items with gain above 0 dB "
            "(user's levels, not changed); loudness check waived", det)


def check_colour(doc, rep):
    lut = [i for i in doc.items if (i.el.get("name") or "").lower().startswith(("look", "lut")) or "lut" in (i.el.get("name") or "").lower()]
    cc = sum(1 for i in doc.items for f in i.el.findall("filter-video") if "Color" in (f.get("name") or ""))
    det = []
    for l in lut:
        det.append(f"LUT layer {tc(l.tl)}–{tc(l.end)}, lane {l.lane}, enabled={l.el.get('enabled', '1')}")
    st = "info"
    rep.add("Colour", st, f"{cc} colour filters on shots; LUT look layer {'none' if not lut else ('off' if all(l.el.get('enabled') == '0' for l in lut) else 'on')} "
            "(look layers are a creative choice: listed, not judged)", det)


def check_fonts(doc, rep):
    fams = Counter()
    for d in doc.root.iter("text-style"):
        f = d.get("font")
        if f:
            fams[f] += 1
    for p in doc.root.iter("param"):
        if p.get("name", "").endswith("Font") and p.get("value") and not p.get("value").replace(".", "").isdigit():
            fams[p.get("value").split(" ")[0] + " (template)"] += 1
    try:
        import skia
        mgr = skia.FontMgr()
        have = {mgr.getFamilyName(i) for i in range(mgr.countFamilies())}
    except Exception:
        have = None
    det, miss = [], []
    for f, n in fams.most_common():
        base = f.replace(" (template)", "")
        ok = None if have is None or "(template)" in f else base in have
        if ok is False:
            miss.append(base)
        det.append(f"{f}: {n} uses" + ("" if ok is None else (" — installed" if ok else " — NOT INSTALLED")))
    rep.add("Fonts", "fail" if miss else "pass", f"{len(fams)} font families; {len(miss)} not installed on this Mac", det[:20])


def main():
    path = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    job = bgjob.Job("qc-" + re.sub(r"[^a-z0-9]+", "-", path.stem.lower())[:40], "QC checks: " + path.stem, "Design My Video") if bgjob else None
    steps = [("Reading timeline", None), ("Structure", check_structure), ("Media", check_media), ("Edit", check_edit),
             ("Captions", check_captions), ("Graphics", check_graphics), ("Transitions", check_transitions),
             ("Audio", check_audio), ("Colour", check_colour), ("Fonts", check_fonts)]
    rep = Report()
    doc, rows = None, []
    try:
        for k, (name, fn) in enumerate(steps):
            if job:
                job.update(round(100 * k / len(steps)), name, force=True)
            if fn is None:
                doc = Doc(path)
            elif fn is check_structure:
                fn(doc, path, rep)
            elif fn is check_captions:
                rows = fn(doc, rep)
            elif fn is check_graphics:
                fn(doc, rep, rows)
            else:
                fn(doc, rep)
    except Exception as e:
        if job:
            job.fail(str(e)[:120])
        raise
    if job:
        job.done(" · ".join(f"{k} {v['status']}" for k, v in rep.sections.items()))
    for k, v in rep.sections.items():
        print(f"[{v['status'].upper():4}] {k}: {v['summary']}")
    if out:
        out.write_text(json.dumps(rep.sections, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
