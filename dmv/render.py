"""Render a Final Cut Pro timeline (FCPXML) to a video file, without Final Cut.

Used on request from the app: a quick preview of any version, or a finished file in a chosen
format. The renderer reads the timeline the way Final Cut lays it out (primary storyline, connected
clips by lane, compound clips, wrapper clips, speed changes) and composites it frame by frame.

Reproduced: cuts and timing, speed changes (timeMap), position / scale / rotation / crop, opacity,
blend modes (Normal, Subtract, Add, Multiply, Screen, Lighten, Darken, Overlay), Cross Dissolve and
Fade To Color, solid generators, still images, Basic Title text (font, size, colour, shadow, alpha),
the audio mix (clip volumes incl. keyframes, muted parts).
Not reproduced (listed in the render report): Final Cut colour corrections and LUTs, Motion templates
other than Basic Title (their text is skipped), other video effects, audio effects.
"""
import bisect
import json
import math
import subprocess
import time
import urllib.parse
import xml.etree.ElementTree as ET
from collections import Counter
from fractions import Fraction
from pathlib import Path

import numpy as np

from .fcpxml import resolve_path
from .timecode import fcp_time

PRESETS = {
    # name: (height, video codec args, audio args, extension)
    "preview": (720, ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p"], ["-c:a", "aac", "-b:a", "192k"], ".mp4"),
    "hd": (1080, ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p"], ["-c:a", "aac", "-b:a", "320k"], ".mp4"),
    "hevc4k": (2160, ["-c:v", "libx265", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p10le", "-tag:v", "hvc1"], ["-c:a", "aac", "-b:a", "320k"], ".mp4"),
    "prores": (None, ["-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le", "-vendor", "apl0"], ["-c:a", "pcm_s24le"], ".mov"),
}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".heic", ".webp", ".bmp", ".psd"}
BASIC_TITLE = "Basic Title.moti"
ADJUSTMENT = "Adjustment Layer"
SR = 48000


# ------------------------------------------------------------------ time maps
class TimeMap:
    """Piecewise-linear clip-local time -> source time (Final Cut timeMap)."""

    def __init__(self, el):
        pts = sorted((fcp_time(p.get("time")), fcp_time(p.get("value"))) for p in el.findall("timept"))
        self.t = [float(a) for a, _ in pts]
        self.v = [float(b) for _, b in pts]

    def __call__(self, x):
        if len(self.t) == 1:
            return self.v[0]
        return float(np.interp(x, self.t, self.v, left=None, right=None)) if self.t[0] <= x <= self.t[-1] else (
            self.v[0] + (x - self.t[0]) * self._slope(0) if x < self.t[0] else self.v[-1] + (x - self.t[-1]) * self._slope(-1))

    def _slope(self, i):
        if i == 0:
            a, b = 0, 1
        else:
            a, b = -2, -1
        dt = self.t[b] - self.t[a]
        return (self.v[b] - self.v[a]) / dt if dt else 1.0


def compose(parent, offset, start, tmap=None):
    """Child local-time function from the parent's container-time function."""
    def f(t, parent=parent, offset=offset, start=start, tmap=tmap):
        x = start + (parent(t) - offset)
        return tmap(x) if tmap else x
    return f


def invert(fn, target, lo, hi):
    """Timeline time where the monotonic fn reaches target, within [lo, hi]."""
    a, b = lo, hi
    fa = fn(a)
    if fa >= target:
        return a
    if fn(b) <= target:
        return b
    for _ in range(40):
        m = (a + b) / 2
        if fn(m) < target:
            a = m
        else:
            b = m
    return b


# ------------------------------------------------------------------ layers
class Layer:
    __slots__ = ("kind", "t0", "t1", "z", "src", "fmap", "xf", "crop", "opacity", "blend", "el", "title", "colour",
                 "ramp", "gain", "keys", "fps", "asset", "speed", "name", "local", "cc")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))


def _floats(s, n):
    vals = [float(x) for x in (s or "").split()]
    return vals + [0.0] * (n - len(vals)) if vals else None


def transform_of(el):
    a = el.find("adjust-transform")
    xf = {"pos": (0.0, 0.0), "scale": (1.0, 1.0), "rot": 0.0, "anchor": (0.0, 0.0)}
    if a is not None:
        if a.get("position"):
            xf["pos"] = tuple(_floats(a.get("position"), 2)[:2])
        if a.get("scale"):
            xf["scale"] = tuple(_floats(a.get("scale"), 2)[:2])
        if a.get("rotation"):
            xf["rot"] = float(a.get("rotation"))
        if a.get("anchor"):
            xf["anchor"] = tuple(_floats(a.get("anchor"), 2)[:2])
    return xf


def crop_of(el):
    c = el.find("adjust-crop")
    if c is None:
        return None
    r = c.find("trim-rect") if c.find("trim-rect") is not None else c.find("crop-rect")
    if r is None:
        return None
    return tuple(float(r.get(k, 0)) for k in ("left", "top", "right", "bottom"))


CC_UID = "FFColorCorrectionHDREffect"


def colour_luts(el, effects, skipped):
    """Final Cut Color Board (Color Correction) -> 256-entry tables, one per correction on this element.
    Exposure pucks map to a CDL fitted on real Final Cut renders (max error ~0.01):
    slope = 1 + 2(H-.5) - 2(S-.5), offset = 2.18(G-.5) + 1.88(S-.5), power = 1 - 1.82(M-.5).
    Global saturation scales chroma by 2*value. Colour pucks are not reproduced."""
    out = []
    for f in el.findall("filter-video"):
        eff = effects.get(f.get("ref"))
        if eff is None or eff.get("uid") != CC_UID:
            continue
        if f.get("enabled") == "0":
            continue
        v = {p.get("key"): float(p.get("value")) for p in f.findall("param") if p.get("value") and
             p.get("value").replace(".", "", 1).replace("-", "", 1).isdigit()}
        G, H, M, S_ = v.get("2008", .5), v.get("2009", .5), v.get("2010", .5), v.get("2011", .5)
        slope = 1 + 2 * (H - .5) - 2 * (S_ - .5)
        offset = 2.18 * (G - .5) + 1.88 * (S_ - .5)
        power = 1 - 1.82 * (M - .5)
        x = np.arange(256) / 255.0
        y = np.clip(x * slope + offset, 0, 1) ** max(0.05, power)
        out.append((np.clip(y * 255 + .5, 0, 255).astype(np.uint8), 2 * v.get("2004", .5)))
        other = [k for k in v if k not in ("2004", "2008", "2009", "2010", "2011")]
        if other:
            skipped["colour board colour/saturation pucks (approximated)"] += 1
    return out


def apply_cc(img, cc):
    import cv2
    if img.shape[2] == 4:
        return np.dstack([apply_cc(np.ascontiguousarray(img[:, :, :3]), cc), img[:, :, 3]])
    for lut, sat in cc:
        img = cv2.LUT(img, lut)
        if abs(sat - 1) > 0.01:
            g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)[:, :, None].astype(np.float32)
            img = np.clip(g + (img.astype(np.float32) - g) * sat, 0, 255).astype(np.uint8)
    return img


def blend_of(el):
    b = el.find("adjust-blend")
    if b is None:
        return 1.0, "Normal"
    amt = float(b.get("amount", 1))
    mode = (b.get("mode") or "0 (Normal)")
    name = mode.split("(")[-1].rstrip(")") if "(" in mode else "Normal"
    return amt, name


def volume_of(el):
    v = el.find("adjust-volume")
    if v is None:
        return 0.0, None
    base = float((v.get("amount") or "0dB").replace("dB", "") or 0)
    keys = []
    p = v.find("param")
    if p is not None:
        for k in p.iter("keyframe"):
            keys.append((float(fcp_time(k.get("time"))), float((k.get("value") or "0dB").replace("dB", "") or 0)))
    return base, (sorted(keys) or None)


class Timeline:
    def __init__(self, path):
        self.path = resolve_path(path)
        self.root = ET.parse(self.path).getroot()
        res = self.root.find("resources")
        self.assets = {a.get("id"): a for a in res.findall("asset")}
        self.effects = {e.get("id"): e for e in res.findall("effect")}
        self.media = {m.get("id"): m for m in res.findall("media")}
        self.formats = {f.get("id"): f for f in res.findall("format")}
        self.project = next(self.root.iter("project"))
        seq = self.project.find("sequence")
        fmt = self.formats[seq.get("format")]
        self.width, self.height = int(fmt.get("width", 1920)), int(fmt.get("height", 1080))
        fd = fcp_time(fmt.get("frameDuration") or "1/25s")
        self.fps = float(1 / fd)
        self.duration = float(fcp_time(seq.get("duration")))
        self.video, self.audio, self.skipped = [], [], Counter()
        self._order = 0
        self._walk_spine(seq.find("spine"), lambda t: t, (), 1.0, True, 0.0, primary=True)

    # -------------------------------------------------- helpers
    def asset_path(self, aid):
        a = self.assets.get(aid)
        if a is None:
            return None
        mr = a.find("media-rep")
        src = mr.get("src") if mr is not None else a.get("src")
        if not src:
            return None
        return urllib.parse.unquote(src.replace("file://", ""))

    def _next(self):
        self._order += 1
        return self._order

    def _window(self, parent, el, t_lo, t_hi):
        """Timeline interval in which el is active, given the parent's container-time function."""
        off = float(fcp_time(el.get("offset")))
        if el.tag == "spine" and not el.get("duration"):
            # a storyline has no duration of its own: it lasts as long as its contents
            dur = max([float(fcp_time(k.get("offset")) + fcp_time(k.get("duration"))) for k in el
                       if k.get("offset") is not None and k.tag != "transition"] or [0.0])
        else:
            dur = float(fcp_time(el.get("duration")))
        a = invert(parent, off, t_lo, t_hi)
        b = invert(parent, off + dur, t_lo, t_hi)
        return a, b

    # -------------------------------------------------- walk
    def _walk_spine(self, spine, container, zpre, opacity, enabled, gain, primary=False, lo=0.0, hi=None):
        hi = self.duration if hi is None else hi
        kids = [k for k in spine if k.tag not in ("note",)]
        clips = [k for k in kids if k.tag not in ("transition",)]
        trans = {}
        for i, k in enumerate(kids):
            if k.tag == "transition":
                prev = next((x for x in reversed(kids[:i]) if x.tag != "transition"), None)
                nxt = next((x for x in kids[i + 1:] if x.tag != "transition"), None)
                fv = k.find("filter-video")
                name = (fv.get("name") if fv is not None else k.get("name")) or "Cross Dissolve"
                o = float(fcp_time(k.get("offset")))
                d = float(fcp_time(k.get("duration")))
                ta, tb = invert(container, o, lo, hi), invert(container, o + d, lo, hi)
                if prev is not None:
                    trans.setdefault(id(prev), {})["out"] = (ta, tb, name)
                if nxt is not None:
                    trans.setdefault(id(nxt), {})["in"] = (ta, tb, name)
        for k in clips:
            self._walk_el(k, container, zpre, opacity, enabled, gain, lo, hi, trans.get(id(k)), in_spine=True)

    def _walk_el(self, el, parent, z, opacity, enabled, gain, lo, hi, tr=None, in_spine=False):
        tag = el.tag
        if tag not in ("asset-clip", "clip", "video", "audio", "ref-clip", "sync-clip", "mc-clip", "title", "gap", "spine"):
            return
        en = enabled and el.get("enabled", "1") != "0"
        t0, t1 = self._window(parent, el, lo, hi)
        if tr and "in" in tr:
            t0 = min(t0, tr["in"][0])
        if tr and "out" in tr:
            t1 = max(t1, tr["out"][1])
        if t1 <= t0 + 1e-9 or not en:
            return
        lane = int(el.get("lane") or 0)
        if tag == "spine":
            off = float(fcp_time(el.get("offset")))
            cont = (lambda t, parent=parent, off=off: parent(t) - off)
            self._walk_spine(el, cont, z + (lane, self._next()), opacity, en, gain, lo=t0, hi=t1)
            return
        start = float(fcp_time(el.get("start"))) if el.get("start") else 0.0
        tm = el.find("timeMap")
        tmap = TimeMap(tm) if tm is not None else None
        local = compose(parent, float(fcp_time(el.get("offset"))), start, tmap)
        amt, mode = blend_of(el)
        op = opacity * amt
        vol, keys = volume_of(el)
        g = gain + (0.0 if keys else vol)   # keyframes, when present, define this clip's own level
        ramp = None
        if tr:
            ramp = {k: v for k, v in tr.items()}
        xf, crop = transform_of(el), crop_of(el)
        zz = z + (lane, self._next())   # z = prefix of the storyline/group this item sits in
        name = el.get("name") or tag
        src_enable = el.get("srcEnable", "all")

        if tag == "title":
            eff = self.effects.get(el.get("ref"))
            uid = (eff.get("uid") or "") if eff is not None else ""
            if ADJUSTMENT in uid:
                for f in el.findall("filter-video"):
                    self.skipped[f"effect on adjustment layer: {f.get('name')}"] += 1
                return
            if BASIC_TITLE not in uid:
                self.skipped[f"title template: {eff.get('name') if eff is not None else '?'}"] += 1
                return
            self.video.append(Layer(kind="title", t0=t0, t1=t1, z=zz, xf=xf, opacity=op, blend=mode, el=el, ramp=ramp, name=name))
            return
        if tag == "gap":
            pass
        elif tag in ("asset-clip", "video", "audio"):
            ref = el.get("ref")
            if ref in self.effects:          # generator
                eff = self.effects[ref]
                if "Solids" in (eff.get("uid") or ""):
                    colour = (0, 0, 0)
                    for p in el.iter("param"):
                        if p.get("name") == "Color":
                            c = _floats(p.get("value"), 3)
                            colour = tuple(int(255 * max(0, min(1, x))) for x in c[:3])
                    self.video.append(Layer(kind="solid", t0=t0, t1=t1, z=zz, colour=colour, opacity=op, blend=mode, xf=xf,
                                            ramp=ramp, name=name))
                else:
                    self.skipped[f"generator: {eff.get('name')}"] += 1
            else:
                a = self.assets.get(ref)
                path = self.asset_path(ref)
                if a is not None and path:
                    astart = float(fcp_time(a.get("start")))
                    fmap = (lambda t, local=local, astart=astart: local(t) - astart)
                    speed = 1.0
                    if tmap:
                        speed = abs((fmap(t0 + 0.5) - fmap(t0)) / 0.5) if t1 - t0 > 0.6 else 1.0
                    is_img = Path(path).suffix.lower() in IMAGE_EXT
                    if tag != "audio" and a.get("hasVideo") == "1" and src_enable != "audio":
                        self.video.append(Layer(kind="still" if is_img else "video", t0=t0, t1=t1, z=zz, src=path, fmap=fmap,
                                                xf=xf, crop=crop, opacity=op, blend=mode, ramp=ramp, name=name, asset=a,
                                                cc=colour_luts(el, self.effects, self.skipped)))
                    if tag != "video" and a.get("hasAudio") == "1" and src_enable != "video" and self._audio_on(el):
                        self.audio.append(Layer(kind="audio", t0=t0, t1=t1, src=path, fmap=fmap, gain=g, keys=keys,
                                                el=el, speed=speed, name=name, ramp=ramp, local=local))
        elif tag == "ref-clip":
            m = self.media.get(el.get("ref"))
            if m is not None and m.find("sequence") is not None:
                se = el.get("srcEnable", "all")
                save_v, save_a = len(self.video), len(self.audio)
                self._walk_spine(m.find("sequence").find("spine"), local, zz, op, en, g, lo=t0, hi=t1)
                if se == "audio":
                    del self.video[save_v:]
                if se == "video":
                    del self.audio[save_a:]
        elif tag in ("clip", "sync-clip"):
            self._group(el, local, zz, op, en, g, t0, t1, xf, crop, mode, ramp)
            self._children(el, local, z if in_spine else zz, opacity, enabled, gain, lo, hi)
            return
        elif tag == "mc-clip":
            self.skipped["multicam clip"] += 1
        # connected items anchored to this one: they stack by lane in the storyline this item belongs to
        self._children(el, local, z if in_spine else zz, opacity, enabled, gain, lo, hi)
        self._note_effects(el)

    def _note_effects(self, el):
        for f in el.findall("filter-video"):
            eff = self.effects.get(f.get("ref"))
            if eff is None or eff.get("uid") != CC_UID:
                self.skipped[f"video effect: {f.get('name')}"] += 1

    def _children(self, el, local, z, opacity, enabled, gain, lo, hi):
        for c in el:
            if c.tag in ("asset-clip", "clip", "video", "audio", "ref-clip", "sync-clip", "mc-clip", "title", "gap", "spine") \
                    and (c.get("lane") not in (None, "0")):
                self._walk_el(c, local, z, opacity, enabled, gain, lo, hi)

    def _group(self, el, local, z, op, en, g, t0, t1, xf, crop, mode, ramp):
        """A wrapper clip: its contents are drawn inside it, then the clip's own transform applies."""
        nv = len(self.video)
        for c in el:
            if c.tag == "spine":
                self._walk_spine(c, (lambda t, local=local, off=float(fcp_time(c.get("offset"))): local(t) - off),
                                 z + (int(c.get("lane") or 0), self._next()), op, en, g, lo=t0, hi=t1)
            elif c.tag in ("asset-clip", "clip", "video", "audio", "ref-clip", "sync-clip", "mc-clip", "title", "gap") \
                    and c.get("lane") in (None, "0"):
                self._walk_el(c, local, z, op, en, g, t0, t1)
        for L in self.video[nv:]:
            L.t0, L.t1 = max(L.t0, t0), min(L.t1, t1)
            if xf != {"pos": (0.0, 0.0), "scale": (1.0, 1.0), "rot": 0.0, "anchor": (0.0, 0.0)} or crop:
                L.xf = combine(L.xf, xf)
                if crop and not L.crop:
                    L.crop = crop
            if mode != "Normal":
                L.blend = mode
            if ramp and not L.ramp:
                L.ramp = ramp
            wrap_cc = colour_luts(el, self.effects, self.skipped)
            if wrap_cc and L.kind in ("video", "still"):
                L.cc = (L.cc or []) + wrap_cc
        self._note_effects(el)

    def _audio_on(self, el):
        for s in el.findall("audio-channel-source") + el.findall("audio-role-source"):
            if s.get("enabled") == "0" or s.get("active") == "0":
                return False
        return True


def combine(inner, outer):
    s = (inner["scale"][0] * outer["scale"][0], inner["scale"][1] * outer["scale"][1])
    p = (inner["pos"][0] * outer["scale"][0] + outer["pos"][0], inner["pos"][1] * outer["scale"][1] + outer["pos"][1])
    return {"pos": p, "scale": s, "rot": inner["rot"] + outer["rot"], "anchor": inner["anchor"]}


# ------------------------------------------------------------------ pictures
class Decoder:
    """Sequential frames of one source file at the output size ('fit'), restarted on jumps."""

    def __init__(self, path, out_w, out_h):
        self.path, self.W, self.H = path, out_w, out_h
        pr = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                             "stream=width,height,avg_frame_rate,r_frame_rate,pix_fmt:stream_side_data=rotation", "-of", "json", path],
                            capture_output=True, text=True)
        s = json.loads(pr.stdout or "{}").get("streams", [{}])[0]
        self.sw, self.sh = int(s.get("width", out_w)), int(s.get("height", out_h))
        pf = s.get("pix_fmt", "")
        self.alpha = pf.startswith(("yuva", "rgba", "bgra", "argb", "abgr", "gbrap", "ya"))
        self.ch = 4 if self.alpha else 3
        fr = s.get("avg_frame_rate") or s.get("r_frame_rate") or "25/1"
        n, d = (int(x) for x in fr.split("/")) if "/" in fr else (25, 1)
        self.fps = n / d if d and n else 25.0
        sc = min(out_w / self.sw, out_h / self.sh)
        self.fw, self.fh = max(2, int(round(self.sw * sc / 2)) * 2), max(2, int(round(self.sh * sc / 2)) * 2)
        self.p, self.idx, self.frame = None, None, None

    def _start(self, t):
        self.close()
        t = max(0.0, t)
        from . import runtime
        self.p = subprocess.Popen(["ffmpeg", "-v", "error", *runtime.hwaccel(), "-ss", f"{t:.4f}", "-i", self.path,
                                   "-an", "-vf", f"scale={self.fw}:{self.fh}:flags=bicubic", "-f", "rawvideo",
                                   "-pix_fmt", "bgra" if self.alpha else "bgr24", "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.idx = int(round(t * self.fps)) - 1
        self.frame = None

    def at(self, t):
        want = int(round(t * self.fps))
        if self.p is None or want < self.idx or want > self.idx + int(self.fps * 3):
            self._start(want / self.fps)
        n = self.fw * self.fh * self.ch
        while self.idx < want or self.frame is None:
            buf = self.p.stdout.read(n)
            if len(buf) < n:
                break
            self.frame = np.frombuffer(buf, np.uint8).reshape(self.fh, self.fw, self.ch)
            self.idx += 1
        return self.frame

    def close(self):
        if self.p:
            self.p.kill()
            self.p.wait()
            self.p = None


def take_decoder(pool, src, t, W, H):
    """Reuse an idle decoder on the same file if it can reach t by reading forward (no new seek)."""
    ds = pool.get(src) or []
    best = None
    for d in ds:
        if d.p is not None and d.idx is not None:
            gap = t * d.fps - d.idx
            if -0.5 <= gap <= d.fps * 4 and (best is None or gap < best[0]):
                best = (gap, d)
    if best:
        ds.remove(best[1])
        return best[1]
    while len(ds) > 2:          # cap idle decoders per file
        ds.pop(0).close()
    return Decoder(src, W, H)


def covers(L, t, W, H, decoders, pool):
    """True if this layer is an opaque picture filling the whole frame at time t."""
    if L.kind not in ("video", "still", "solid") or L.blend != "Normal" or L.crop:
        return False
    if L.opacity * ramp_alpha(L, t) < 0.999:
        return False
    if L.kind == "solid":
        return True
    xf = L.xf or {}
    if xf.get("rot") or abs(xf.get("pos", (0, 0))[0]) > 1e-3 or abs(xf.get("pos", (0, 0))[1]) > 1e-3:
        return False
    if min(xf.get("scale", (1, 1))) < 0.999:
        return False
    d = decoders.get(id(L))
    if d is not None:
        return not d.alpha and d.fw >= W - 2 and d.fh >= H - 2
    a = L.asset
    if a is not None and (a.get("hasVideo") == "1"):
        name = (L.src or "").lower()
        if name.endswith(".mov") and ("4444" in name or "graphics" in name):
            return False
        # unknown alpha/size until decoded: assume a 16:9 camera file covers when it is not a graphic
        return "/graphics/" not in name and not name.endswith((".png", ".tif", ".tiff", ".psd"))
    return False


def load_still(path, W, H):
    import cv2
    im = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if im is None:
        r = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
                           capture_output=True)
        im = cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_UNCHANGED)
    if im is None:
        return None
    if im.ndim == 2:
        im = cv2.cvtColor(im, cv2.COLOR_GRAY2BGR)
    sc = min(W / im.shape[1], H / im.shape[0])
    return cv2.resize(im, (max(2, int(im.shape[1] * sc)), max(2, int(im.shape[0] * sc))), interpolation=cv2.INTER_AREA)


def place(img, L, W, H, unit):
    """Apply crop and transform; returns a full-frame BGR image and an alpha mask (0..1)."""
    import cv2
    h, w = img.shape[:2]
    alpha = None
    if img.shape[2] == 4:
        alpha = img[:, :, 3].astype(np.float32) / 255
        img = img[:, :, :3]
    x0, y0 = (W - w) / 2, (H - h) / 2  # fit, centred
    mask = np.ones((h, w), np.float32) if alpha is None else alpha
    if L.crop:
        l, t, r, b = (v * unit for v in L.crop)   # crop values are in % of frame height
        cl, ct = int(max(0, l - x0)), int(max(0, t - y0))
        cr, cb = int(max(0, (x0 + w) - (W - r))), int(max(0, (y0 + h) - (H - b)))
        mask = mask.copy()
        if cl: mask[:, :cl] = 0
        if ct: mask[:ct, :] = 0
        if cr: mask[:, w - cr:] = 0
        if cb: mask[h - cb:, :] = 0
    xf = L.xf or {"pos": (0, 0), "scale": (1, 1), "rot": 0, "anchor": (0, 0)}
    sx, sy = xf["scale"]
    px, py = xf["pos"][0] * unit, -xf["pos"][1] * unit
    rot = -math.radians(xf.get("rot") or 0)
    cx, cy = W / 2 + px, H / 2 + py
    c, s = math.cos(rot), math.sin(rot)
    # source pixel (u,v) relative to its centre -> output
    M = np.array([[sx * c, -sy * s, 0], [sx * s, sy * c, 0]], np.float64)
    M[:, 2] = [cx - (M[0, 0] * w / 2 + M[0, 1] * h / 2), cy - (M[1, 0] * w / 2 + M[1, 1] * h / 2)]
    if abs(sx - 1) < 1e-6 and abs(sy - 1) < 1e-6 and not rot and abs(px) < 1e-6 and abs(py) < 1e-6 and w == W and h == H:
        return np.ascontiguousarray(img), mask
    out = cv2.warpAffine(img, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    m = cv2.warpAffine(mask, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    return out, m


def blend(base, top, a, mode):
    """Composite `top` over `base` with per-pixel alpha `a`, only inside the area `a` actually covers."""
    import cv2
    if mode == "Normal" and a.min() >= 0.999:
        return np.array(top, copy=True) if top.shape == base.shape else base   # own copy: later layers draw on it
    rows = np.flatnonzero(a.max(axis=1) > 0.002)
    if not len(rows):
        return base
    cols = np.flatnonzero(a[rows[0]:rows[-1] + 1].max(axis=0) > 0.002)
    y0, y1, x0, x1 = rows[0], rows[-1] + 1, cols[0], cols[-1] + 1
    b = np.ascontiguousarray(base[y0:y1, x0:x1])
    t = np.ascontiguousarray(top[y0:y1, x0:x1])
    m = np.ascontiguousarray(a[y0:y1, x0:x1], dtype=np.float32)
    if mode == "Subtract":
        res = cv2.subtract(b, t)
    elif mode == "Add":
        res = cv2.add(b, t)
    elif mode == "Multiply":
        res = cv2.multiply(b, t, scale=1 / 255)
    elif mode == "Screen":
        res = 255 - cv2.multiply(255 - b, 255 - t, scale=1 / 255)
    elif mode == "Lighten":
        res = cv2.max(b, t)
    elif mode == "Darken":
        res = cv2.min(b, t)
    elif mode == "Overlay":
        bf, tf = b.astype(np.float32), t.astype(np.float32)
        res = np.where(bf < 128, 2 * bf * tf / 255, 255 - 2 * (255 - bf) * (255 - tf) / 255).astype(np.uint8)
    else:
        res = t
    base[y0:y1, x0:x1] = cv2.blendLinear(res, b, m, 1 - m)
    return base


def ramp_alpha(L, t):
    """Opacity multiplier from transitions at the layer's head (in) and tail (out)."""
    if not L.ramp:
        return 1.0
    k = 1.0
    if "in" in L.ramp:
        a, b, name = L.ramp["in"]
        if a <= t < b:
            u = (t - a) / max(1e-6, b - a)
            k *= max(0.0, (u - 0.5) * 2) if "Fade" in name else u
    if "out" in L.ramp:
        a, b, name = L.ramp["out"]
        if a <= t < b:
            u = (t - a) / max(1e-6, b - a)
            k *= max(0.0, 1 - u * 2) if "Fade" in name else 1.0
    return k


# ------------------------------------------------------------------ titles
_fonts = {}


def _typeface(family, face):
    import skia
    key = (family, face)
    if key not in _fonts:
        face_l = (face or "Regular").lower()
        weight = skia.FontStyle.kBold_Weight if "bold" in face_l else (
            skia.FontStyle.kLight_Weight if "light" in face_l else skia.FontStyle.kNormal_Weight)
        slant = skia.FontStyle.kItalic_Slant if "italic" in face_l else skia.FontStyle.kUpright_Slant
        tf = skia.FontMgr().matchFamilyStyle(family, skia.FontStyle(weight, skia.FontStyle.kNormal_Width, slant))
        _fonts[key] = tf or skia.Typeface.MakeDefault()
    return _fonts[key]


def _typeface_for(family, face, text):
    """The run's font, or a system font that has the character when the run's font lacks it (as Final Cut does)."""
    import skia
    tf = _typeface(family, face)
    missing = next((ch for ch in text if not ch.isspace() and tf.unicharToGlyph(ord(ch)) == 0), None)
    if missing is None:
        return tf
    key = ("fallback", family, face, missing)
    if key not in _fonts:
        _fonts[key] = skia.FontMgr().matchFamilyStyleCharacter(family, tf.fontStyle(), ["en"], ord(missing)) or tf
    return _fonts[key]


def _rgba(s, default=(1, 1, 1, 1)):
    v = _floats(s, 4)
    return tuple(v[:4]) if v else default


def render_title(el, W, H, cache):
    """Basic Title: text runs with their own style; centred on its position; returns BGRA (premultiplied off)."""
    import skia
    key = ET.tostring(el)
    if key in cache:
        return cache[key]
    defs = {d.get("id"): d.find("text-style") for d in el.findall("text-style-def")}
    runs = []
    for tx in el.findall("text"):
        for r in tx.findall("text-style"):
            st = defs.get(r.get("ref"))
            runs.append((r.text or "", st if st is not None else ET.Element("text-style")))
    k = H / 1080
    surf = skia.Surface(W, H)
    c = surf.getCanvas()
    c.clear(skia.Color4f(0, 0, 0, 0))
    xf = transform_of(el)
    unit = H / 100
    ox, oy = W / 2 + xf["pos"][0] * unit, H / 2 - xf["pos"][1] * unit
    # lines of runs
    lines, cur = [], []
    for text, st in runs:
        parts = text.split("\n")
        for i, p in enumerate(parts):
            if i:
                lines.append(cur)
                cur = []
            if p:
                cur.append((p, st))
    lines.append(cur)
    sizes = [max([float(st.get("fontSize", 60)) for _, st in ln] or [60]) for ln in lines]
    y = oy - sum(s * 1.2 * k for s in sizes[1:])
    for ln, size in zip(lines, sizes):
        fonts = [skia.Font(_typeface_for(st.get("font", "Helvetica"), st.get("fontFace"), t),
                           float(st.get("fontSize", 60)) * k * xf["scale"][0]) for t, st in ln]
        widths = [f.measureText(t) for (t, _), f in zip(ln, fonts)]
        align = (ln[0][1].get("alignment") if ln else "center") or "center"
        x = ox - (sum(widths) / 2 if align == "center" else (sum(widths) if align == "right" else 0))
        for (t, st), f, w in zip(ln, fonts, widths):
            col = _rgba(st.get("fontColor"))
            sh = _rgba(st.get("shadowColor"), (0, 0, 0, 0)) if st.get("shadowColor") else None
            if sh and sh[3] > 0 and col[3] > 0:
                off = _floats(st.get("shadowOffset") or "5 315", 2)
                ang = math.radians(off[1])
                dx, dy = off[0] * k * math.cos(ang), -off[0] * k * math.sin(ang)
                p = skia.Paint(AntiAlias=True, Color4f=skia.Color4f(sh[0], sh[1], sh[2], sh[3]))
                blur = float(st.get("shadowBlurRadius") or 0) * k
                if blur > 0:
                    p.setMaskFilter(skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, blur / 2))
                c.drawString(t, x + dx, y + dy, f, p)
            if col[3] > 0:
                c.drawString(t, x, y, f, skia.Paint(AntiAlias=True, Color4f=skia.Color4f(col[0], col[1], col[2], col[3])))
            x += w
        y += size * 1.2 * k
    arr = surf.makeImageSnapshot().toarray(colorType=skia.kBGRA_8888_ColorType)
    img = np.ascontiguousarray(arr[:, :, :3])
    alpha = arr[:, :, 3].astype(np.float32) / 255
    with np.errstate(invalid="ignore", divide="ignore"):
        img = np.where(alpha[:, :, None] > 0, np.clip(img / np.maximum(alpha[:, :, None], 1e-6), 0, 255), 0).astype(np.uint8)
    cache[key] = (img, alpha)
    if len(cache) > 400:
        cache.pop(next(iter(cache)))
    return cache[key]


# ------------------------------------------------------------------ audio
def mix_audio(tl, t_start, t_end, out_wav, log):
    n = int((t_end - t_start) * SR) + SR
    buf = np.zeros((n, 2), np.float32)
    used = 0
    for L in tl.audio:
        a, b = max(L.t0, t_start), min(L.t1, t_end)
        if b <= a:
            continue
        if abs(L.speed - 1) > 0.02:
            tl.skipped["retimed audio (left silent)"] += 1
            continue
        s = L.fmap(a)
        if s < 0:
            a, s = a - s, 0
        dur = b - a
        r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{s:.5f}", "-t", f"{dur:.5f}", "-i", L.src, "-vn",
                            "-ac", "2", "-ar", str(SR), "-f", "f32le", "-"], capture_output=True)
        x = np.frombuffer(r.stdout, np.float32)
        if not len(x):
            continue
        x = x.reshape(-1, 2).copy()
        times = a + np.arange(len(x)) / SR
        if L.keys:
            grid = np.linspace(a, b, 64)
            loc = np.interp(times, grid, np.array([L.local(tt) for tt in grid]))   # clip-local time of each sample
            kt = np.array([k[0] for k in L.keys])
            kv = np.array([k[1] for k in L.keys])
            gdb = L.gain + np.interp(loc, kt, kv)
        else:
            gdb = np.full(len(x), L.gain)
        g = np.power(10, np.clip(gdb, -96, 24) / 20)
        if L.ramp:   # audio crossfade follows the picture transition
            step = SR // 100
            ra = np.array([ramp_alpha(L, tt) for tt in times[::step]])
            g = g * np.repeat(ra, step)[: len(g)]
        i0 = int(round((a - t_start) * SR))
        m = min(len(x), n - i0)
        buf[i0:i0 + m] += x[:m] * g[:m, None]
        used += 1
    buf = buf[: int((t_end - t_start) * SR)]
    peak = float(np.max(np.abs(buf))) if len(buf) else 0
    Path(out_wav).write_bytes(np.ascontiguousarray(buf, np.float32).tobytes())   # raw 32-bit float stereo
    pk = 20 * math.log10(max(peak, 1e-9))
    log(f"audio: {used} sound pieces mixed, peak {pk:.1f} dBFS")
    tl.mix_peak = round(pk, 1)
    return used


# ------------------------------------------------------------------ render
def render(fcpxml, out, preset="preview", start=None, end=None, progress=None, log=print, workers=None, video_only=False):
    """Render [start, end) of the timeline. Long renders are split into chunks rendered in parallel."""
    import os
    t_begin = time.time()
    if workers is None:
        workers = max(1, min(4, (os.cpu_count() or 4) // 3))
    if workers > 1 and not video_only:
        tl0 = Timeline(fcpxml)
        a = max(0.0, float(start or 0))
        b = min(tl0.duration, float(end)) if end else tl0.duration
        if b - a > 90:
            return _render_parallel(fcpxml, out, preset, a, b, workers, tl0, progress, log, t_begin)
    tl = Timeline(fcpxml)
    H0, vcodec, acodec, ext = PRESETS[preset]
    H = H0 or tl.height
    W = int(round(tl.width * H / tl.height / 2) * 2)
    out = Path(out).with_suffix(ext)
    t_start = max(0.0, float(start or 0))
    t_end = min(tl.duration, float(end)) if end else tl.duration
    fps = tl.fps
    nframes = int(round((t_end - t_start) * fps))
    unit = H / 100.0
    log(f"{tl.project.get('name')}: {len(tl.video)} picture layers, {len(tl.audio)} sound pieces, "
        f"{nframes} frames at {W}x{H}")

    wav = out.with_suffix(".mix.f32")
    if not video_only:
        mix_audio(tl, t_start, t_end, wav, log)

    layers = sorted(tl.video, key=lambda L: L.t0)
    starts = [L.t0 for L in layers]
    decoders, stills, tcache, pool = {}, {}, {}, {}
    audio_in = [] if video_only else ["-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", str(wav)]
    audio_out = [] if video_only else [*acodec, "-shortest"]
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
                             "-r", f"{fps:g}", "-i", "-", *audio_in, *vcodec, *audio_out,
                             "-movflags", "+faststart", str(out)], stdin=subprocess.PIPE)
    active = []
    nxt = 0
    for i in range(nframes):
        t = t_start + (i + 0.5) / fps
        while nxt < len(layers) and starts[nxt] <= t:
            active.append(layers[nxt])
            nxt += 1
        alive = [L for L in active if L.t1 > t]
        for L in active:
            if L.t1 <= t and id(L) in decoders:
                pool.setdefault(L.src, []).append(decoders.pop(id(L)))   # keep it: the next cut often continues the same file
        active = alive
        frame = np.zeros((H, W, 3), np.uint8)
        stack = sorted((L for L in active if L.t0 <= t), key=lambda L: L.z)
        # occlusion: nothing below an opaque full-frame picture is visible, so don't decode it
        for j in range(len(stack) - 1, -1, -1):
            if covers(stack[j], t, W, H, decoders, pool):
                stack = stack[j:]
                break
        for L in stack:
            a = L.opacity * ramp_alpha(L, t)
            if a <= 0.002:
                continue
            if L.kind == "solid":
                img = np.empty((H, W, 3), np.uint8)
                img[:] = L.colour[::-1]
                frame = blend(frame, img, np.full((H, W), a, np.float32), L.blend)
                continue
            if L.kind == "title":
                img, m = render_title(L.el, W, H, tcache)
                frame = blend(frame, img, m * a, L.blend)
                continue
            if L.kind == "still":
                if L.src not in stills:
                    stills[L.src] = load_still(L.src, W, H)
                src = stills[L.src]
            else:
                d = decoders.get(id(L))
                if d is None:
                    d = decoders[id(L)] = take_decoder(pool, L.src, max(0.0, L.fmap(t)), W, H)
                src = d.at(max(0.0, L.fmap(t)))
            if src is None:
                continue
            if L.cc:
                src = apply_cc(src, L.cc)
            img, m = place(src, L, W, H, unit)
            frame = blend(frame, img, m * a, L.blend)
        proc.stdin.write(frame.tobytes())
        if progress and i % 25 == 0:
            progress(i / max(1, nframes))
    proc.stdin.close()
    proc.wait()
    for d in list(decoders.values()) + [d for ds in pool.values() for d in ds]:
        d.close()
    wav.unlink(missing_ok=True)
    secs = time.time() - t_begin
    report = {"output": str(out), "preset": preset, "size": f"{W}x{H}", "frames": nframes, "seconds": round(secs, 1),
              "start": t_start, "end": t_end, "notReproduced": dict(tl.skipped.most_common()),
              "mixPeakDBFS": getattr(tl, "mix_peak", None)}
    if (getattr(tl, "mix_peak", None) or -99) > 0:
        report["warning"] = (f"The timeline's sound mix peaks at +{tl.mix_peak} dBFS, above full scale, so it will distort. "
                             "Choose Broadcast or Social loudness, or lower the levels in Final Cut.")
    Path(str(out) + ".json").write_text(json.dumps(report, indent=1))
    log(f"done in {secs / 60:.1f} min → {out}")
    return report


def _render_parallel(fcpxml, out, preset, a, b, workers, tl, progress, log, t_begin):
    import sys
    H0, vcodec, acodec, ext = PRESETS[preset]
    out = Path(out).with_suffix(ext)
    fps = tl.fps
    frames = int(round((b - a) * fps))
    edges = [a + round(frames * k / workers) / fps for k in range(workers + 1)]
    tmp = out.parent / (out.stem + ".parts")
    tmp.mkdir(exist_ok=True)
    log(f"rendering {frames} frames in {workers} parallel parts")
    wav = out.with_suffix(".mix.f32")
    mix_audio(tl, a, b, wav, log)
    procs = []
    for k in range(workers):
        part = tmp / f"part{k}{ext}"
        procs.append((subprocess.Popen([sys.executable, "-m", "dmv.render", str(fcpxml), str(part), "--preset", preset,
                                        "--start", repr(edges[k]), "--end", repr(edges[k + 1]), "--video-only",
                                        "--workers", "1"], cwd=str(Path(__file__).resolve().parent.parent),
                                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE), part))
    while any(p.poll() is None for p, _ in procs):
        time.sleep(2)
        if progress:
            done = sum(Path(str(pt) + ".progress").read_text().strip() and float(Path(str(pt) + ".progress").read_text())
                       if Path(str(pt) + ".progress").exists() else 0 for _, pt in procs)
            progress(done / workers)
    for p, part in procs:
        if p.returncode != 0 or not part.exists():
            raise RuntimeError(f"part {part.name} failed: {p.stderr.read().decode()[-400:]}")
    lst = tmp / "list.txt"
    lst.write_text("".join(f"file '{pt}'\n" for _, pt in procs))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-f", "f32le", "-ar", str(SR),
                    "-ac", "2", "-i", str(wav), "-map", "0:v", "-map", "1:a", "-c:v", "copy", *acodec, "-shortest",
                    "-movflags", "+faststart", str(out)], check=True)
    skipped = Counter()
    for _, pt in procs:
        rj = Path(str(pt) + ".json")
        if rj.exists():
            skipped.update(json.loads(rj.read_text()).get("notReproduced", {}))
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    wav.unlink(missing_ok=True)
    secs = time.time() - t_begin
    W = int(round(tl.width * (H0 or tl.height) / tl.height / 2) * 2)
    report = {"output": str(out), "preset": preset, "size": f"{W}x{H0 or tl.height}", "frames": frames,
              "seconds": round(secs, 1), "start": a, "end": b, "parts": workers,
              "notReproduced": {k: v // workers or 1 for k, v in skipped.most_common()},
              "mixPeakDBFS": getattr(tl, "mix_peak", None)}
    if (getattr(tl, "mix_peak", None) or -99) > 0:
        report["warning"] = (f"The timeline's sound mix peaks at +{tl.mix_peak} dBFS, above full scale, so it will distort. "
                             "Choose Broadcast or Social loudness, or lower the levels in Final Cut.")
    Path(str(out) + ".json").write_text(json.dumps(report, indent=1))
    log(f"done in {secs / 60:.1f} min → {out}")
    return report


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("fcpxml")
    ap.add_argument("out")
    ap.add_argument("--preset", default="preview", choices=list(PRESETS))
    ap.add_argument("--start", type=float)
    ap.add_argument("--end", type=float)
    ap.add_argument("--workers", type=int)
    ap.add_argument("--video-only", action="store_true")
    a = ap.parse_args()
    prog_file = Path(str(Path(a.out).with_suffix(PRESETS[a.preset][3])) + ".progress")
    def _p(x):
        prog_file.write_text(f"{x:.4f}")
    print(json.dumps(render(a.fcpxml, a.out, a.preset, a.start, a.end, progress=_p, workers=a.workers,
                            video_only=a.video_only), indent=1))
