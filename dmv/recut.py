"""Re-cutting a Final Cut timeline: ripple cuts, moving sections and swapping takes.

Works like Final Cut's own blade + ripple delete. Every cut point is first bladed through the whole
primary storyline: the storyline clip under the point is split, and every connected item that runs
across the point (music stems, overlays, titles, captions, connected storylines), wherever it is
anchored, is split too, its later half re-anchored to the clip that starts at the point. After that,
removing or moving a section keeps everything after it in sync. Transitions that straddle a cut
point are removed (their edit no longer exists).

Ops (added to dmv.edit.OPS):
  cut_range   start_seconds, end_seconds                 remove a section, close the gap
  move_range  start_seconds, end_seconds, to_seconds     move a section so it starts where `to` was
  swap_take   targets=[clip id], source_start_seconds, optional source (file/clip name)
"""
import copy
import xml.etree.ElementTree as ET
from fractions import Fraction

from .timecode import fcp_time

CLIPLIKE = {"asset-clip", "clip", "video", "audio", "ref-clip", "sync-clip", "mc-clip", "title", "gap", "audition"}
CONNECTED = CLIPLIKE | {"spine"}
MARKERS = {"marker", "chapter-marker", "rating", "keyword", "analysis-marker"}


from .edit import EditError as RecutError  # noqa: E402  (shown to the editor like any other edit problem)


def F(v):
    return fcp_time(v) if isinstance(v, str) else Fraction(v)


def fmt(t):
    t = Fraction(t)
    return f"{t.numerator}s" if t.denominator == 1 else f"{t.numerator}/{t.denominator}s"


def _start(el):
    return F(el.get("start")) if el.get("start") else Fraction(0)


def _dur(el):
    if el.tag == "spine" and not el.get("duration"):
        return max([F(k.get("offset")) + F(k.get("duration")) for k in el if k.tag != "transition" and k.get("offset")] or [Fraction(0)])
    return F(el.get("duration"))


def _connected(el):
    """Items anchored to el (lane != 0). Lane-0 children of a wrapper clip are its own contents."""
    return [c for c in el if c.tag in CONNECTED and c.get("lane") not in (None, "0")]


# ------------------------------------------------------------------ splitting
def split_el(el, at):
    """Split a clip-like element at local time `at` (start < at < start+dur). Returns (left, right).
    Connected items move to the half they start in; ones that cross `at` are split as well."""
    s, d = _start(el), _dur(el)
    if not (s < at < s + d):
        raise RecutError("split point outside the clip")
    if el.find("timeMap") is not None and el.tag != "title":
        pass  # local time is the retimed domain; start/duration stay consistent
    left, right = copy.deepcopy(el), copy.deepcopy(el)
    for x in (left, right):
        for c in [c for c in x if c.tag in CONNECTED and c.get("lane") not in (None, "0")] + [c for c in x if c.tag in MARKERS]:
            x.remove(c)
    left.set("duration", fmt(at - s))
    right.set("start", fmt(at))
    right.set("duration", fmt(s + d - at))
    if el.get("offset") is not None:
        right.set("offset", fmt(F(el.get("offset")) + (at - s)))
    for c in _connected(el):
        co, cd = F(c.get("offset")), _dur(c)
        if co + cd <= at:
            _append_child(left, copy.deepcopy(c))
        elif co >= at:
            _append_child(right, copy.deepcopy(c))
        else:
            cl, cr = split_connected(c, at - co)
            _append_child(left, cl)
            cr.set("offset", fmt(at))
            _append_child(right, cr)
    for m in [m for m in el if m.tag in MARKERS]:
        ms = F(m.get("start"))
        _append_child(left if ms < at else right, copy.deepcopy(m))
    return left, right


def split_connected(c, rel):
    """Split a connected item `rel` seconds after its anchor point. Right part keeps its own offset unset (caller sets)."""
    if c.tag == "spine":
        return split_spine(c, rel)
    s = _start(c)
    left, right = split_el(c, s + rel)
    return left, right


def split_spine(sp, rel):
    """Split a connected storyline `rel` seconds into it."""
    left = ET.Element("spine", {k: v for k, v in sp.attrib.items()})
    right = ET.Element("spine", {k: v for k, v in sp.attrib.items()})
    kids = list(sp)
    for k in kids:
        if k.tag == "transition":
            o, d = F(k.get("offset")), F(k.get("duration"))
            if o + d <= rel:
                left.append(copy.deepcopy(k))
            elif o >= rel:
                kk = copy.deepcopy(k)
                kk.set("offset", fmt(o - rel))
                right.append(kk)
            continue  # a transition across the split is dropped
        if k.get("offset") is None:
            left.append(copy.deepcopy(k))
            continue
        o, d = F(k.get("offset")), _dur(k)
        if o + d <= rel:
            left.append(copy.deepcopy(k))
        elif o >= rel:
            kk = copy.deepcopy(k)
            kk.set("offset", fmt(o - rel))
            right.append(kk)
        else:
            kl, kr = split_el(k, _start(k) + (rel - o))
            left.append(kl)
            kr.set("offset", fmt(0))
            right.append(kr)
    return left, right


def _append_child(parent, child):
    from .edit import insert_ordered
    insert_ordered(parent, child)


# ------------------------------------------------------------------ the primary storyline
class Spine:
    def __init__(self, doc):
        self.doc = doc
        self.sp = doc.spine

    def clips(self):
        out = []
        for k in self.sp:
            if k.tag == "transition" or k.get("offset") is None:
                continue
            o = F(k.get("offset"))
            out.append((o, o + _dur(k), k))
        return out

    def blade(self, T, log):
        """Split everything on and connected to the primary storyline at timeline time T."""
        T = self.doc.snap(Fraction(T))
        # transitions straddling T go
        for k in list(self.sp):
            if k.tag == "transition":
                o, d = F(k.get("offset")), F(k.get("duration"))
                if o < T < o + d:
                    self.sp.remove(k)
                    log.append(f"removed a {(k.find('filter-video').get('name') if k.find('filter-video') is not None else 'transition')} at {float(T):.2f}s")
        clips = self.clips()
        host = next(((o, e, k) for o, e, k in clips if o <= T < e), None)
        if host is None:
            return
        o, e, k = host
        if k.find("timeMap") is not None and o < T:
            raise RecutError(f"the storyline clip at {float(T):.2f}s has a speed change; cut it by hand in Final Cut")
        if o < T:
            l, r = split_el(k, _start(k) + (T - o))
            i = list(self.sp).index(k)
            self.sp.remove(k)
            self.sp.insert(i, r)
            self.sp.insert(i, l)
            right_host = r
        else:
            right_host = k
        # connected items on earlier storyline clips that run across T
        for po, pe, p in self.clips():
            if po >= T or p is right_host:
                continue
            ps = _start(p)
            for c in _connected(p):
                ct = po + (F(c.get("offset")) - ps)
                if ct < T < ct + _dur(c):
                    cl, cr = split_connected(c, T - ct)
                    i = list(p).index(c)
                    p.remove(c)
                    p.insert(i, cl)
                    cr.set("offset", fmt(_start(right_host)))
                    _append_child(right_host, cr)

    def reflow(self, order=None):
        """Lay storyline clips end to end again (optionally in a new order); transitions keep their place
        relative to the clip after them."""
        kids = list(self.sp)
        rel = {}
        for i, k in enumerate(kids):
            if k.tag == "transition":
                nxt = next((x for x in kids[i + 1:] if x.tag != "transition"), None)
                if nxt is not None:
                    rel[id(k)] = (nxt, F(nxt.get("offset")) - F(k.get("offset")))
        seq = order if order is not None else kids
        for k in list(self.sp):
            self.sp.remove(k)
        t = Fraction(0)
        for k in seq:
            self.sp.append(k)
            if k.tag != "transition" and k.get("offset") is not None:
                k.set("offset", fmt(t))
                t += _dur(k)
        for k in seq:
            if k.tag == "transition" and id(k) in rel:
                nxt, d = rel[id(k)]
                k.set("offset", fmt(F(nxt.get("offset")) - d))
        self.doc.seq.set("duration", fmt(t))
        return t


def dedupe_style_ids(doc):
    """A title split in two carries the same text-style ids twice; give the second copy fresh ids."""
    used, seen = {e.get("id") for e in doc.root.iter() if e.get("id")}, set()
    n = 0
    for title in doc.root.iter("title"):
        mapping = {}
        for d in title.findall("text-style-def"):
            i = d.get("id")
            if i in seen:
                n += 1
                while f"tsR{n}" in used:
                    n += 1
                mapping[i] = f"tsR{n}"
                used.add(f"tsR{n}")
                d.set("id", f"tsR{n}")
            seen.add(d.get("id"))
        if mapping:
            for ts in title.iter("text-style"):
                if ts.get("ref") in mapping:
                    ts.set("ref", mapping[ts.get("ref")])


def _refresh(doc):
    """Re-index the timeline after the storyline changed shape."""
    dedupe_style_ids(doc)
    doc.items, doc.by_id, doc.by_el = [], {}, {}
    doc._walk(doc.spine, Fraction(0), Fraction(0), 0, True, None)


# ------------------------------------------------------------------ ops
def op_cut_range(doc, op):
    s, e = doc.snap(Fraction(str(op["start_seconds"]))), doc.snap(Fraction(str(op["end_seconds"])))
    if e - s < doc.frame:
        raise RecutError("the section to remove is shorter than one frame")
    log = []
    sp = Spine(doc)
    sp.blade(s, log)
    sp.blade(e, log)
    keep = []
    removed = 0
    for k in list(sp.sp):
        if k.tag == "transition":
            o, d = F(k.get("offset")), F(k.get("duration"))
            if s <= o and o + d <= e:
                removed += 1
                continue
            keep.append(k)
            continue
        o = F(k.get("offset")) if k.get("offset") is not None else None
        if o is not None and s <= o < e:
            removed += 1
            continue
        keep.append(k)
    sp.reflow(keep)
    _refresh(doc)
    return f"removed {float(e - s):.2f}s at {float(s):.2f}s" + (f" ({'; '.join(log)})" if log else "")


def op_move_range(doc, op):
    s, e = doc.snap(Fraction(str(op["start_seconds"]))), doc.snap(Fraction(str(op["end_seconds"])))
    to = doc.snap(Fraction(str(op["to_seconds"])))
    if e - s < doc.frame:
        raise RecutError("the section to move is shorter than one frame")
    if s < to < e:
        raise RecutError("the destination is inside the section being moved")
    log = []
    sp = Spine(doc)
    for T in sorted({s, e, to}):
        sp.blade(T, log)
    kids = list(sp.sp)
    block = [k for k in kids if k.tag != "transition" and k.get("offset") is not None and s <= F(k.get("offset")) < e]
    if not block:
        raise RecutError("nothing on the storyline in that section")
    rest = [k for k in kids if k not in block]
    # transitions wholly inside the moved block travel with it
    inner_tr = [k for k in rest if k.tag == "transition" and s <= F(k.get("offset")) and F(k.get("offset")) + F(k.get("duration")) <= e]
    rest = [k for k in rest if k not in inner_tr]
    block = sorted(block + inner_tr, key=lambda k: F(k.get("offset")))
    at = next((i for i, k in enumerate(rest) if k.tag != "transition" and k.get("offset") is not None and F(k.get("offset")) >= to), len(rest))
    sp.reflow(rest[:at] + block + rest[at:])
    _refresh(doc)
    return f"moved {float(s):.2f}–{float(e):.2f}s to {float(to):.2f}s" + (f" ({'; '.join(log)})" if log else "")


def op_swap_take(doc, op):
    """Use a different moment (or another file) for a clip, keeping its place and length."""
    ids = op.get("targets") or []
    if len(ids) != 1:
        raise RecutError("swap_take needs exactly one clip")
    it = doc.item(ids[0])
    el = it.el
    src_s = Fraction(str(op["source_start_seconds"]))
    new_ref = None
    if op.get("source"):
        want = str(op["source"]).lower()
        for aid, a in doc.assets.items():
            mr = a.find("media-rep")
            path = (mr.get("src") if mr is not None else "") or ""
            if want in (a.get("name") or "").lower() or want in path.lower():
                new_ref = aid
                break
        if new_ref is None:
            raise RecutError(f"no media named {op['source']!r} in this project")
    if el.tag == "asset-clip":
        ref = new_ref or el.get("ref")
        a = doc.assets[ref]
        el.set("ref", ref)
        el.set("start", fmt(doc.snap(src_s + F(a.get("start") or "0s"))))
        dur = F(a.get("duration") or "0s")
        if dur and src_s + it.dur > dur:
            raise RecutError("the new take is shorter than the clip")
        if new_ref:
            el.set("name", a.get("name") or el.get("name"))
    elif el.tag == "clip":
        inner = next((c for c in el if c.tag in ("video", "asset-clip") and c.get("lane") in (None, "0")), None)
        if inner is None:
            raise RecutError("this clip has no picture inside to retake")
        if new_ref:
            raise RecutError("this clip wraps its media; swapping to another file has to be done in Final Cut")
        a = doc.assets[inner.get("ref")]
        local = src_s + F(a.get("start") or "0s") - _start(inner) + F(inner.get("offset") or "0s")
        el.set("start", fmt(doc.snap(local)))
    else:
        raise RecutError(f"{el.tag} clips can't be retaken")
    return f"{el.get('name')} now uses the take at {float(src_s):.2f}s" + (f" of {doc.assets[new_ref].get('name')}" if new_ref else "")


# ------------------------------------------------------------------ fillers
def filler_ops(verbatim, film_end=None, pad_before=0.03, pad_after=0.05, min_len=0.12):
    """Cut plan for every filler word in a verbatim transcript (timeline-aligned)."""
    words = verbatim.get("words") or []
    ops = []
    for i, w in enumerate(words):
        if not w.get("filler"):
            continue
        prev_end = words[i - 1]["end"] if i else 0.0
        nxt_start = words[i + 1]["start"] if i + 1 < len(words) else w["end"] + 0.3
        a = max(prev_end + 0.02, w["start"] - pad_before)
        b = min(nxt_start - pad_after, w["end"] + 0.25)
        if film_end and b > film_end:
            continue
        if b - a < min_len:
            continue
        ctx = " ".join(x["word"] for x in words[max(0, i - 4): i + 5])
        ops.append({"op": "cut_range", "start_seconds": round(a, 2), "end_seconds": round(b, 2),
                    "note": f"Remove “{w['word']}” — …{ctx}…"})
    # later cuts first, so earlier timecodes stay valid while applying
    ops.sort(key=lambda o: -o["start_seconds"])
    return ops
