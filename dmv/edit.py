"""Apply an approved edit plan to a copy of a Final Cut Pro timeline (FCPXML).

The original file is never changed. Each apply writes a new numbered version
("<Project> – v2.fcpxml", "– v3", …) that Final Cut imports as a new project.

Every timeline item gets a stable id (i0001, i0002, … in document order), so a plan made
against one file applies to exactly the same items in that file. Plans record the file's
SHA-1 and refuse to apply to a different file.
"""
import copy
import hashlib
import re
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

from .fcpxml import TIMED, resolve_path
from .timecode import fcp_time

CLIP_KINDS = {"asset-clip", "clip", "video", "audio", "ref-clip", "sync-clip", "mc-clip", "title", "gap"}
AUDIO_KINDS = {"asset-clip", "clip", "audio", "ref-clip", "sync-clip", "mc-clip"}
ANCHOR_TAGS = {"audio", "video", "clip", "title", "caption", "mc-clip", "ref-clip", "sync-clip", "asset-clip",
               "audition", "spine", "live-drawing", "gap", "transition"}
MARKER_TAGS = {"marker", "chapter-marker", "rating", "keyword", "analysis-marker"}
COLOUR_WORDS = ("color", "colour", "lut", "hue/saturation", "exposure", "white balance", "balance")
CROSS_DISSOLVE_UID = "FxPlug:4731E73A-8DAC-4113-9A30-AE85B1761265"
AUDIO_CROSSFADE_UID = "FFAudioTransition"
BASIC_TITLE_UID = ".../Titles.localized/Bumper:Opener.localized/Basic Title.localized/Basic Title.moti"


def _rank(tag):
    """Position of a child element inside a clip, following Final Cut's DTD order."""
    if tag == "param":
        return -3
    if tag == "text":
        return -2
    if tag == "text-style-def":
        return -1
    if tag == "note":
        return 0
    if tag == "conform-rate":
        return 1
    if tag == "timeMap":
        return 2
    if tag == "adjust-volume":
        return 4
    if tag == "adjust-panner":
        return 5
    if tag.startswith("adjust-") or tag == "object-tracker":
        return 3
    if tag == "mc-source":
        return 5.5
    if tag in ANCHOR_TAGS:
        return 6
    if tag in MARKER_TAGS:
        return 7
    if tag in ("audio-channel-source", "audio-role-source", "sync-source"):
        return 8
    if tag in ("filter-video", "filter-video-mask"):
        return 9
    if tag == "filter-audio":
        return 10
    if tag == "metadata":
        return 11
    return 12


def insert_ordered(parent, child):
    r = _rank(child.tag)
    idx = 0
    for i, k in enumerate(list(parent)):
        if _rank(k.tag) <= r:
            idx = i + 1
    parent.insert(idx, child)


def fmt(t):
    t = Fraction(t)
    return f"{t.numerator}s" if t.denominator == 1 else f"{t.numerator}/{t.denominator}s"


def db(s):
    try:
        return float(str(s).replace("dB", "").strip() or 0)
    except ValueError:
        return 0.0


def fmt_db(v):
    return f"{max(-96.0, min(12.0, v)):.2f}".rstrip("0").rstrip(".") + "dB"


def sha1(path):
    return hashlib.sha1(Path(path).read_bytes()).hexdigest()


class Item:
    __slots__ = ("id", "el", "parent", "kind", "tl", "dur", "lane", "enabled", "local_start", "host")

    def to_local(self, t):
        return self.local_start + (Fraction(t) - self.tl)

    @property
    def end(self):
        return self.tl + self.dur


class Doc:
    def __init__(self, path):
        self.src = resolve_path(path)
        self.tree = ET.parse(self.src)
        self.root = self.tree.getroot()
        self.resources = self.root.find("resources")
        self.formats = {f.get("id"): f for f in self.root.iter("format")}
        self.effects = {e.get("id"): e for e in self.resources.findall("effect")}
        self.assets = {a.get("id"): a for a in self.resources.findall("asset")}
        self.project = next(self.root.iter("project"))
        self.seq = self.project.find("sequence")
        fd = self.formats.get(self.seq.get("format"))
        self.frame = fcp_time(fd.get("frameDuration")) if fd is not None and fd.get("frameDuration") else Fraction(1, 25)
        self.spine = self.seq.find("spine")
        self.items, self.by_id, self.by_el = [], {}, {}
        self._walk(self.spine, Fraction(0), Fraction(0), 0, True, None)

    # ---- index -------------------------------------------------------------------------------
    def _walk(self, container, cont_tl, cont_start, cont_lane, enabled, host):
        for child in list(container):
            if child.tag not in TIMED:
                continue
            tl = cont_tl + fcp_time(child.get("offset")) - cont_start
            lane = cont_lane + int(child.get("lane")) if child.get("lane") else cont_lane
            en = enabled and child.get("enabled", "1") != "0"
            if child.tag == "spine":
                self._walk(child, tl, Fraction(0), lane, en, host)
                continue
            it = Item()
            it.id = f"i{len(self.items) + 1:04d}"
            it.el, it.parent, it.kind = child, container, child.tag
            it.tl, it.dur, it.lane, it.enabled = tl, fcp_time(child.get("duration")), lane, en
            it.local_start = fcp_time(child.get("start"))
            it.host = host
            self.items.append(it)
            self.by_id[it.id] = it
            self.by_el[child] = it
            self._walk(child, tl, it.local_start, lane, en, it)

    def snap(self, t):
        return round(Fraction(t) / self.frame) * self.frame

    def primary(self):
        return [i for i in self.items if i.parent is self.spine and i.kind != "transition"]

    def host_at(self, t):
        t = Fraction(t)
        for i in self.primary():
            if i.tl <= t < i.end:
                return i
        return None

    def item(self, iid):
        it = self.by_id.get(iid)
        if it is None:
            raise EditError(f"item {iid} not found in this timeline")
        return it

    def text_of(self, it):
        blocks = ["".join(ts.text or "" for ts in t.iter("text-style")) for t in it.el.findall("text")]
        return " ".join(" ".join(b.split()) for b in blocks if b.strip())

    def role(self, it):
        r = it.el.get("audioRole")
        if r:
            return r
        a = self.assets.get(it.el.get("ref"))
        if a is not None and a.get("hasAudio") == "1" and a.get("hasVideo") != "1":
            return "music?" if it.lane < 0 else "audio"
        return None

    def has_audio(self, it):
        if it.kind not in AUDIO_KINDS:
            return False
        a = self.assets.get(it.el.get("ref"))
        return a is None or a.get("hasAudio") == "1"

    def effect_name(self, it):
        e = self.effects.get(it.el.get("ref"))
        return e.get("name") if e is not None else None

    def next_id(self):
        ids = [int(m.group(1)) for el in self.root.iter() for m in [re.match(r"r(\d+)$", el.get("id") or "")] if m]
        return f"r{max(ids, default=0) + 1}"

    def ensure_effect(self, name, uid):
        for e in self.resources.findall("effect"):
            if e.get("uid") == uid or e.get("name") == name:
                return e.get("id")
        rid = self.next_id()
        el = ET.Element("effect", {"id": rid, "name": name, "uid": uid})
        self.resources.append(el)
        self.effects[rid] = el
        return rid

    def unique_style_ids(self, el):
        """Give a cloned title fresh text-style-def ids so they don't clash with the original."""
        used = {e.get("id") for e in self.root.iter("text-style-def")}
        n = 1
        mapping = {}
        for d in el.iter("text-style-def"):
            while f"ts{n}" in used:
                n += 1
            mapping[d.get("id")] = f"ts{n}"
            used.add(f"ts{n}")
            d.set("id", f"ts{n}")
        for ts in el.iter("text-style"):
            if ts.get("ref") in mapping:
                ts.set("ref", mapping[ts.get("ref")])

    # ---- inventory for the planner -----------------------------------------------------------
    def inventory(self, include_captions=False):
        rows = []
        for it in self.items:
            if it.kind == "caption" and not include_captions:
                continue
            row = {"id": it.id, "kind": it.kind, "name": it.el.get("name"), "start": round(float(it.tl), 2),
                   "end": round(float(it.end), 2), "lane": it.lane}
            if not it.enabled:
                row["disabled"] = True
            if it.parent is self.spine:
                row["primary"] = True
            elif it.parent.tag == "spine":
                row["inSpine"] = True
            if it.kind == "title":
                row["text"] = self.text_of(it)[:80]
                row["effect"] = self.effect_name(it)
            elif it.kind == "video":
                row["effect"] = self.effect_name(it)
            r = self.role(it)
            if r:
                row["role"] = r
            a = self.assets.get(it.el.get("ref"))
            if a is not None and a.get("hasVideo") != "1" and a.get("hasAudio") == "1":
                row["audioOnly"] = True
            v = it.el.find("adjust-volume")
            if v is not None:
                row["volume"] = v.get("amount", "0dB") + (" (keyframed)" if v.find(".//keyframe") is not None else "")
            fx = [f.get("name") for f in it.el.findall("filter-video")]
            if fx:
                row["filters"] = fx
            if it.el.find("timeMap") is not None:
                row["retimed"] = True
            rows.append(row)
        return rows

    # ---- output ------------------------------------------------------------------------------
    def write(self, out_path, project_name, keep_identity=False):
        """New project by default: new name, no uid, so Final Cut imports it alongside the original."""
        self.project.set("name", project_name)
        if not keep_identity:
            for attr in ("uid", "modDate", "id"):
                self.project.attrib.pop(attr, None)
        ET.indent(self.tree, space="    ")
        xml = ET.tostring(self.root, encoding="unicode")
        Path(out_path).write_text('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n\n' + xml + "\n")


class EditError(Exception):
    pass


# ---- volume helpers ----------------------------------------------------------------------------

def _volume(el, create=True):
    v = el.find("adjust-volume")
    if v is None and create:
        v = ET.Element("adjust-volume")
        insert_ordered(el, v)
    return v


def _amount_param(v, create=True):
    p = next((p for p in v.findall("param") if p.get("name") == "amount"), None)
    if p is None and create:
        p = ET.SubElement(v, "param", {"name": "amount"})
    return p


def _curve(v):
    """Existing volume as (base_db, [(local_time, db), …])."""
    base = db(v.get("amount", "0dB")) if v is not None else 0.0
    kfs = []
    if v is not None:
        p = _amount_param(v, create=False)
        if p is not None:
            for k in p.iter("keyframe"):
                kfs.append((fcp_time(k.get("time")), db(k.get("value"))))
    kfs.sort()
    return base, kfs


def _eval(base, kfs, t):
    if not kfs:
        return base
    if t <= kfs[0][0]:
        return kfs[0][1]
    for (t0, v0), (t1, v1) in zip(kfs, kfs[1:]):
        if t0 <= t <= t1:
            return v0 if t1 == t0 else v0 + (v1 - v0) * float((t - t0) / (t1 - t0))
    return kfs[-1][1]


def _write_keyframes(v, points):
    p = _amount_param(v)
    old = p.find("keyframeAnimation")
    if old is not None:
        p.remove(old)
    ka = ET.SubElement(p, "keyframeAnimation")  # after fadeIn/fadeOut, per the DTD
    # drop points that sit on a flat line between equal neighbours
    keep = [pt for i, pt in enumerate(points)
            if i in (0, len(points) - 1) or not (abs(points[i - 1][1] - pt[1]) < 0.01 and abs(points[i + 1][1] - pt[1]) < 0.01)]
    for t, val in keep:
        ET.SubElement(ka, "keyframe", {"time": fmt(t), "value": fmt_db(val), "curve": "linear"})


def _audio_time(t):
    return Fraction(round(Fraction(t) * 48000), 48000)


# ---- operations --------------------------------------------------------------------------------

def op_set_volume(doc, op):
    gain = float(op.get("gain_db") or 0)
    done = []
    for iid in op.get("targets") or []:
        it = doc.item(iid)
        if not doc.has_audio(it):
            continue
        v = _volume(it.el)
        base, kfs = _curve(v)
        v.set("amount", fmt_db(base + gain))
        if kfs:
            _write_keyframes(v, [(t, val + gain) for t, val in kfs])
        done.append(iid)
    if not done:
        raise EditError("none of the targets carry audio")
    return f"{'+' if gain >= 0 else ''}{gain:g} dB on {len(done)} clip(s)"


def _speech_ranges(speech, a, b, pad=0.15, join=1.2):
    segs = sorted((max(a, s - pad), min(b, e + pad)) for s, e in speech if e > a and s < b)
    out = []
    for s, e in segs:
        if out and s - out[-1][1] < join:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def op_duck(doc, op, speech=None):
    gain = -abs(float(op.get("gain_db") or 8))
    ramp = Fraction(str(op.get("ramp_seconds") or 0.4))
    a = float(op.get("start_seconds") or 0)
    b = float(op.get("end_seconds") or 1e9)
    if op.get("under_speech"):
        if not speech:
            raise EditError("no transcript or captions to find speech")
        ranges = _speech_ranges(speech, a, b)
    else:
        ranges = [(a, b)]
    if not ranges:
        raise EditError("no speech in that range")
    done = 0
    for iid in op.get("targets") or []:
        it = doc.item(iid)
        if not doc.has_audio(it):
            continue
        if it.el.find("timeMap") is not None:
            continue
        lo, hi = it.local_start, it.local_start + it.dur
        duck = []
        for s, e in ranges:
            s, e = max(Fraction(str(s)), it.tl), min(Fraction(str(e)), it.end)
            if e - s < Fraction(1, 5):
                continue
            ls, le = it.to_local(s), it.to_local(e)
            duck.append((max(lo, ls - ramp), ls, le, min(hi, le + ramp)))
        if not duck:
            continue
        v = _volume(it.el)
        base, kfs = _curve(v)

        def d(t):
            m = 0.0
            for r0, s0, e0, r1 in duck:
                if s0 <= t <= e0:
                    m = min(m, gain)
                elif r0 <= t < s0 and s0 > r0:
                    m = min(m, gain * float((t - r0) / (s0 - r0)))
                elif e0 < t <= r1 and r1 > e0:
                    m = min(m, gain * float((r1 - t) / (r1 - e0)))
            return m

        times = {_audio_time(t) for t, _ in kfs if lo <= t <= hi}
        for q in duck:
            times |= {_audio_time(x) for x in q}
        times.add(_audio_time(lo))
        pts = [(t, _eval(base, kfs, t) + d(t)) for t in sorted(times)]
        _write_keyframes(v, pts)
        done += 1
    if not done:
        raise EditError("the targets don't overlap the ranges, or are retimed")
    return f"{gain:g} dB dip on {done} clip(s) across {len(ranges)} range(s)"


def op_fade_audio(doc, op):
    fi, fo = op.get("fade_in_seconds"), op.get("fade_out_seconds")
    if not fi and not fo:
        raise EditError("no fade length given")
    n = 0
    for iid in op.get("targets") or []:
        it = doc.item(iid)
        if not doc.has_audio(it):
            continue
        p = _amount_param(_volume(it.el))
        for tag, val, kind in (("fadeIn", fi, "easeIn"), ("fadeOut", fo, "easeOut")):
            if not val:
                continue
            old = p.find(tag)
            if old is not None:
                p.remove(old)
            el = ET.Element(tag, {"type": kind, "duration": fmt(_audio_time(min(Fraction(str(val)), it.dur / 2)))})
            p.insert(0 if tag == "fadeIn" else (1 if p.find("fadeIn") is not None else 0), el)
        n += 1
    if not n:
        raise EditError("none of the targets carry audio")
    return f"fades on {n} clip(s)"


def _transition_template(doc, template):
    if template and template in doc.by_id and doc.by_id[template].kind == "transition":
        return copy.deepcopy(doc.by_id[template].el)
    if template:
        for it in doc.items:
            if it.kind == "transition" and (it.el.get("name") or "").lower() == template.lower():
                return copy.deepcopy(it.el)
    vid = doc.ensure_effect("Cross Dissolve", CROSS_DISSOLVE_UID)
    aid = doc.ensure_effect("Audio Crossfade", AUDIO_CROSSFADE_UID)
    el = ET.Element("transition", {"name": "Cross Dissolve"})
    ET.SubElement(el, "filter-video", {"ref": vid, "name": "Cross Dissolve"})
    ET.SubElement(el, "filter-audio", {"ref": aid, "name": "Audio Crossfade"})
    return el


def op_add_transition(doc, op):
    targets = op.get("targets") or []
    if not targets:
        raise EditError("no clip given")
    dur = doc.snap(Fraction(str(op.get("duration_seconds") or 1)))
    at_end = op.get("position") == "end"
    n = 0
    for iid in targets:
        it = doc.item(iid)
        spine = it.parent
        if spine.tag != "spine" or it.kind == "transition":
            raise EditError(f"{iid} is not on a storyline, so it can't take a transition")
        kids = list(spine)
        idx = kids.index(it.el)
        j = idx + 1 if at_end else idx - 1
        neighbour = kids[j] if 0 <= j < len(kids) else None
        if neighbour is not None and neighbour.tag == "transition":
            raise EditError(f"{iid} already has a transition there")
        tr = _transition_template(doc, op.get("template"))
        edit = fcp_time(it.el.get("offset")) + (it.dur if at_end else 0)
        has_other_side = neighbour is not None and neighbour.tag in CLIP_KINDS
        if has_other_side:
            off = edit - dur / 2
        else:
            off = edit - dur if at_end else edit
        tr.set("offset", fmt(doc.snap(off)))
        tr.set("duration", fmt(dur))
        for k in ("lane", "enabled"):
            tr.attrib.pop(k, None)
        spine.insert(idx + 1 if at_end else idx, tr)
        n += 1
    return f"{tr.get('name')} ({float(dur):g}s) on {n} edit(s)"


def op_remove_transition(doc, op):
    n = 0
    for iid in op.get("targets") or []:
        it = doc.item(iid)
        if it.kind != "transition":
            raise EditError(f"{iid} is not a transition")
        it.parent.remove(it.el)
        n += 1
    return f"removed {n} transition(s)"


def _set_title_text(el, text):
    """Put new text into a title. Multi-line templates (one <text> per animated line) get the words
    spread across their lines so the animation keeps its rhythm; unused lines are left empty."""
    texts = el.findall("text")
    if not texts:
        t = ET.Element("text")
        ts = ET.SubElement(t, "text-style", {"ref": "tsNEW"})
        ts.text = text
        insert_ordered(el, t)
        d = ET.Element("text-style-def", {"id": "tsNEW"})
        ET.SubElement(d, "text-style", {"font": "Helvetica Neue", "fontSize": "72", "fontFace": "Medium",
                                        "fontColor": "1 1 1 1", "alignment": "center"})
        insert_ordered(el, d)
        return
    words = text.split()
    n = len(texts)
    chunks = [" ".join(words[round(i * len(words) / n):round((i + 1) * len(words) / n)]) for i in range(n)] \
        if len(words) >= n else words + [""] * (n - len(words))
    if n == 1:
        chunks = [text]
    for t, chunk in zip(texts, chunks):
        styles = list(t.iter("text-style"))
        keep = styles[0] if styles else ET.SubElement(t, "text-style")
        for child in list(t):
            if child is not keep:
                t.remove(child)
        t.text = None
        keep.text = chunk
        keep.tail = None


def _anchor(doc, host, el, t, lane):
    el.set("offset", fmt(doc.snap(host.local_start + (Fraction(str(t)) - host.tl))))
    if lane is None:
        lanes = [int(c.get("lane")) for c in host.el if c.tag in ANCHOR_TAGS and c.get("lane") and int(c.get("lane")) > 0]
        lane = max(lanes, default=0) + 1
    el.set("lane", str(lane))
    insert_ordered(host.el, el)


def _strip_children(el):
    for c in list(el):
        if c.tag in ANCHOR_TAGS or c.tag in MARKER_TAGS:
            el.remove(c)


def op_add_title(doc, op):
    text = (op.get("text") or "").strip()
    t = op.get("start_seconds")
    if not text or t is None:
        raise EditError("a title needs text and a start time")
    host = doc.host_at(t)
    if host is None:
        raise EditError("no clip on the main storyline at that time")
    tpl = op.get("template")
    if tpl and tpl in doc.by_id and doc.by_id[tpl].kind == "title":
        el = copy.deepcopy(doc.by_id[tpl].el)
        _strip_children(el)
        doc.unique_style_ids(el)
        style = doc.effect_name(doc.by_id[tpl])
    else:
        rid = doc.ensure_effect("Basic Title", BASIC_TITLE_UID)
        el = ET.Element("title", {"ref": rid, "start": "3600s"})
        style = "Basic Title"
        _set_title_text(el, text)
        doc.unique_style_ids(el)
    dur = doc.snap(Fraction(str(op.get("duration_seconds") or 4)))
    el.set("duration", fmt(dur))
    el.set("name", f"{text[:40]} - {style}")
    el.attrib.pop("enabled", None)
    if el.find("text") is not None:
        _set_title_text(el, text)
    _anchor(doc, host, el, t, op.get("lane"))
    return f"“{text[:50]}” at {float(t):.1f}s for {float(dur):g}s"


STYLE_ATTRS = ("font", "fontSize", "fontFace", "fontColor", "bold", "italic", "strokeColor", "strokeWidth",
               "shadowColor", "shadowOffset", "shadowBlurRadius", "kerning", "alignment", "lineSpacing", "tracking",
               "backgroundColor")


def _colour(c):
    c = (c or "").strip()
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", c)
    if m:
        h = m.group(1)
        return " ".join(f"{int(h[i:i + 2], 16) / 255:.4g}" for i in (0, 2, 4)) + " 1"
    return c


def op_restyle_title(doc, op):
    style = {}
    tpl = op.get("template")
    tpl_el = None
    if tpl:
        t_it = doc.item(tpl)
        if t_it.kind != "title":
            raise EditError(f"{tpl} is not a title")
        tpl_el = t_it.el
        first = tpl_el.find("text-style-def/text-style")
        if first is not None:
            style = {k: v for k, v in first.attrib.items() if k in STYLE_ATTRS}
    if op.get("font"):
        style["font"] = op["font"]
    if op.get("font_size"):
        style["fontSize"] = f"{float(op['font_size']):g}"
    if op.get("font_color"):
        style["fontColor"] = _colour(op["font_color"])
    if not style and not op.get("copy_position"):
        raise EditError("nothing to restyle: give a template title or font settings")
    n = 0
    for iid in op.get("targets") or []:
        it = doc.item(iid)
        if it.kind != "title" or it.el is tpl_el:
            continue
        for ts in it.el.iter("text-style"):
            if ts.get("ref"):
                continue  # inline references; the look lives in text-style-def
            ts.attrib.update(style)
        if op.get("copy_position") and tpl_el is not None:
            old = it.el.find("adjust-transform")
            if old is not None:
                it.el.remove(old)
            src = tpl_el.find("adjust-transform")
            if src is not None:
                insert_ordered(it.el, copy.deepcopy(src))
        n += 1
    if not n:
        raise EditError("no titles among the targets")
    return f"restyled {n} title(s)"


def _is_colour(f):
    return any(w in (f.get("name") or "").lower() for w in COLOUR_WORDS)


def op_copy_grade(doc, op):
    tpl = doc.item(op.get("template") or "")
    grade = [copy.deepcopy(f) for f in tpl.el.findall("filter-video") if _is_colour(f)]
    if not grade:
        raise EditError(f"{tpl.id} has no colour correction to copy")
    n = 0
    for iid in op.get("targets") or []:
        it = doc.item(iid)
        if it is tpl or it.kind not in ("asset-clip", "clip", "ref-clip", "sync-clip", "mc-clip", "video"):
            continue
        for f in [f for f in it.el.findall("filter-video") if _is_colour(f)]:
            it.el.remove(f)
        kids = list(it.el)
        first = next((i for i, k in enumerate(kids) if k.tag in ("filter-video", "filter-video-mask")), None)
        for j, g in enumerate(copy.deepcopy(x) for x in grade):
            if first is None:
                insert_ordered(it.el, g)
            else:
                it.el.insert(first + j, g)
        n += 1
    if not n:
        raise EditError("no video clips among the targets")
    return f"copied the grade from {tpl.el.get('name')} to {n} clip(s)"


def op_trim(doc, op):
    head = doc.snap(Fraction(str(op.get("trim_head_seconds") or 0)))
    tail = doc.snap(Fraction(str(op.get("trim_tail_seconds") or 0)))
    if head < 0 or tail < 0 or head + tail <= 0:
        raise EditError("trim lengths must be positive")
    notes = []
    for iid in op.get("targets") or []:
        it = doc.item(iid)
        if it.parent.tag != "spine" or it.kind == "transition":
            raise EditError(f"{iid} is not on a storyline")
        if head + tail >= it.dur - doc.frame:
            raise EditError(f"{iid} is too short to trim that much")
        if head and it.el.find("timeMap") is not None:
            raise EditError(f"{iid} has a speed change; only its end can be trimmed")
        it.el.set("duration", fmt(it.dur - head - tail))
        if head:
            it.el.set("start", fmt(it.local_start + head))
            for c in it.el:
                if c.tag in ANCHOR_TAGS and c.get("offset") and fcp_time(c.get("offset")) < it.local_start + head:
                    notes.append(f"{c.get('name') or c.tag} now starts before the trimmed clip")
        cut = head + tail
        kids = list(it.parent)
        for sib in kids[kids.index(it.el) + 1:]:
            if sib.get("offset") is not None:
                sib.set("offset", fmt(fcp_time(sib.get("offset")) - cut))
        if it.parent is doc.spine:
            doc.seq.set("duration", fmt(fcp_time(doc.seq.get("duration")) - cut))
        it.dur -= cut
    return "trimmed" + (f" (check: {'; '.join(notes[:3])})" if notes else "")


def op_duplicate(doc, op):
    tpl = doc.item(op.get("template") or "")
    t = op.get("start_seconds")
    if t is None:
        raise EditError("no start time")
    if tpl.parent is doc.spine:
        raise EditError("only connected items (overlays, titles, sounds) can be duplicated")
    host = doc.host_at(t)
    if host is None:
        raise EditError("no clip on the main storyline at that time")
    el = copy.deepcopy(tpl.el)
    _strip_children(el)
    if el.tag == "title":
        doc.unique_style_ids(el)
    if op.get("duration_seconds"):
        el.set("duration", fmt(doc.snap(Fraction(str(op["duration_seconds"])))))
    el.attrib.pop("enabled", None)
    _anchor(doc, host, el, t, op.get("lane") if op.get("lane") is not None else tpl.lane)
    return f"copy of {tpl.el.get('name')} at {float(t):.1f}s"


def op_set_enabled(doc, op):
    on = op.get("enabled", True) is not False
    n = 0
    for iid in op.get("targets") or []:
        el = doc.item(iid).el
        if on:
            el.attrib.pop("enabled", None)
        else:
            el.set("enabled", "0")
        n += 1
    return f"{'enabled' if on else 'disabled'} {n} item(s)"


def op_add_marker(doc, op):
    from .markers import _insert_marker

    t = op.get("start_seconds")
    host = doc.host_at(t if t is not None else -1)
    if host is None:
        raise EditError("no clip on the main storyline at that time")
    ET_marker = ET.Element("marker", {"start": fmt(doc.snap(host.to_local(Fraction(str(t))))),
                                      "duration": fmt(doc.frame), "value": (op.get("text") or op.get("note") or "")[:250],
                                      "completed": "0"})
    _insert_marker(host.el, ET_marker)
    return "marker added"


OPS = {
    "set_volume": op_set_volume,
    "duck": op_duck,
    "fade_audio": op_fade_audio,
    "add_transition": op_add_transition,
    "remove_transition": op_remove_transition,
    "add_title": op_add_title,
    "restyle_title": op_restyle_title,
    "copy_grade": op_copy_grade,
    "trim": op_trim,
    "duplicate": op_duplicate,
    "set_enabled": op_set_enabled,
    "add_marker": op_add_marker,
}
# Ops that change the timeline's length or move clips; they are applied last so that
# time-based ops before them still see the original positions.
LATE = ("trim",)
RECUT = ("swap_take", "cut_range", "move_range")


def _order(ops):
    """Normal edits first, then trims, takes, cuts (latest first, so earlier times stay valid), then moves.
    All times in a plan refer to the timeline as it was when the plan was made; moves are shifted to
    account for the cuts made before them."""
    idx = list(range(len(ops)))
    plain = [i for i in idx if ops[i]["op"] not in LATE + RECUT]
    trims = [i for i in idx if ops[i]["op"] in LATE]
    takes = [i for i in idx if ops[i]["op"] == "swap_take"]
    cuts = sorted([i for i in idx if ops[i]["op"] == "cut_range"], key=lambda i: -float(ops[i].get("start_seconds") or 0))
    moves = [i for i in idx if ops[i]["op"] == "move_range"]
    return plain + trims + takes + cuts + moves


def _shift_for_cuts(op, done_cuts):
    def m(t):
        t = float(t)
        return t - sum(b - a for a, b in done_cuts if b <= t + 1e-9) - sum(t - a for a, b in done_cuts if a < t < b)
    op = dict(op)
    for k in ("start_seconds", "end_seconds", "to_seconds"):
        if op.get(k) is not None:
            op[k] = m(op[k])
    return op


def apply_ops(doc, ops, speech=None):
    results = []
    done_cuts = []
    for i in _order(ops):
        op = ops[i]
        if op.get("op") == "move_range" and done_cuts:
            op = _shift_for_cuts(op, done_cuts)
        fn = OPS.get(op.get("op"))
        try:
            if fn is None:
                raise EditError(f"unknown edit type {op.get('op')}")
            detail = fn(doc, op, speech) if fn is op_duck else fn(doc, op)
            results.append((i, "applied", detail))
            if op.get("op") == "cut_range":
                done_cuts.append((float(op["start_seconds"]), float(op["end_seconds"])))
        except EditError as e:
            results.append((i, "skipped", str(e)))
        except Exception as e:  # keep going; report the failure for this one op
            results.append((i, "skipped", f"{e.__class__.__name__}: {e}"))
    results.sort()
    return [{"index": i, "status": s, "detail": d} for i, s, d in results]


from .recut import op_cut_range, op_move_range, op_swap_take  # noqa: E402

OPS.update({"cut_range": op_cut_range, "move_range": op_move_range, "swap_take": op_swap_take})

VERSION_RE = re.compile(r"\s+[–-]\s+(v\d+|Design review)$")


def base_name(project_name):
    return VERSION_RE.sub("", project_name or "Project")
