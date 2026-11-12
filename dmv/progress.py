"""Writes background-task progress to .progress/progress.json for the Build progress panel."""
import json
import os
import signal
import time
from pathlib import Path

PROGRESS_FILE = Path(__file__).resolve().parent.parent / ".progress" / "progress.json"


def _load():
    try:
        return json.loads(PROGRESS_FILE.read_text())
    except Exception:
        return None


def _save(data):
    tmp = PROGRESS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, PROGRESS_FILE)


_active = []


def _on_stop(signum, frame):
    # The app's Stop button sends SIGTERM. Mark open tasks as stopped instead of leaving them "running",
    # then exit; SystemExit also makes subprocess.run kill any child (ffmpeg, claude -p).
    for t in list(_active):
        try:
            t.update(status="stopped", note="Stopped", force=True)
        except Exception:
            pass
    raise SystemExit(143)


try:
    signal.signal(signal.SIGTERM, _on_stop)
except ValueError:  # not the main thread
    pass


_JOBS = Path.home() / ".claude" / "progress" / "jobs"


def _mirror(task, fields):
    """Also show the task on the background-jobs band above the Claude Code prompt (every project, every session)."""
    try:
        rec = getattr(task, "_job", None) or {"id": "dmv-" + "".join(c if c.isalnum() else "-" for c in task.label)[:70],
                                             "project": "Design My Video", "label": task.label, "status": "running",
                                             "percent": 0, "startedAt": task.started}
        for k in ("status", "percent", "note"):
            if fields.get(k) is not None:
                rec[k] = fields[k] if k != "status" else {"running": "running", "done": "done", "failed": "failed",
                                                          "stopped": "stopped"}.get(fields[k], "running")
        rec["updatedAt"] = int(time.time() * 1000)
        task._job = rec
        _JOBS.mkdir(parents=True, exist_ok=True)
        p = _JOBS / f"{rec['id']}.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(rec))
        os.replace(tmp, p)
    except Exception:
        pass


class Task:
    """One background entry in the panel. Silently does nothing if the panel file is absent."""

    def __init__(self, label):
        self.label = label
        self.started = int(time.time() * 1000)
        self._last = 0.0
        _active.append(self)
        self.update(status="running", percent=0, note="Starting…", force=True)

    def update(self, force=False, **fields):
        now = time.time()
        if not force and now - self._last < 1.0:
            return
        self._last = now
        _mirror(self, fields)
        if os.environ.get("DMV_PROGRESS_STDOUT"):
            # The Mac app reads these JSON lines for its progress bar.
            print(json.dumps({"event": "progress", "label": self.label, **fields}), flush=True)
        data = _load()
        if data is None:
            return
        bg = data.setdefault("background", [])
        entry = next((t for t in bg if t.get("label") == self.label), None)
        if entry is None:
            entry = {"label": self.label}
            bg.append(entry)
        entry["startedAt"] = self.started
        entry.update({k: v for k, v in fields.items() if v is not None})
        if "percent" in entry and entry["percent"] is not None:
            entry["percent"] = int(max(0, min(100, entry["percent"])))
        _save(data)

    def done(self, note=None):
        self.update(status="done", percent=100, note=note, force=True)
        if self in _active:
            _active.remove(self)

    def fail(self, note):
        self.update(status="failed", note=note, force=True)
        if self in _active:
            _active.remove(self)


class Stages:
    """Maps per-stage progress (0..1) onto one overall percentage."""

    def __init__(self, task, weights):
        self.task = task
        self.weights = weights
        self.total = sum(weights.values())
        self.done_w = 0.0
        self.current = None

    def start(self, name, note):
        if self.current:
            self.done_w += self.weights[self.current]
        self.current = name
        self.report(0.0, note, force=True)

    def report(self, frac, note=None, force=False):
        w = self.weights.get(self.current, 0)
        pct = 100 * (self.done_w + w * max(0.0, min(1.0, frac))) / self.total
        self.task.update(percent=pct, note=note, force=force)


def _cli():
    """python -m dmv.progress task LABEL --status running|done|failed|stopped [--percent N] [--note TEXT]
       python -m dmv.progress item PHASE_PREFIX LABEL_PREFIX --status todo|doing|done [--label NEW_LABEL]
       python -m dmv.progress add-item PHASE_PREFIX LABEL [--status todo|doing|done]"""
    import argparse

    ap = argparse.ArgumentParser(prog="dmv.progress")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("task"); t.add_argument("label"); t.add_argument("--status", default="running")
    t.add_argument("--percent", type=float); t.add_argument("--note")
    i = sub.add_parser("item"); i.add_argument("phase"); i.add_argument("label"); i.add_argument("--status", required=True)
    i.add_argument("--label", dest="new_label")
    a = sub.add_parser("add-item"); a.add_argument("phase"); a.add_argument("label"); a.add_argument("--status", default="todo")
    a_ = ap.parse_args()
    data = _load()
    if data is None:
        raise SystemExit("progress file not found")
    if a_.cmd == "task":
        bg = data.setdefault("background", [])
        e = next((x for x in bg if x.get("label") == a_.label), None)
        if e is None:
            e = {"label": a_.label, "startedAt": int(time.time() * 1000)}
            bg.append(e)
        e["status"] = a_.status
        if a_.status == "done":
            e["percent"] = 100
        if a_.percent is not None:
            e["percent"] = int(max(0, min(100, a_.percent)))
        if a_.note is not None:
            e["note"] = a_.note
    else:
        ph = next((p for p in data["phases"] if p["name"].startswith(a_.phase)), None)
        if ph is None:
            raise SystemExit(f"no phase starting with {a_.phase!r}")
        if a_.cmd == "add-item":
            ph["items"].append({"label": a_.label, "status": a_.status})
        else:
            it = next((x for x in ph["items"] if x["label"].startswith(a_.label)), None)
            if it is None:
                raise SystemExit(f"no item starting with {a_.label!r}")
            it["status"] = a_.status
            if a_.new_label:
                it["label"] = a_.new_label
    _save(data)


if __name__ == "__main__":
    _cli()
