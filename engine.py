#!/usr/bin/env python
"""Design My Video engine: one command-line entry point used by the Mac app.

  engine.py analyze --video V --fcpxml F [--end S] [--name N] [--out DIR] [--whisper small]
  engine.py review DIR [--provider auto|claude_code|anthropic|openai|gemini|ollama] [--model M]
  engine.py report DIR
  engine.py markers DIR --fcpxml F [--ids S01,S03]
  engine.py all --video V --fcpxml F [...]       analyze + review + report + markers
  engine.py plan DIR (--ids S01,S03 | --command "…") [--base latest|original|PATH] [--provider P]
  engine.py apply DIR [--ops 0,2,5]              apply the approved ops of DIR/plan.json as a new version
  engine.py versions DIR                         list edited versions
  engine.py fillers DIR [--base latest|original|PATH]   find um/uh/er… and plan cuts for them (plan.json)
  engine.py clips find --video V [--count 10 --min 20 --max 60 --aspect 9:16 --style bold --layout fill
                         --prompt "…" --whisper small --provider auto] [--no-render]   new clip project
  engine.py clips render DIR [--ids c01,c02] [--style …] [--aspect …] [--layout …] [--hook 0|1]
  engine.py clips export DIR --dest FOLDER [--ids c01] [--quality original|small]   save finished clips
  engine.py render DIR [--base …] [--preset preview|hd|hevc4k|prores] [--start S --end E]
                       [--loudness none|broadcast|social]   render a version to a video file
  engine.py log --src DIR [--src DIR] --name N [--whisper small]   media log of original footage
  engine.py search LOGDIR "query"                full-text search of a media log (speech + names)
  engine.py selftest                             check every part of the engine works on this Mac (JSON)
  engine.py providers                            which AI reviewers are available (JSON)
  engine.py set-key PROVIDER                     store an API key from stdin in the Keychain
  engine.py delete-key PROVIDER

Set DMV_PROGRESS_STDOUT=1 for JSON-lines progress on stdout.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def emit(event, **kw):
    print(json.dumps({"event": event, **kw}), flush=True)


def record_timing(d, key, seconds):
    """Keep how long each step took (timings.json in the analysis folder) so the app can show it."""
    import datetime

    f = Path(d) / "timings.json"
    try:
        t = json.loads(f.read_text()) if f.exists() else {}
    except Exception:
        t = {}
    t[key] = {"seconds": round(seconds, 1), "at": datetime.datetime.now().isoformat(timespec="seconds")}
    f.write_text(json.dumps(t, indent=1))
    emit("timing", step=key, seconds=round(seconds, 1))


def cmd_review(a):
    from dmv import review
    from dmv.progress import Task

    import time

    task = Task(f"Reviewing “{Path(a.dir).name}”")
    t0 = time.time()
    try:
        r = review.run(a.dir, a.provider, a.model, task=task)
        record_timing(a.dir, "review", time.time() - t0)
    except Exception as e:
        task.fail(str(e)[:200])
        emit("error", message=str(e))
        sys.exit(1)
    task.done(f"Score {r['overall_score']}/100 · {len(r['suggestions'])} suggestions")
    emit("review", score=r["overall_score"], suggestions=len(r["suggestions"]), provider=r["provider"])


def cmd_report(a):
    from dmv import report

    p = report.build(a.dir)
    emit("report", path=str(p))


def cmd_markers(a):
    from dmv import markers

    r = json.loads((Path(a.dir) / "review.json").read_text())
    sugg = r["suggestions"]
    if a.ids:
        keep = set(a.ids.split(","))
        sugg = [s for s in sugg if s["id"] in keep]
    name = Path(a.dir).name
    out = Path(a.dir) / f"{name} – Design review markers.fcpxml"
    res = markers.export(a.fcpxml, sugg, out)
    ok, msg = markers.validate(out)
    res.update(valid=ok, validation=msg)
    emit("markers", **res)
    if ok is False:
        sys.exit(1)


def original_fcpxml(d, given=None):
    if given:
        return given
    try:
        f = json.loads((Path(d) / "analysis.json").read_text())["timeline"]["file"]
    except Exception:
        f = None
    if not f or not Path(f).exists():
        raise RuntimeError("The original FCPXML for this analysis wasn't found. Pass --fcpxml.")
    return f


def list_versions(d):
    from dmv import edit

    out = []
    for log in sorted((Path(d) / "versions").glob("v*.json"), key=lambda p: int(p.stem[1:])):
        info = json.loads(log.read_text())
        if Path(info["path"]).exists():
            out.append({k: info.get(k) for k in ("version", "path", "created", "summary", "applied", "skipped", "valid", "base",
                                                 "overwroteOriginal", "seconds")})
    return out


def resolve_base(d, base, fcpxml=None):
    if base and base not in ("latest", "original"):
        return base
    vs = list_versions(d)
    vs = [v for v in vs if not v.get("overwroteOriginal")]
    if base == "latest" and vs:
        return vs[-1]["path"]
    return original_fcpxml(d, fcpxml)


def cmd_plan(a):
    from dmv import planner
    from dmv.progress import Task

    import time

    task = Task(f"Planning edits for “{Path(a.dir).name}”")
    t0 = time.time()
    try:
        base = resolve_base(a.dir, a.base, a.fcpxml)
        sugg = None
        if a.ids:
            keep = set(a.ids.split(","))
            sugg = [s for s in json.loads((Path(a.dir) / "review.json").read_text())["suggestions"] if s["id"] in keep]
            if not sugg:
                raise RuntimeError("None of those suggestions were found in the review.")
        elif not a.command:
            raise RuntimeError("Give --ids or --command.")
        p = planner.plan(a.dir, base, suggestions=sugg, command=a.command, provider=a.provider, model=a.model, task=task)
        record_timing(a.dir, "plan", time.time() - t0)
    except Exception as e:
        task.fail(str(e)[:200])
        emit("error", message=str(e))
        sys.exit(1)
    ok = sum(1 for o in p["ops"] if o["check"] == "applied")
    task.done(f"{len(p['ops'])} edits planned · {ok} ready")
    emit("plan", path=str(Path(a.dir) / "plan.json"), ops=len(p["ops"]), ready=ok)


def cmd_apply(a):
    import datetime
    import time

    from dmv import edit, markers, planner

    t0 = time.time()
    d = Path(a.dir)
    p = json.loads((d / "plan.json").read_text())
    base = p["base"]
    if not Path(base).exists():
        emit("error", message="The timeline this plan was made for is missing.")
        sys.exit(1)
    if edit.sha1(base) != p["baseSha"]:
        emit("error", message="The timeline changed since this plan was made. Plan the edits again.")
        sys.exit(1)
    idx = [int(i) for i in a.ops.split(",")] if a.ops else list(range(len(p["ops"])))
    ops = [p["ops"][i] for i in idx if 0 <= i < len(p["ops"])]
    if not ops:
        emit("error", message="No edits selected.")
        sys.exit(1)
    doc = edit.Doc(base)
    speech = [(s, e) for s, e, _ in planner.speech_segments(d)]
    results = edit.apply_ops(doc, ops, speech)
    applied = [r for r in results if r["status"] == "applied"]
    if not applied:
        emit("error", message="None of the selected edits could be applied: " + "; ".join(r["detail"] for r in results)[:400])
        sys.exit(1)
    vdir = d / "versions"
    vdir.mkdir(exist_ok=True)
    n = max([int(x.stem[1:]) for x in vdir.glob("v*.json")] or [1]) + 1
    # Always build and check the edited timeline in a new file first.
    name = f"{edit.base_name(doc.project.get('name'))} – v{n}"
    out = vdir / f"{name}.fcpxml"
    original = edit.resolve_path(original_fcpxml(d))
    if a.overwrite:
        doc.write(out, doc.project.get("name"), keep_identity=True)
    else:
        doc.write(out, name)
    valid, msg = markers.validate(out)
    if valid is False:
        out.rename(out.with_suffix(".invalid.fcpxml"))
        emit("error", message=f"The edited timeline did not pass Final Cut's format check, so nothing was saved. {msg[:300]}")
        sys.exit(1)
    if a.overwrite:
        if a.overwrite != "CONFIRMED":
            out.unlink()
            emit("error", message="Overwriting the original needs both confirmations.")
            sys.exit(1)
        # Replace the original file in place. No copy of the old contents is kept.
        original.write_bytes(out.read_bytes())
        out.unlink()
        out = original
    seconds = time.time() - t0
    log = {"version": n, "path": str(out), "created": datetime.datetime.now().isoformat(timespec="seconds"),
           "summary": p.get("summary"), "command": p.get("command"), "base": base, "overwroteOriginal": bool(a.overwrite),
           "applied": len(applied), "skipped": len(results) - len(applied), "valid": valid, "seconds": round(seconds, 1),
           "ops": [{**op, "result": r} for op, r in zip(ops, results)]}
    (vdir / f"v{n}.json").write_text(json.dumps(log, indent=1, ensure_ascii=False))
    record_timing(d, "apply", seconds)
    emit("version", version=n, path=str(out), applied=len(applied), skipped=len(results) - len(applied), valid=valid,
         overwroteOriginal=bool(a.overwrite), results=results)


def cmd_versions(a):
    emit("versions", versions=list_versions(a.dir))


def cmd_providers(_a):
    from dmv import providers

    emit("providers", providers=providers.status())


def cmd_set_key(a):
    from dmv import keychain

    key = sys.stdin.readline().strip()
    if not key:
        emit("error", message="No key given")
        sys.exit(1)
    keychain.set_key(a.provider, key)
    emit("key", provider=a.provider, stored=True)


def cmd_delete_key(a):
    from dmv import keychain

    keychain.delete_key(a.provider)
    emit("key", provider=a.provider, stored=False)


def analyze_argv(a):
    argv = []
    for flag in ("video", "fcpxml", "out", "name", "end", "whisper", "language"):
        v = getattr(a, flag, None)
        if v is not None:
            argv += [f"--{flag}", str(v)]
    if getattr(a, "no_transcribe", False):
        argv.append("--no-transcribe")
    return argv


def run_analyze(a):
    import analyze

    import time

    sys.argv = ["analyze.py"] + analyze_argv(a)
    t0 = time.time()
    out = analyze.main()
    record_timing(out, "analysis", time.time() - t0)
    emit("analysis", dir=str(out))
    return out


def cmd_all(a):
    out = run_analyze(a)
    a.dir = str(out)
    cmd_review(a)
    cmd_report(a)
    if a.fcpxml:
        cmd_markers(argparse.Namespace(dir=a.dir, fcpxml=a.fcpxml, ids=None))


def cmd_log(a):
    import time

    from dmv import medialog
    from dmv.progress import Task

    out = Path(a.out).expanduser() if a.out else Path.home() / "Movies" / "Design My Video" / "Media Logs" / a.name
    out.mkdir(parents=True, exist_ok=True)
    task = Task(f"Logging media for “{a.name}”")
    t0 = time.time()
    try:
        r = medialog.log_folder(a.src, out, a.whisper, task=task, emit=emit)
    except Exception as e:
        task.fail(str(e)[:200])
        emit("error", message=str(e))
        sys.exit(1)
    s = medialog.summary(out)
    record_timing(out, "log", time.time() - t0)
    task.done(f"{s['clips']} clips · {s['shots']} shots · {s['speechMinutes']} min of speech")
    emit("log", dir=str(out), **r, **s)


SPEECH_ROLE_WORDS = ("dialogue", "vocal", "voice", "speech", "interview", "narration")


def cmd_fillers(a):
    """Verbatim transcript of the timeline's own dialogue, then a cut for every filler word."""
    import hashlib
    import subprocess
    import tempfile
    import time

    from dmv import edit, recut, render, verbatim
    from dmv.progress import Task

    task = Task(f"Finding filler words in “{Path(a.dir).name}”")
    t0 = time.time()
    try:
        base = resolve_base(a.dir, a.base)
        doc = edit.Doc(base)
        film_end = float(edit.fcp_time(doc.seq.get("duration")))
        tl = render.Timeline(base)
        def is_speech(L):
            roles = " ".join(filter(None, [L.el.get("audioRole")] + [s.get("role") or "" for s in L.el.findall("audio-channel-source")])).lower()
            return any(w in roles for w in SPEECH_ROLE_WORDS)
        speech = [L for L in tl.audio if is_speech(L)]
        # dialogue-tagged music is common; prefer stems named like vocals when present
        vocal = [L for L in speech if "vocal" in (L.src or "").lower() or "vocal" in (L.name or "").lower()]
        if vocal:
            speech = vocal
        if speech:
            tl.audio = speech
        key = hashlib.sha1((edit.sha1(edit.resolve_path(base)) + "v1").encode()).hexdigest()[:10]
        cache = Path(a.dir) / f"verbatim_{key}.json"
        if cache.exists():
            vb = json.loads(cache.read_text())
        else:
            task.update(percent=3, note="Mixing the dialogue…", force=True)
            with tempfile.TemporaryDirectory() as tmp:
                raw, wav = Path(tmp) / "mix.f32", Path(tmp) / "mix.wav"
                render.mix_audio(tl, 0.0, film_end, raw, lambda *x: None)
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(render.SR), "-ac", "2", "-i", str(raw),
                                "-ac", "1", "-ar", "16000", str(wav)], check=True)
                task.update(percent=6, note="Transcribing every word as spoken (large model, slow on this Mac)…", force=True)
                vb = verbatim.transcribe(wav, report=lambda f, note: task.update(percent=6 + 88 * f, note=note))
            cache.write_text(json.dumps(vb))
        ops = recut.filler_ops(vb, film_end)
        task.update(percent=95, note="Checking each cut against the timeline…", force=True)
        trial = edit.Doc(base)
        checks = edit.apply_ops(trial, ops)
        for op, c in zip(ops, checks):
            op["check"], op["detail"] = c["status"], c["detail"]
        fill = [w for w in vb.get("words", []) if w.get("filler")]
        plan = {"summary": f"Removes {len(ops)} filler words (um, uh, er…) found in a word-for-word transcript of the dialogue. "
                           f"Each cut closes the gap; picture, music and captions after it move up together.",
                "ops": ops, "not_possible": ([f"{len(fill) - len(ops)} fillers were too short or too tight to cut cleanly; left in."]
                                             if len(fill) > len(ops) else []),
                "base": str(edit.resolve_path(base)), "baseSha": edit.sha1(edit.resolve_path(base)),
                "command": "Remove filler words", "suggestionIds": [], "provider": "local", "providerLabel": "On this Mac"}
        (Path(a.dir) / "plan.json").write_text(json.dumps(plan, indent=1, ensure_ascii=False))
        record_timing(a.dir, "fillers", time.time() - t0)
    except Exception as e:
        task.fail(str(e)[:200])
        emit("error", message=str(e))
        sys.exit(1)
    ok = sum(1 for o in plan["ops"] if o["check"] == "applied")
    task.done(f"{len(plan['ops'])} filler cuts planned · {ok} ready")
    emit("plan", path=str(Path(a.dir) / "plan.json"), ops=len(plan["ops"]), ready=ok)


def cmd_render(a):
    """Render a version of the timeline to a video file (runs in the graphics environment for titles)."""
    import subprocess
    import time

    from dmv import edit
    from dmv.progress import Task

    t0 = time.time()
    try:
        base = resolve_base(a.dir, a.base)
        name = Path(base).stem
        outdir = Path(a.dir) / "renders"
        outdir.mkdir(exist_ok=True)
        label = {"preview": "Preview 720p", "hd": "HD 1080p", "hevc4k": "4K HEVC", "prores": "ProRes 422 HQ"}[a.preset]
        out = outdir / f"{name} – {label}"
        task = Task(f"Rendering “{name}” ({label})")
        from dmv import runtime
        py = runtime.python("graphics")
        args = [py, "-m", "dmv.render", str(base), str(out), "--preset", a.preset]
        if a.start is not None:
            args += ["--start", str(a.start)]
        if a.end is not None:
            args += ["--end", str(a.end)]
        from dmv.render import PRESETS
        final = out.with_suffix(PRESETS[a.preset][3])
        prog = Path(str(final) + ".progress")
        prog.unlink(missing_ok=True)
        p = subprocess.Popen(args, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        while p.poll() is None:
            time.sleep(2)
            try:
                f = float(prog.read_text())
                task.update(percent=3 + 92 * f, note=f"{int(f * 100)} % of frames")
            except Exception:
                task.update(percent=2, note="Mixing the sound and opening the media…")
        out_txt, err = p.communicate()
        prog.unlink(missing_ok=True)
        if p.returncode != 0 or not final.exists():
            raise RuntimeError("The render stopped: " + (err or out_txt)[-300:])
        report = json.loads(Path(str(final) + ".json").read_text())
        if a.loudness and a.loudness != "none":
            from dmv import master_audio
            task.update(percent=96, note="Setting the loudness…", force=True)
            target, ceiling = (-23.0, -3.0) if a.loudness == "broadcast" else (-14.0, -1.0)
            mastered = final.with_name(final.stem + f" ({a.loudness} loudness){final.suffix}")
            master_audio.master(str(final), str(mastered), ceiling=ceiling, target_lufs=target)
            final.unlink()
            mastered.rename(final)
            report["loudness"] = master_audio.measure(str(final))
            Path(str(final) + ".json").write_text(json.dumps(report, indent=1))
        record_timing(a.dir, "render", time.time() - t0)
    except Exception as e:
        try:
            task.fail(str(e)[:200])
        except Exception:
            pass
        emit("error", message=str(e))
        sys.exit(1)
    task.done(f"{report['size']} · {report['seconds'] / 60:.1f} min")
    emit("render", path=str(final), report=report)


def _clip_render(pr, ids, task, base_pct=60):
    """Track and render clips; tracking in .venv, drawing in the graphics environment."""
    import subprocess
    import time

    from dmv import clips as C
    from dmv import runtime
    py = runtime.python("graphics")
    todo = [c for c in pr.data["clips"] if not ids or c["id"] in ids]
    for n, c in enumerate(todo):
        span = (100 - base_pct) / max(1, len(todo))
        pct0 = base_pct + n * span
        task.update(percent=pct0, note=f"Clip {n + 1} of {len(todo)}: following the speaker…", force=True)
        if pr.data["settings"].get("layout", "fill") == "fill" and "track" not in c:
            c["track"] = C.track(pr, c)
            pr.save()
        c["status"] = "rendering"
        pr.save()
        prog = pr.dir / f".{c['id']}.progress"
        p = subprocess.Popen([py, "-m", "dmv.clips", "render", str(pr.dir), c["id"]], cwd=str(ROOT),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        while p.poll() is None:
            time.sleep(1.5)
            try:
                f = float(prog.read_text())
                task.update(percent=pct0 + span * 0.95 * f, note=f"Clip {n + 1} of {len(todo)}: “{c['title'][:40]}” · {int(f * 100)} %")
            except Exception:
                pass
        out, err = p.communicate()
        pr.data = json.loads((pr.dir / "project.json").read_text())   # re-read: the renderer doesn't write it
        cc = next(x for x in pr.data["clips"] if x["id"] == c["id"])
        if p.returncode == 0:
            r = json.loads(out.strip().splitlines()[-1])
            cc.update(status="ready", file=r["mp4"], thumb=r["jpg"], rendered=time.strftime("%Y-%m-%dT%H:%M:%S"))
            if "track" in c:
                cc["track"] = c["track"]
        else:
            cc.update(status="failed", error=(err or "")[-300:])
        pr.save()
        emit("clip", dir=str(pr.dir), id=c["id"], status=cc["status"])


def cmd_clips(a):
    import time

    from dmv import clips as C
    from dmv.progress import Task

    t0 = time.time()
    if a.action == "find":
        if not a.video or not Path(a.video).exists():
            emit("error", message="Choose a video file to clip.")
            sys.exit(1)
        settings = {k: getattr(a, k) for k in ("count", "min", "max", "aspect", "style", "layout", "prompt", "whisper",
                                                "language", "provider", "model", "genre") if getattr(a, k, None) is not None}
        settings["hook"] = a.hook != 0
        pr = C.Project.create(a.video, a.name, **settings)
        emit("clipproject", dir=str(pr.dir))
        task = Task(f"Clipping “{pr.data['name']}”")
        try:
            task.update(percent=2, note="Listening to the video…", force=True)
            found = C.find(pr, task)
            if not found:
                raise RuntimeError("No clips could be found in this video. Try shorter or longer clip lengths.")
            task.update(percent=60, note=f"{len(found)} clips found · picked by {pr.data.get('picker')}", force=True)
            emit("clips", dir=str(pr.dir), count=len(found))
            if not a.no_render:
                _clip_render(pr, None, task)
            pr.data["status"] = "ready"
            pr.data["seconds"] = round(time.time() - t0, 1)
            pr.save()
        except Exception as e:
            pr.data["status"] = "failed"
            pr.data["error"] = str(e)[:300]
            pr.save()
            task.fail(str(e)[:200])
            emit("error", message=str(e))
            sys.exit(1)
        task.done(f"{len(pr.data['clips'])} clips ready")
        emit("clipsdone", dir=str(pr.dir))
    elif a.action == "render":
        pr = C.Project(a.dir)
        for k in ("aspect", "style", "layout"):
            if getattr(a, k, None):
                if k == "layout" or k == "aspect":
                    for c in pr.data["clips"]:
                        c.pop("track", None) if k == "layout" else None
                pr.data["settings"][k] = getattr(a, k)
        if a.hook is not None:
            pr.data["settings"]["hook"] = a.hook != 0
        ids = set(a.ids.split(",")) if a.ids else None
        if a.start is not None or a.end is not None:
            for c in pr.data["clips"]:
                if ids and c["id"] in ids:
                    if a.start is not None:
                        c["start"] = round(max(0.0, a.start), 2)
                    if a.end is not None:
                        c["end"] = round(min(pr.data["source"]["duration"], a.end), 2)
                    c.pop("track", None)
        pr.save()
        task = Task(f"Rendering clips of “{pr.data['name']}”")
        try:
            _clip_render(pr, ids, task, base_pct=2)
        except Exception as e:
            task.fail(str(e)[:200])
            emit("error", message=str(e))
            sys.exit(1)
        task.done("Clips rendered")
        emit("clipsdone", dir=str(pr.dir))
    elif a.action == "export":
        import shutil
        pr = C.Project(a.dir)
        ids = set(a.ids.split(",")) if a.ids else None
        todo = [c for c in pr.data["clips"] if (not ids or c["id"] in ids) and c.get("file") and Path(c["file"]).exists()]
        if not todo or not a.dest:
            emit("error", message="No finished clips to export.")
            sys.exit(1)
        dest = Path(a.dest)
        dest.mkdir(parents=True, exist_ok=True)
        task = Task(f"Exporting clips of “{pr.data['name']}”")
        out = []
        try:
            for n, c in enumerate(todo):
                src = Path(c["file"])
                dst = dest / src.name
                task.update(percent=100 * n / len(todo), note=f"{n + 1} of {len(todo)}: {src.stem[:50]}", force=True)
                if a.quality == "small":
                    C.export_small(src, dst, lambda f, n=n: task.update(percent=100 * (n + f) / len(todo)))
                else:
                    shutil.copy2(src, dst)
                (dest / (src.stem + ".txt")).write_text(c.get("title", "") + "\n\n" + " ".join(c.get("hashtags") or []) + "\n")
                out.append({"id": c["id"], "path": str(dst), "mb": round(dst.stat().st_size / 1e6, 1)})
        except Exception as e:
            task.fail(str(e)[:200])
            emit("error", message=str(e))
            sys.exit(1)
        task.done(f"{len(out)} clips exported")
        emit("exported", dest=str(dest), files=out)


def cmd_selftest(_a):
    """Imports, tools and models, plus the Apple Silicon extras; one JSON line, exit 1 if anything required fails."""
    import importlib
    import shutil
    import subprocess
    import time

    from dmv import runtime
    checks, t0 = [], time.time()

    def check(name, fn, required=True):
        try:
            detail = fn()
            checks.append({"name": name, "ok": True, "detail": detail, "required": required})
        except Exception as e:
            checks.append({"name": name, "ok": False, "detail": f"{e.__class__.__name__}: {str(e)[:200]}", "required": required})

    for mod in ("numpy", "cv2", "skia", "faster_whisper", "ctranslate2", "librosa", "soundfile", "pyloudnorm", "scenedetect",
                "pydantic", "anthropic", "colour", "opentimelineio"):
        check(f"import {mod}", lambda m=mod: getattr(importlib.import_module(m), "__version__", "ok"))
    for tool in ("ffmpeg", "ffprobe"):
        check(tool, lambda t=tool: subprocess.run([shutil.which(t) or t, "-hide_banner", "-version"], capture_output=True,
                                                  text=True, check=True).stdout.split("\n")[0][:60])
    check("mediainfo", lambda: subprocess.run([shutil.which("mediainfo") or "mediainfo", "--Version"], capture_output=True,
                                              text=True).stdout.strip().splitlines()[-1], required=False)
    check("speech models", lambda: ", ".join(runtime.features()["speechModels"]) or (_ for _ in ()).throw(RuntimeError("none")),
          required=runtime.BUNDLED)

    def load_whisper():
        from faster_whisper import WhisperModel
        WhisperModel(runtime.whisper_model("base"), device="cpu", compute_type="int8")
        return "base loads"
    check("load Whisper", load_whisper, required=runtime.BUNDLED)
    check("YuNet face detector", lambda: (runtime.yunet_model() and "present") or (_ for _ in ()).throw(RuntimeError("missing")),
          required=runtime.BUNDLED)
    if runtime.ARM:
        check("Apple GPU (PyTorch MPS)", lambda: __import__("torch").backends.mps.is_available() or (_ for _ in ()).throw(RuntimeError("not available")))
        check("MLX Whisper", lambda: runtime.has_mlx() or (_ for _ in ()).throw(RuntimeError("missing")))

        def load_demucs():
            from demucs.pretrained import get_model
            get_model("htdemucs", repo=runtime.MODELS / "demucs")
            return "htdemucs loads"
        check("Demucs voice isolation", load_demucs, required=(runtime.MODELS / "demucs").exists())
        check("VideoToolbox", lambda: "h264_videotoolbox" in subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                                                                            capture_output=True, text=True).stdout or (_ for _ in ()).throw(RuntimeError("missing")))
    ok = all(c["ok"] for c in checks if c["required"])
    emit("selftest", ok=ok, seconds=round(time.time() - t0, 1), features=runtime.features(), checks=checks)
    if not ok:
        sys.exit(1)


def cmd_search(a):
    from dmv import medialog

    print(json.dumps(medialog.search(a.logdir, a.query, a.limit), indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def analyze_args(p):
        p.add_argument("--video")
        p.add_argument("--fcpxml")
        p.add_argument("--out")
        p.add_argument("--name")
        p.add_argument("--end", type=float)
        p.add_argument("--whisper", default="small")
        p.add_argument("--language")
        p.add_argument("--no-transcribe", action="store_true")

    p = sub.add_parser("analyze"); analyze_args(p); p.set_defaults(fn=run_analyze)
    p = sub.add_parser("review"); p.add_argument("dir"); p.add_argument("--provider", default="auto"); p.add_argument("--model"); p.set_defaults(fn=cmd_review)
    p = sub.add_parser("report"); p.add_argument("dir"); p.set_defaults(fn=cmd_report)
    p = sub.add_parser("markers"); p.add_argument("dir"); p.add_argument("--fcpxml", required=True); p.add_argument("--ids"); p.set_defaults(fn=cmd_markers)
    p = sub.add_parser("all"); analyze_args(p); p.add_argument("--provider", default="auto"); p.add_argument("--model"); p.set_defaults(fn=cmd_all)
    p = sub.add_parser("plan"); p.add_argument("dir"); p.add_argument("--ids"); p.add_argument("--command")
    p.add_argument("--base", default="latest"); p.add_argument("--fcpxml"); p.add_argument("--provider", default="auto")
    p.add_argument("--model"); p.set_defaults(fn=cmd_plan)
    p = sub.add_parser("apply"); p.add_argument("dir"); p.add_argument("--ops")
    p.add_argument("--overwrite", help="Replace the original FCPXML in place (pass CONFIRMED). Default: new project")
    p.set_defaults(fn=cmd_apply)
    p = sub.add_parser("versions"); p.add_argument("dir"); p.set_defaults(fn=cmd_versions)
    p = sub.add_parser("fillers"); p.add_argument("dir"); p.add_argument("--base", default="latest"); p.set_defaults(fn=cmd_fillers)
    p = sub.add_parser("clips"); p.add_argument("action", choices=["find", "render", "export"]); p.add_argument("dir", nargs="?")
    p.add_argument("--video"); p.add_argument("--name"); p.add_argument("--count", type=int); p.add_argument("--min", type=float)
    p.add_argument("--max", type=float); p.add_argument("--aspect", choices=["9:16", "1:1", "4:5", "16:9"])
    p.add_argument("--style", choices=["bold", "karaoke", "typewriter", "minimal"]); p.add_argument("--layout", choices=["fill", "fit"])
    p.add_argument("--prompt"); p.add_argument("--genre"); p.add_argument("--whisper"); p.add_argument("--language")
    p.add_argument("--provider"); p.add_argument("--model"); p.add_argument("--hook", type=int); p.add_argument("--ids")
    p.add_argument("--start", type=float); p.add_argument("--end", type=float); p.add_argument("--no-render", action="store_true")
    p.add_argument("--dest"); p.add_argument("--quality", choices=["original", "small"], default="original")
    p.set_defaults(fn=cmd_clips)
    p = sub.add_parser("render"); p.add_argument("dir"); p.add_argument("--base", default="latest")
    p.add_argument("--preset", default="preview", choices=["preview", "hd", "hevc4k", "prores"])
    p.add_argument("--start", type=float); p.add_argument("--end", type=float)
    p.add_argument("--loudness", default="none", choices=["none", "broadcast", "social"]); p.set_defaults(fn=cmd_render)
    p = sub.add_parser("log"); p.add_argument("--src", action="append", required=True); p.add_argument("--name", required=True)
    p.add_argument("--out"); p.add_argument("--whisper", default="small"); p.set_defaults(fn=cmd_log)
    p = sub.add_parser("search"); p.add_argument("logdir"); p.add_argument("query"); p.add_argument("--limit", type=int, default=20)
    p.set_defaults(fn=cmd_search)
    p = sub.add_parser("providers"); p.set_defaults(fn=cmd_providers)
    p = sub.add_parser("selftest"); p.set_defaults(fn=cmd_selftest)
    p = sub.add_parser("set-key"); p.add_argument("provider"); p.set_defaults(fn=cmd_set_key)
    p = sub.add_parser("delete-key"); p.add_argument("provider"); p.set_defaults(fn=cmd_delete_key)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
