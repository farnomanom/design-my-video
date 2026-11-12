"""Write a copy of the FCPXML with one to-do marker per suggestion, for import into Final Cut Pro.

Markers go on the primary-storyline element under each suggestion's start time, at the matching
clip-local time snapped to the frame grid. The original file is never modified.
"""
import copy
import re
import subprocess
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

from .fcpxml import resolve_path
from .timecode import fcp_time

AFTER_MARKERS = {"filter-video", "filter-video-mask", "filter-audio", "metadata", "audio-channel-source",
                 "audio-role-source"}
SPINE_KINDS = {"asset-clip", "clip", "video", "gap", "ref-clip", "sync-clip", "mc-clip", "title", "audio"}


def _rational(sec, frame_dur):
    frames = round(Fraction(sec) / frame_dur)
    v = frames * frame_dur
    return f"{v.numerator}/{v.denominator}s" if v.denominator != 1 else f"{v.numerator}s"


def _insert_marker(elem, marker):
    """DTD order inside a clip: timing/adjustments, anchored items, markers, then filters/metadata."""
    kids = list(elem)
    idx = next((i for i, k in enumerate(kids) if k.tag in AFTER_MARKERS), len(kids))
    elem.insert(idx, marker)


def export(fcpxml_path, suggestions, out_path, project_suffix=" – Design review"):
    src = resolve_path(fcpxml_path)
    tree = ET.parse(src)
    root = tree.getroot()
    fmts = {f.get("id"): f for f in root.iter("format")}
    project = next(root.iter("project"))
    seq = project.find("sequence")
    frame_dur = fcp_time(fmts.get(seq.get("format"), {}).get("frameDuration", "1/25s")) or Fraction(1, 25)
    spine = seq.find("spine")
    items = []
    for el in spine:
        if el.tag in SPINE_KINDS:
            start_tl = fcp_time(el.get("offset"))
            items.append((el, start_tl, start_tl + fcp_time(el.get("duration")), fcp_time(el.get("start"))))
    placed, skipped = 0, []
    for s in suggestions:
        t = Fraction(str(round(s["start_seconds"], 3)))
        host = next(((el, a, b, st) for el, a, b, st in items if a <= t < b), None)
        if host is None:
            skipped.append(s["id"])
            continue
        el, a, _, st = host
        marker = ET.Element("marker", {
            "start": _rational(st + (t - a), frame_dur),
            "duration": _rational(frame_dur, frame_dur),
            "value": f'{s["id"]} [{s["severity"].upper()}] {s["category"]}: {s["suggestion"]}'[:250],
            "completed": "0",
        })
        _insert_marker(el, marker)
        placed += 1
    project.set("name", (project.get("name") or "Project") + project_suffix)
    for attr in ("uid", "modDate", "id"):
        project.attrib.pop(attr, None)
    out_path = Path(out_path)
    ET.indent(tree, space="    ")
    xml = ET.tostring(root, encoding="unicode")
    out_path.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n\n' + xml + "\n")
    return {"path": str(out_path), "placed": placed, "skipped": skipped}


def find_dtd(version):
    for app in Path("/Applications").glob("Final Cut Pro*.app"):
        p = app / "Contents/Frameworks/Interchange.framework/Versions/A/Resources" / f"FCPXMLv{version.replace('.', '_')}.dtd"
        if p.exists():
            return p
    return None


def validate(path):
    """Validate against Final Cut's own DTD when Final Cut is installed. Returns (ok, message)."""
    version = ET.parse(path).getroot().get("version", "1.11")
    dtd = find_dtd(version)
    if not dtd:
        return None, "Final Cut Pro DTD not found; skipped validation."
    # xmllint reads the DTD argument as a URL, so paths with spaces must be file:// URIs.
    r = subprocess.run(["xmllint", "--noout", "--dtdvalid", Path(dtd).as_uri(), str(path)], capture_output=True, text=True)
    msg = (r.stderr or "").strip()
    return r.returncode == 0, re.sub(r"\n+", "\n", msg)[-1500:] or "Valid"
