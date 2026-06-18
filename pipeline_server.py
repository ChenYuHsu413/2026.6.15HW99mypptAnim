#!/usr/bin/env python3
"""Shared pipeline server -- serves the repo-root pipeline-ui AND handles
apply / render / suggest for ANY task (the task path comes in the request).

Run from the repo root:
    python pipeline_server.py [port]      # default 9001

Then open  http://localhost:<port>/pipeline-ui/

Note: the 8000-8099 port range is reserved/blocked on some Windows machines;
9001 (and 3000/5000/5500/9000/9090) bind fine.
"""

import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SKILL_DIR = ROOT / "skill-pptx-to-animated-video" / "scripts"

MIME = {
    ".html": "text/html", ".css": "text/css", ".js": "text/javascript",
    ".json": "application/json", ".png": "image/png", ".jpg": "image/jpeg",
    ".mp3": "audio/mpeg", ".mp4": "video/mp4", ".srt": "text/plain",
    ".vtt": "text/plain", ".wav": "audio/wav", ".svg": "image/svg+xml",
    ".pdf": "application/pdf", ".ico": "image/x-icon",
}


def log(msg):
    sys.stderr.write(f"[pipeline-server] {msg}\n")


def load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def write_json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def deep_merge(base, over):
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def safe_task(task):
    """Resolve a task path from the request to a directory under ROOT."""
    if not task:
        return None
    p = (ROOT / task).resolve()
    if p != ROOT and ROOT not in p.parents:
        return None
    return p if p.is_dir() else None


def run_python(script, *args, cwd):
    return subprocess.run(
        [sys.executable, str(script)] + list(args),
        capture_output=True, text=True, cwd=str(cwd),
    )


def apply_overrides(task_dir, incoming):
    """Persist edits to <task>/overrides.json and rebuild the resolved view.

    Generated artifacts stay pristine. Narration/voice edits also re-run
    TTS + timeline (they change the audio); layer/notes edits only need the
    composition rebuilt.
    """
    logs = []
    ov_path = task_dir / "overrides.json"
    merged = deep_merge(load_json(ov_path) or {}, incoming)
    write_json(ov_path, merged)
    logs.append("Saved edits to overrides.json (generated artifacts untouched)")

    audio_changed = bool(merged.get("voice")) or any(
        isinstance(v, dict) and v.get("narration") for v in merged.values()
    )

    def step(name, label):
        script = SKILL_DIR / name
        if not script.exists():
            logs.append(f"{name} not found")
            return
        r = run_python(script, cwd=task_dir)
        logs.append(f"{label}: {r.stdout.strip() or '(ok)'}")
        if r.returncode:
            logs.append(f"{label} error: {r.stderr.strip()[:200]}")

    if audio_changed:
        log("narration/voice changed -- re-running TTS + timeline")
        step("tts_edge.py", "TTS")
        step("build_timeline.py", "Timeline")
    step("build_composition.py", "composition.json")
    return logs


def suggest_slide(task_dir, slide_num):
    meta = load_json(task_dir / "output" / f"slide_{slide_num:02d}" / "metadata.json")
    if not meta:
        return [{"type": "error", "message": f"metadata for slide {slide_num:02d} not found"}]
    layers = meta.get("layers", [])
    dur = meta.get("duration", 10)
    out = []
    if len(layers) <= 2 and dur > 12:
        out.append({"type": "subdivide", "message": f"Only {len(layers)} layers in {dur:.0f}s -- consider more reveal steps"})
    elif len(layers) >= 12 and dur < 15:
        out.append({"type": "consolidate", "message": f"{len(layers)} layers in {dur:.0f}s -- may feel rushed"})
    if len(layers) >= 4:
        starts = sorted(l["start"] for l in layers)
        for k in range(len(starts) - 2):
            if starts[k + 2] - starts[k] < 0.6:
                out.append({"type": "spread", "message": f"3 layers start within {starts[k+2]-starts[k]:.2f}s near {starts[k]:.1f}s"})
                break
    return out


def render_task(task_dir, fr, to):
    script = SKILL_DIR / "render_final_video.py"
    if not script.exists():
        return "render_final_video.py not found"
    if not (task_dir / "composition.json").exists():
        return "composition.json missing -- apply an edit (or run build_composition.py) first"
    env = os.environ.copy()
    env["RENDER_SLIDES"] = f"{fr},{to}"
    r = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True, text=True, cwd=str(task_dir), env=env, timeout=1800,
    )
    if r.returncode:
        return f"Render error: {r.stderr.strip()[:300]}"
    final = task_dir / "final" / "final_video_with_voiceover_and_subtitles.mp4"
    if final.exists():
        return f"Rendered: final/final_video_with_voiceover_and_subtitles.mp4 ({final.stat().st_size//1024//1024}MB)"
    return f"Render ran.\n{r.stdout.strip()[:300]}"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0].lstrip("/")
        if not path:
            path = "pipeline-ui/index.html"
        file_path = (ROOT / path).resolve()
        if file_path.is_dir():
            file_path = file_path / "index.html"
        if (ROOT in file_path.parents or file_path == ROOT) and file_path.is_file():
            self.send_response(200)
            self.send_header("Content-Type", MIME.get(file_path.suffix.lower(), "application/octet-stream"))
            self.end_headers()
            self.wfile.write(file_path.read_bytes())
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"not found")

    def _body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length)) if length else {}

    def do_POST(self):
        route = self.path.split("?")[0]
        try:
            body = self._body()
        except Exception as e:
            return self._json(400, {"status": "error", "message": str(e)})
        task_dir = safe_task(body.get("task"))
        if not task_dir:
            return self._json(400, {"status": "error", "message": "missing or invalid 'task'"})
        if route == "/apply":
            logs = apply_overrides(task_dir, body.get("overrides") or {})
            self._json(200, {"status": "ok", "logs": logs})
        elif route == "/suggest":
            if not body.get("slide"):
                return self._json(400, {"status": "error", "message": "missing 'slide'"})
            self._json(200, {"status": "ok", "suggestions": suggest_slide(task_dir, int(body["slide"]))})
        elif route == "/render":
            out = render_task(task_dir, body.get("from", 1), body.get("to", 99))
            self._json(200, {"status": "ok", "output": out})
        else:
            self._json(404, {"status": "error", "message": "not found"})

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def _json(self, code, obj):
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(obj, ensure_ascii=False).encode())

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, fmt, *args):
        return


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9001
    server = HTTPServer(("127.0.0.1", port), Handler)
    print(f"Pipeline server on http://localhost:{port}  ->  /pipeline-ui/")
    print(f"Serving repo root: {ROOT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
