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
    def do_GET(self):
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Pipeline server running. Use POST /apply or POST /suggest from the Pipeline UI.")

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
