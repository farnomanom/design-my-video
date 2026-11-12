"""Self-contained HTML report from analysis.json + review.json."""
import base64
import html
import json
from pathlib import Path

from .timecode import mmss, tc

SEV = {"high": ("High impact", "#e5484d"), "medium": ("Medium", "#f5a524"), "low": ("Polish", "#3e9b4f")}


def _img(path, width=None):
    try:
        data = base64.b64encode(Path(path).read_bytes()).decode()
    except OSError:
        return ""
    return f'<img src="data:image/jpeg;base64,{data}" alt="" loading="lazy">'


def _frame_for(analysis, t, folder):
    shots = analysis.get("shots") or []
    for s in shots:
        if s["start"] <= t < s["end"] and s.get("frame"):
            return folder / s["frame"]
    return None


def build(analysis_dir):
    d = Path(analysis_dir)
    a = json.loads((d / "analysis.json").read_text())
    r = json.loads((d / "review.json").read_text())
    fps = (a.get("video") or {}).get("fps") or (a.get("timeline") or {}).get("fps") or 25
    e = html.escape
    score = r["overall_score"]
    ring = 2 * 3.14159 * 54
    cats = "".join(
        f'<div class="cat"><div class="cat-h"><span>{e(c["category"])}</span><b>{c["score"]}/{c["max"]}</b></div>'
        f'<div class="bar"><i style="width:{100 * c["score"] / max(1, c["max"]):.0f}%"></i></div>'
        f'<p>{e(c["rationale"])}</p></div>'
        for c in r["categories"]
    )
    sugg = []
    for s in r["suggestions"]:
        label, colour = SEV.get(s["severity"], ("", "#888"))
        frame = _frame_for(a, s["start_seconds"], d)
        sugg.append(
            f'<article class="s" id="{e(s["id"])}">'
            f'<div class="thumb">{_img(frame) if frame else "<div class=noimg>No frame</div>"}</div>'
            f'<div class="body"><div class="meta"><span class="id">{e(s["id"])}</span>'
            f'<span class="tc">{tc(s["start_seconds"], fps)} → {tc(s["end_seconds"], fps)}</span>'
            f'<span class="sev" style="--c:{colour}">{label}</span><span class="cat-tag">{e(s["category"])}</span>'
            f'<span class="gain">+{s["expected_gain"]} pts</span></div>'
            f'<h3>{e(s["suggestion"])}</h3><p class="issue">{e(s["issue"])}</p>'
            f'<details><summary>How to do it in Final Cut</summary><p>{e(s["how_in_final_cut"])}</p></details></div></article>'
        )
    strengths = "".join(f"<li>{e(x)}</li>" for x in r["strengths"])
    potential = min(100, score + sum(s["expected_gain"] for s in r["suggestions"]))
    name = e(a["name"])
    meta = a.get("video") or {}
    sub = f'{mmss(meta.get("duration", 0))} · {meta.get("width")}×{meta.get("height")}' if meta else "Timeline only"

    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{name} · Design review</title>
<style>
:root{{--bg:#f6f5f2;--card:#fff;--ink:#1b1b1d;--mut:#6b6b70;--line:#e4e2dd;--acc:#c2410c}}
@media (prefers-color-scheme:dark){{:root{{--bg:#121214;--card:#1c1c1f;--ink:#ececee;--mut:#9a9aa2;--line:#2c2c31;--acc:#fb923c}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,BlinkMacSystemFont,"Helvetica Neue",sans-serif}}
main{{max-width:1080px;margin:0 auto;padding:32px 16px 64px}}
header{{display:flex;gap:28px;align-items:center;flex-wrap:wrap;margin-bottom:28px}}
.ring{{position:relative;width:132px;height:132px;flex:none}}.ring svg{{transform:rotate(-90deg)}}
.ring b{{position:absolute;inset:0;display:grid;place-items:center;font-size:38px;font-weight:700}}
.ring small{{position:absolute;bottom:26px;width:100%;text-align:center;color:var(--mut);font-size:12px}}
h1{{margin:0 0 4px;font-size:28px}}.sub{{color:var(--mut)}}.verdict{{max-width:640px;margin:10px 0 0}}
.pill{{display:inline-block;margin-top:8px;padding:3px 10px;border-radius:99px;background:var(--line);font-size:12px}}
section{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:20px;margin:18px 0}}
h2{{margin:0 0 14px;font-size:18px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:18px}}
.cat-h{{display:flex;justify-content:space-between;font-weight:600}}.bar{{height:8px;background:var(--line);border-radius:9px;overflow:hidden;margin:6px 0}}
.bar i{{display:block;height:100%;background:var(--acc)}}.cat p{{margin:0;color:var(--mut);font-size:13.5px}}
.s{{display:grid;grid-template-columns:260px 1fr;gap:18px;padding:16px 0;border-top:1px solid var(--line)}}.s:first-of-type{{border-top:0}}
.thumb img{{width:100%;border-radius:8px;display:block}}.noimg{{aspect-ratio:16/9;border-radius:8px;background:var(--line);display:grid;place-items:center;color:var(--mut);font-size:12px}}
.meta{{display:flex;flex-wrap:wrap;gap:8px;font-size:12px;align-items:center}}.id{{font-weight:700}}.tc{{font-family:ui-monospace,Menlo,monospace;color:var(--mut)}}
.sev{{color:#fff;background:var(--c);padding:1px 8px;border-radius:99px}}.cat-tag{{border:1px solid var(--line);padding:0 8px;border-radius:99px}}.gain{{color:var(--acc);font-weight:600}}
.s h3{{margin:8px 0 4px;font-size:16px}}.issue{{margin:0 0 6px;color:var(--mut)}}details summary{{cursor:pointer;color:var(--acc);font-size:13.5px}}
ul{{margin:0;padding-left:20px}}
@media (max-width:640px){{.s{{grid-template-columns:1fr}}}}
</style></head><body><main>
<header><div class="ring"><svg width="132" height="132"><circle cx="66" cy="66" r="54" stroke="var(--line)" stroke-width="12" fill="none"/>
<circle cx="66" cy="66" r="54" stroke="var(--acc)" stroke-width="12" fill="none" stroke-linecap="round"
stroke-dasharray="{ring:.1f}" stroke-dashoffset="{ring * (1 - score / 100):.1f}"/></svg><b>{score}</b><small>out of 100</small></div>
<div><h1>{name}</h1><div class="sub">{sub} · reviewed by {e(r.get("providerLabel", ""))}</div>
<p class="verdict">{e(r["verdict"])}</p><span class="pill">Potential after suggestions: ~{potential}/100</span></div></header>
<section><h2>Tone &amp; mood</h2><p>{e(r["tone_and_mood"])}</p><h2 style="margin-top:16px">Keep</h2><ul>{strengths}</ul></section>
<section><h2>Scores</h2><div class="grid">{cats}</div></section>
<section><h2>Suggestions ({len(r["suggestions"])})</h2>{"".join(sugg)}</section>
</main></body></html>"""
    out = d / "report.html"
    out.write_text(page)
    return out
