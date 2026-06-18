#!/usr/bin/env python3
"""Pipeline server — serves the UI AND handles pipeline apply/suggest.

Run from the task directory:
    cd task=InfoGraphic2AIGCdirection
    python pipeline_server.py [port]

Serves all static files from the project root, plus:
    POST /apply   — apply pipeline_state.json overrides
    POST /suggest — analyze a slide
"""

import json
import mimetypes
import os
import shutil
import subprocess
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
TASK = HERE  # running from task directory
ROOT = TASK.parent
SKILL_DIR = ROOT / "skill-pptx-to-animated-video" / "scripts"

RECENT_LOGS = []  # global log buffer for GET status page
MAX_LOG_ENTRIES = 100

MIME = {
    ".html": "text/html", ".css": "text/css", ".js": "text/javascript",
    ".json": "application/json", ".png": "image/png", ".jpg": "image/jpeg",
    ".mp3": "audio/mpeg", ".mp4": "video/mp4", ".srt": "text/plain",
    ".vtt": "text/plain", ".wav": "audio/wav", ".svg": "image/svg+xml",
    ".pdf": "application/pdf", ".ico": "image/x-icon",
}

RECENT_LOGS = []  # global log buffer for GET status page
MAX_LOG_ENTRIES = 100


def log_info(msg):
    ts = __import__("datetime").datetime.now().strftime("%H:%M:%S")
    entry = f"[{ts}] {msg}"
    RECENT_LOGS.append(entry)
    if len(RECENT_LOGS) > MAX_LOG_ENTRIES:
        RECENT_LOGS.pop(0)
    sys.stderr.write(f"[pipeline-server] {msg}\n")


def redact(s):
    """Shorten long strings for log messages."""
    return s[:200] + "…" if len(s) > 200 else s


def load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def write_json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def run_python(script, *args, cwd=None):
    cmd = [sys.executable, str(script)] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd or str(TASK))
    return result


def suggest_slide(slide_num):
    """Analyze a slide's metadata and return improvement suggestions."""
    meta_path = TASK / "output" / f"slide_{slide_num:02d}" / "metadata.json"
    meta = load_json(meta_path)
    if not meta:
        return [{"type": "error", "message": f"metadata for slide {slide_num:02d} not found"}]

    layers = meta.get("layers", [])
    dur = meta.get("duration", 10)
    suggestions = []

    # ─── 1. Merge text blocks on the same line ─────────────────────────
    texts = [(i, l) for i, l in enumerate(layers) if l.get("type") == "text_block"]
    for i in range(len(texts)):
        for j in range(i + 1, len(texts)):
            _, a = texts[i]
            _, b = texts[j]
            ay_mid = a["y"] + a["height"] / 2
            by_mid = b["y"] + b["height"] / 2
            if abs(ay_mid - by_mid) < max(a["height"], b["height"]) * 0.6:
                gap = max(0, min(a["x"] + a["width"], b["x"] + b["width"]) - max(a["x"], b["x"]))
                gap_x = max(0, max(a["x"], b["x"]) - min(a["x"] + a["width"], b["x"] + b["width"]))
                if gap_x < 30 and gap_x > 2:
                    suggestions.append({
                        "type": "merge_text",
                        "message": f"Merge \"{a['name']}\" and \"{b['name']}\" — same line, {gap_x}px apart",
                        "layers": [a["name"], b["name"]],
                        "action": {"merge": [a["name"], b["name"]]},
                    })

    # ─── 2. Reading order vs z_index ───────────────────────────────────
    sorted_layers = sorted(layers, key=lambda l: (l["y"], l["x"]))
    z_order = sorted(layers, key=lambda l: l["z_index"])
    for i in range(min(len(sorted_layers), len(z_order))):
        if sorted_layers[i]["name"] != z_order[i]["name"]:
            s = sorted_layers[i]
            z = z_order[i]
            suggestions.append({
                "type": "reorder",
                "message": f"\"{s['name']}\" reads before \"{z['name']}\" but has higher z_index ({s['z_index']} vs {z['z_index']})",
                "layers": [s["name"], z["name"]],
                "action": {"swap_z": [s["name"], z["name"]]},
            })
            break  # one at a time to keep it simple

    # ─── 3. Clustered starts ───────────────────────────────────────────
    if len(layers) >= 4:
        starts = sorted([l["start"] for l in layers])
        for k in range(len(starts) - 2):
            if starts[k + 2] - starts[k] < 0.6:
                suggestions.append({
                    "type": "spread",
                    "message": f"3 layers start within {starts[k+2]-starts[k]:.2f}s (around {starts[k]:.1f}s) — consider spreading them",
                    "action": {},
                })
                break

    # ─── 4. Layer count vs duration ────────────────────────────────────
    if len(layers) <= 2 and dur > 12:
        suggestions.append({
            "type": "subdivide",
            "message": f"Only {len(layers)} layers in {dur:.0f}s of narration — consider splitting into more reveal steps",
            "action": {},
        })
    elif len(layers) >= 12 and dur < 15:
        suggestions.append({
            "type": "consolidate",
            "message": f"{len(layers)} layers packed into {dur:.0f}s — may feel rushed; consider merging small elements",
            "action": {},
        })

    return suggestions


def deep_merge(base, over):
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def apply_overrides(incoming):
    """Persist edits to overrides.json and rebuild the resolved composition.

    Generated artifacts (metadata.json, narration_script.md) are never
    mutated. Edits accumulate in overrides.json; build_composition.py applies
    them on top of the pristine generated data to produce composition.json.
    """
    logs = []
    log_info(f"Received overrides for {len(incoming)} slide(s)")

    # ── Accumulate edits into overrides.json (deep-merged) ─────────────
    ov_path = TASK / "overrides.json"
    merged = deep_merge(load_json(ov_path) or {}, incoming)
    write_json(ov_path, merged)
    logs.append("Saved edits to overrides.json (generated artifacts untouched)")
    for key, ov in incoming.items():
        if isinstance(ov, dict) and ov.get("notes"):
            logs.append(f"[{key}] notes: {ov['notes'][:120]}")
            log_info(f"  {key}: notes present")

    # ── Rebuild the resolved contract (overrides applied here) ─────────
    log_info("Rebuilding composition.json ...")
    build_script = SKILL_DIR / "build_composition.py"
    if build_script.exists():
        r = run_python(build_script)
        logs.append(f"composition.json: {r.stdout.strip() or '(ok)'}")
        if r.returncode:
            logs.append(f"composition error: {redact(r.stderr.strip())}")
    else:
        logs.append(f"build_composition.py not found at {build_script}")

    return logs


def render_slides(slide_from, slide_to):
    """Render a range of slides to MP4."""
    log_info(f"Rendering slides {slide_from}-{slide_to} ...")
    render_script = SKILL_DIR / "render_final_video.py"
    if not render_script.exists():
        return f"Render script not found at {render_script}"
    env = os.environ.copy()
    env["RENDER_SLIDES"] = f"{slide_from},{slide_to}"
    r = subprocess.run(
        [sys.executable, str(render_script)],
        capture_output=True, text=True, cwd=str(TASK), env=env, timeout=600
    )
    if r.returncode:
        return f"Render error: {redact(r.stderr.strip())}"
    final = TASK / "final" / "final_video_with_voiceover_and_subtitles.mp4"
    if final.exists():
        return f"Rendered: final/final_video_with_voiceover_and_subtitles.mp4 ({final.stat().st_size//1024//1024}MB)\n{r.stdout.strip()}"
    return f"Render ran but output not found.\n{r.stdout.strip()}"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0].lstrip("/")
        if not path:
            path = "index.html"

        # Auto-serve index.html for directory paths
        file_path = ROOT / path
        if file_path.is_dir():
            file_path = file_path / "index.html"

        if file_path.is_file() and not file_path.is_symlink():
            ext = file_path.suffix.lower()
            self.send_response(200)
            self.send_header("Content-Type", MIME.get(ext, "application/octet-stream"))
            self.end_headers()
            self.wfile.write(file_path.read_bytes())
        else:
            # Fallback: show server status page
            html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Pipeline Server Status</title>
<style>
body {{ font-family: monospace; background: #0d1117; color: #edf3fb; padding: 20px; }}
h1 {{ color: #7dd3fc; }}
.log {{ color: #86efac; white-space: pre-wrap; }}
.entry {{ padding: 4px 0; border-bottom: 1px solid #303946; }}
.meta {{ color: #93a4b8; font-size: 12px; }}
</style></head><body>
<h1>Pipeline Server</h1>
<p class="meta">Task: {TASK.name} | Port: {self.server.server_port}</p>
<p class="meta">Serving from: {ROOT}</p>
<p class="meta">Endpoints: POST /apply | POST /suggest</p>
<hr>
<h2>Activity log</h2>
<div class="log">"""
            if not RECENT_LOGS:
                html += "<div class='entry'>Waiting for requests…</div>"
            for entry in RECENT_LOGS[-50:]:
                html += f"<div class='entry'>{entry}</div>"
            html += "</div></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(html.encode())

    def do_POST(self):
        if self.path == "/apply":
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length))
            except Exception as e:
                self._json(400, {"status": "error", "message": str(e)})
                return
            logs = apply_overrides(body)
            self._json(200, {"status": "ok", "logs": logs})
        elif self.path == "/suggest":
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length))
            except Exception as e:
                self._json(400, {"status": "error", "message": str(e)})
                return
            slide_num = body.get("slide")
            if not slide_num:
                self._json(400, {"status": "error", "message": "Missing 'slide' in body"})
                return
            suggestions = suggest_slide(slide_num)
            self._json(200, {"status": "ok", "suggestions": suggestions})
        elif self.path == "/render":
            length = int(self.headers.get("Content-Length", 0))
            try: body = json.loads(self.rfile.read(length))
            except Exception as e: self._json(400, {"status":"error","message":str(e)}); return
            fr = body.get("from", 1); to = body.get("to", 99)
            output = render_slides(fr, to)
            self._json(200, {"status": "ok", "output": output})
        else:
            self._json(404, {"status": "error", "message": "Not found"})

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
        sys.stderr.write(f"[pipeline-server] {args}\n")


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8001
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"Pipeline server running on http://localhost:{port}")
    print(f"Task directory: {TASK}")
    print(f"Skill scripts:  {SKILL_DIR}")
    server.serve_forever()


if __name__ == "__main__":
    main()
