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


def apply_overrides(overrides):
    logs = []

    log_info(f"Received overrides for {len(overrides)} slide(s)")

    # ── 0. Save overrides to pipeline_state.json ───────────────────────
    state_path = TASK / "pipeline_state.json"
    write_json(state_path, overrides)
    logs.append(f"Saved overrides to pipeline_state.json")

    for key, ov in overrides.items():
        if ov.get("notes"):
            logs.append(f"[{key}] notes: {ov['notes'][:120]}")
            log_info(f"  {key}: notes present")

    log_info("Step 1/4: Updating narration_script.md ...")
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

    log_info("Step 2/4: Patching metadata.json ...")
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

    log_info("Step 3/4: Re-running TTS ...")
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

    log_info("Step 4/4: Rebuilding timeline ...")
    build_script = SKILL_DIR / "build_timeline.py"
    if build_script.exists():
        r = run_python(build_script)
        logs.append(f"Timeline: {r.stdout.strip()}")
        if r.returncode:
            logs.append(f"Timeline error: {redact(r.stderr.strip())}")
    else:
        logs.append(f"Build script not found at {build_script}")

    # ── 5. Auto-process adjustment notes ───────────────────────────────
    rebuilt = False
    for key, ov in overrides.items():
        note = (ov.get("notes") or "").strip()
        if not note:
            continue
        logs.append(f"---")
        logs.append(f"Processing notes for {key}: \"{note[:80]}\"")
        actions, did_tts = auto_process_note(note, key, logs)
        for a in actions:
            logs.append(f"  → {a}")
        if not actions:
            logs.append(f"  → (no rules matched — tell the agent in chat)")
        if did_tts:
            rebuilt = True

    if rebuilt:
        log_info("Rebuilding timeline after auto-processed notes ...")
        build_script = SKILL_DIR / "build_timeline.py"
        if build_script.exists():
            r = run_python(build_script)
            logs.append(f"Timeline (post-notes): {r.stdout.strip()}")
            if r.returncode:
                logs.append(f"Timeline error: {redact(r.stderr.strip())}")

    return logs


def auto_process_note(note, slide_key, logs):
    """Simple keyword-based processing for common requests.
    Returns (actions, did_tts)."""
    actions = []
    did_tts = False
    nl = note.lower()
    tts = SKILL_DIR / "tts_edge.py"

    if re.search(r"慢一点|慢一點|slower|slow\s*down", nl):
        actions.append("Detected: slow down narration → re-running TTS at -10%")
        if tts.exists():
            r = run_python(tts, "zh-TW-YunJheNeural", "-10%")
            actions.append(f"TTS: {r.stdout.strip() or '(ok)'}")
            did_tts = True
        return actions, did_tts

    if re.search(r"快一点|快一點|faster|speed\s*up", nl):
        actions.append("Detected: speed up narration → re-running TTS at +10%")
        if tts.exists():
            r = run_python(tts, "zh-TW-YunJheNeural", "+10%")
            actions.append(f"TTS: {r.stdout.strip() or '(ok)'}")
            did_tts = True
        return actions, did_tts

    if re.search(r"正常速|normal\s*speed", nl):
        actions.append("Detected: normal speed → re-running TTS at -5%")
        if tts.exists():
            r = run_python(tts, "zh-TW-YunJheNeural", "-5%")
            actions.append(f"TTS: {r.stdout.strip() or '(ok)'}")
            did_tts = True
        return actions, did_tts

    if re.search(r"换(?:成|为)?.*[男女]声|change\s*voice|换声音", nl):
        actions.append("Detected: voice change request → re-running TTS")
        if tts.exists():
            r = run_python(tts, "zh-TW-YunJheNeural", "+0%")
            actions.append(f"TTS: {r.stdout.strip() or '(ok)'}")
            did_tts = True
        return actions, did_tts

    if re.search(r"first|appear\s*first|先进[场場]|先出現|先進場", nl):
        actions.append("Detected: animation order request")
        m = re.search(r"slide\s*(\d+)", nl)
        if m:
            sn = int(m.group(1))
            meta_path = TASK / "output" / f"slide_{sn:02d}" / "metadata.json"
            meta = load_json(meta_path)
            if meta:
                if re.search(r"title|标题|標題", nl):
                    for l in meta.get("layers", []):
                        if l.get("type") == "title":
                            l["z_index"] = 0
                            l["start"] = 0.45
                            actions.append(f"→ force {l['name']} first (z=0)")
                    write_json(meta_path, meta)
        return actions, did_tts

    return actions, did_tts


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
