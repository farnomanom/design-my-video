"""Parse a Final Cut Pro FCPXML (or .fcpxmld bundle) into a flat timeline of items.

Timeline position of a child = parent timeline position + (child.offset - parent.start).
Retimed clips (timeMap) are placed by their container offsets; their internal speed
changes are not modelled.
"""
import collections
import xml.etree.ElementTree as ET
from pathlib import Path

from .timecode import fcp_time

TIMED = {
    "asset-clip", "clip", "video", "audio", "title", "caption", "gap",
    "transition", "ref-clip", "sync-clip", "mc-clip", "spine",
}
VISUAL_KINDS = {"asset-clip", "clip", "video", "ref-clip", "sync-clip", "mc-clip"}


def resolve_path(path):
    p = Path(path)
    if p.is_dir() and p.suffix == ".fcpxmld":
        p = p / "Info.fcpxml"
    return p


def _text_of(elem):
    blocks = ["".join(ts.text or "" for ts in t.iter("text-style")) for t in elem.findall("text")]
    text = " ".join(b for b in blocks if b.strip()) or (elem.get("name") or "")
    return " ".join(text.replace("█", "").split())


def parse(path):
    path = resolve_path(path)
    root = ET.parse(path).getroot()
    res = root.find("resources")
    assets, effects, formats, media = {}, {}, {}, {}
    for r in res:
        rid = r.get("id")
        if r.tag == "asset":
            rep = r.find("media-rep")
            assets[rid] = {
                "name": r.get("name"),
                "hasVideo": r.get("hasVideo") == "1",
                "hasAudio": r.get("hasAudio") == "1",
                "src": rep.get("src") if rep is not None else None,
            }
        elif r.tag == "effect":
            effects[rid] = {"name": r.get("name"), "uid": r.get("uid")}
        elif r.tag == "format":
            formats[rid] = dict(r.attrib)
        elif r.tag == "media":
            media[rid] = {"name": r.get("name")}

    project = next(root.iter("project"))
    seq = project.find("sequence")
    fmt = formats.get(seq.get("format"), {})
    frame_dur = fcp_time(fmt.get("frameDuration", "1/25s"))
    fps = float(1 / frame_dur) if frame_dur else 25.0

    items = []

    def walk(container, cont_tl, cont_start, cont_lane, parent_enabled):
        for child in container:
            if child.tag not in TIMED:
                continue
            tl = cont_tl + fcp_time(child.get("offset")) - cont_start
            lane = cont_lane + int(child.get("lane")) if child.get("lane") else cont_lane
            enabled = parent_enabled and child.get("enabled", "1") != "0"
            if child.tag == "spine":
                walk(child, tl, 0, lane, enabled)
                continue
            ref = child.get("ref")
            asset = assets.get(ref)
            item = {
                "kind": child.tag,
                "name": child.get("name"),
                "start": float(tl),
                "duration": float(fcp_time(child.get("duration"))),
                "lane": lane,
                "enabled": enabled,
            }
            if asset:
                item["hasVideo"] = asset["hasVideo"]
                item["hasAudio"] = asset["hasAudio"]
                item["asset"] = asset["name"]
            elif ref in effects:
                item["effect"] = effects[ref]["name"]
            elif ref in media:
                item["compound"] = media[ref]["name"]
            if child.get("audioRole"):
                item["audioRole"] = child.get("audioRole")
            fx = [f.get("name") for f in child.findall("filter-video")]
            if fx:
                item["filters"] = fx
            afx = [f.get("name") for f in child.findall("filter-audio")]
            if afx:
                item["audioFilters"] = afx
            vol = child.find("adjust-volume")
            if vol is not None:
                item["volume"] = vol.get("amount", "0dB")
                if vol.find(".//keyframe") is not None:
                    item["volumeKeyframed"] = True
            if child.find("adjust-blend") is not None:
                item["blend"] = child.find("adjust-blend").attrib
            if child.find("timeMap") is not None:
                item["retimed"] = True
            if child.tag in ("title", "caption"):
                item["text"] = _text_of(child)
            items.append(item)
            walk(child, tl, fcp_time(child.get("start")), lane, enabled)

    walk(seq.find("spine"), 0, 0, 0, True)
    items.sort(key=lambda i: (i["start"], i["lane"]))

    return {
        "file": str(path),
        "project": project.get("name"),
        "version": root.get("version"),
        "duration": float(fcp_time(seq.get("duration"))),
        "fps": fps,
        "width": int(fmt.get("width", 0) or 0),
        "height": int(fmt.get("height", 0) or 0),
        "items": items,
    }


def is_visual(item):
    if item["kind"] not in VISUAL_KINDS:
        return False
    if item["kind"] == "asset-clip" and not item.get("hasVideo", True):
        return False
    return item["lane"] >= 0


def dedupe_captions(items):
    """Typewriter-style captions repeat with growing text; keep the final form of each line."""
    caps = [i for i in items if i["kind"] == "caption" and i.get("text")]
    caps.sort(key=lambda c: c["start"])
    out = []
    for c in caps:
        if out and c["text"].startswith(out[-1]["text"]) and c["start"] - (out[-1]["start"] + out[-1]["duration"]) < 0.5:
            out[-1] = {**c, "start": out[-1]["start"], "duration": c["start"] + c["duration"] - out[-1]["start"]}
        else:
            out.append(dict(c))
    return out


def summarize(tl):
    items = tl["items"]
    enabled = [i for i in items if i["enabled"]]
    visual = [i for i in enabled if is_visual(i)]
    cuts = sorted({round(i["start"], 2) for i in visual} | {round(i["start"] + i["duration"], 2) for i in visual})
    cuts = [c for c in cuts if 0 < c < tl["duration"] - 0.05]
    transitions = collections.Counter(i["name"] for i in enabled if i["kind"] == "transition")
    filters = collections.Counter(f for i in enabled for f in i.get("filters", []))
    titles = [
        {"start": i["start"], "duration": i["duration"], "name": i["name"], "text": i.get("text", ""), "lane": i["lane"]}
        for i in enabled if i["kind"] == "title"
    ]
    audio = [i for i in enabled if i.get("audioRole") or (i["kind"] == "asset-clip" and not i.get("hasVideo", True))]
    music_like = [i for i in audio if i["lane"] < 0 and i.get("asset")]
    return {
        "project": tl["project"],
        "duration": tl["duration"],
        "fps": tl["fps"],
        "resolution": f'{tl["width"]}x{tl["height"]}',
        "counts": dict(collections.Counter(i["kind"] for i in items)),
        "disabledItems": sum(1 for i in items if not i["enabled"]),
        "lanes": sorted({i["lane"] for i in items}),
        "editPoints": cuts,
        "transitions": dict(transitions.most_common()),
        "videoFilters": dict(filters.most_common()),
        "generators": dict(collections.Counter(i.get("effect") for i in enabled if i["kind"] == "video" and i.get("effect")).most_common()),
        "titles": titles,
        # Captions are often disabled (hidden) in the timeline but still hold the transcript.
        "captions": dedupe_captions(items),
        "audioClips": [
            {k: i.get(k) for k in ("start", "duration", "name", "lane", "volume", "volumeKeyframed", "audioRole")}
            for i in music_like
        ],
        "retimedClips": sum(1 for i in enabled if i.get("retimed")),
    }
