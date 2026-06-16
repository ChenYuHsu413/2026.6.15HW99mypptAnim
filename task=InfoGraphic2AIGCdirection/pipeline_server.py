#!/usr/bin/env python3
"""Pipeline action server — receives pipeline_state.json overrides and applies them.

Run from the task directory:
    cd task=InfoGraphic2AIGCdirection
    python pipeline_server.py [port]

The UI POSTs to http://localhost:<port>/apply.
"""

import json
import os
import re
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


def apply_overrides(overrides):
    logs = []

    # ── 1. Update narration_script.md ──────────────────────────────────
    nar_path = TASK / "narration" / "narration_script.md"
    if nar_path.exists():
        text = nar_path.read_text(encoding="utf-8")
        changed = False
        for key, ov in overrides.items():
            narration = ov.get("narration")
            if not narration:
                continue
            m = re.match(r"slide_(\d+)", key)
            if not m:
                continue
            n = int(m.group(1))
            old = re.search(
                rf"^## Slide {n}\b[^\n]*\n+(.*?)(?=^## Slide |\Z)", text, re.M | re.S
            )
            if old:
                text = text[: old.start()] + f"## Slide {n:02d}\n\n{narration}\n" + text[old.end() :]
                changed = True
        if changed:
            nar_path.write_text(text, encoding="utf-8")
            logs.append(f"Updated narration_script.md")

    # ── 2. Patch metadata.json for each slide with layer overrides ─────
    for key, ov in overrides.items():
        layer_ovs = ov.get("layers")
        if not layer_ovs:
            continue
        m = re.match(r"slide_(\d+)", key)
        if not m:
            continue
        n = int(m.group(1))
        meta_path = TASK / "output" / f"slide_{n:02d}" / "metadata.json"
        meta = load_json(meta_path)
        if not meta:
            continue
        patched = False
        for layer in meta.get("layers", []):
            lo = layer_ovs.get(layer["name"])
            if not lo:
                continue
            if lo.get("start") is not None:
                layer["start"] = lo["start"]
                patched = True
            if lo.get("duration") is not None:
                layer["duration"] = lo["duration"]
                patched = True
            if lo.get("animation") is not None:
                layer["animation"] = lo["animation"]
                patched = True
        if patched:
            write_json(meta_path, meta)
            logs.append(f"Patched metadata slide {n:02d}")

    # ── 3. Re-run TTS if any narration changed ─────────────────────────
    narration_changed = any(ov.get("narration") for ov in overrides.values())
    if narration_changed:
        tts_script = SKILL_DIR / "tts_edge.py"
        if tts_script.exists():
            r = run_python(tts_script, "zh-TW-YunJheNeural", "+0%")
            logs.append(f"TTS: {r.stdout.strip() or '(ok)'}")
            if r.returncode:
                logs.append(f"TTS error: {redact(r.stderr.strip())}")
        else:
            logs.append(f"TTS script not found at {tts_script}")

    # ── 4. Rebuild timeline ────────────────────────────────────────────
    build_script = SKILL_DIR / "build_timeline.py"
    if build_script.exists():
        r = run_python(build_script)
        logs.append(f"Timeline: {r.stdout.strip()}")
        if r.returncode:
            logs.append(f"Timeline error: {redact(r.stderr.strip())}")
    else:
        logs.append(f"Build script not found at {build_script}")

    return logs


class Handler(BaseHTTPRequestHandler):
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
