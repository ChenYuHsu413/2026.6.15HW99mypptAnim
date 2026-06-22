#!/usr/bin/env python3
"""Shared pipeline server -- serves the repo-root pipeline-ui AND handles
apply / render / suggest for ANY task (the task path comes in the request).

Run from the repo root:
    python pipeline_server.py                       # localhost only (default)
    python pipeline_server.py 9001                  # localhost on a specific port
    python pipeline_server.py 9001 0.0.0.0          # reachable on the LAN
    python pipeline_server.py 9001 0.0.0.0 share    # LAN + acknowledge no auth

Then open  http://localhost:<port>/pipeline-ui/

Note: the 8000-8099 port range is reserved/blocked on some Windows machines;
9001 (and 3000/5000/5500/9000/9090) bind fine.

SECURITY: this server has no auth, no upload size cap, and no rate limit.
Binding to 0.0.0.0 exposes the /ingest endpoint to anyone who can reach the
port. Fine for a trusted LAN or a short cloudflared tunnel; do not leave a
public tunnel up unattended.
"""

import json
import os
import re
import subprocess
import sys
from email.parser import BytesParser
from email.policy import default as default_policy
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SKILL_DIR = ROOT / "skill-pptx-to-animated-video" / "scripts"
TASK_NAME_RE = re.compile(r"[A-Za-z0-9._-]+")
INGEST_SUFFIXES = {".pdf", ".pptx", ".ppt"}

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


_SPLIT_SUFFIX_RE = re.compile(r"^(.*)_split_(a|b)(\.[^.]+)$")


def _split_parent(name):
    """Return (parent_name, 'a'|'b') if `name` is a split child, else (None, None).

    Naming chain: `<base>_split_a.png` → parent `<base>.png`;
                  `<base>_split_a_split_b.png` → parent `<base>_split_a.png`.
    So the recursion just strips the rightmost `_split_<x>` token.
    """
    m = _SPLIT_SUFFIX_RE.match(name)
    if not m:
        return None, None
    return f"{m.group(1)}{m.group(3)}", m.group(2)


def _resolved_bbox(task_dir, slide_n, name, incoming_bbox):
    """Resolve a layer's bbox by walking overrides → metadata, then chasing
    split chains for synthetic child names.

    Order of precedence:
      1. incoming override (this apply call's payload)
      2. bbox override saved in overrides.json
      3. for split children (name has `_split_<a|b>` suffix): derive from
         parent's resolved bbox + parent's split spec, recursively
      4. metadata.json (only for real, segmented layers)
    """
    if incoming_bbox:
        return [int(v) for v in incoming_bbox]
    ov = load_json(task_dir / "overrides.json") or {}
    sl = ov.get(f"slide_{slide_n:02d}") or {}
    layers_ov = sl.get("layers") or {}
    ov_bbox = (layers_ov.get(name) or {}).get("bbox")
    if ov_bbox:
        return [int(v) for v in ov_bbox]
    # Synthetic split child? walk up the chain.
    parent_name, _which = _split_parent(name)
    if parent_name:
        parent_bbox = _resolved_bbox(task_dir, slide_n, parent_name, None)
        if parent_bbox is None:
            return None
        parent_split = (layers_ov.get(parent_name) or {}).get("split")
        if not parent_split:
            return None  # parent doesn't have a split — this child is stale
        sys.path.insert(0, str(SKILL_DIR))
        try:
            from overrides import split_of, split_children  # noqa: WPS433
            spec = split_of({"split": parent_split})
        finally:
            sys.path.pop(0)
        if not spec:
            return None
        for cname, cbbox in split_children(parent_name, parent_bbox, spec):
            if cname == name:
                return cbbox
        return None
    meta = load_json(task_dir / "output" / f"slide_{slide_n:02d}" / "metadata.json") or {}
    for layer in meta.get("layers", []):
        if layer.get("name") == name:
            return [layer["x"], layer["y"], layer["width"], layer["height"]]
    return None


def _reextract_layer_edits(task_dir, incoming, logs):
    """For each layer override that changes geometry, cut fresh transparent PNGs.

    Handles two edit shapes:
      bbox  → re-extract the layer at the new bbox.
      split → resolve the parent bbox, compute children, re-extract each child
              into edits/<child_name> so the renderer picks them up.
    """
    bbox_edits = []   # (slide_n, layer_name, bbox)
    split_edits = []  # (slide_n, layer_name, parent_bbox, split_spec)
    for sk_, sl in (incoming or {}).items():
        if not isinstance(sl, dict) or not sk_.startswith("slide_"):
            continue
        try:
            slide_n = int(sk_.split("_")[1])
        except (IndexError, ValueError):
            continue
        for name, layer_ov in (sl.get("layers") or {}).items():
            if not isinstance(layer_ov, dict):
                continue
            if layer_ov.get("bbox"):
                bbox_edits.append((slide_n, name, layer_ov["bbox"]))
            if isinstance(layer_ov.get("split"), dict):
                parent_bbox = _resolved_bbox(task_dir, slide_n, name, layer_ov.get("bbox"))
                if parent_bbox:
                    split_edits.append((slide_n, name, parent_bbox, layer_ov["split"]))
                else:
                    logs.append(f"split skipped for {name}: parent bbox not found")
    if not bbox_edits and not split_edits:
        return
    sys.path.insert(0, str(SKILL_DIR))
    try:
        from reextract import reextract  # noqa: WPS433
        from overrides import split_children, split_of  # noqa: WPS433
        for slide_n, name, bbox in bbox_edits:
            sd = task_dir / "output" / f"slide_{slide_n:02d}"
            try:
                reextract(sd, name, bbox)
                logs.append(f"re-extracted {name} at bbox {list(bbox)}")
            except Exception as exc:  # noqa: BLE001
                logs.append(f"re-extract failed for {name}: {exc}")
        for slide_n, name, parent_bbox, raw_spec in split_edits:
            spec = split_of({"split": raw_spec})
            if not spec:
                logs.append(f"split skipped for {name}: invalid spec {raw_spec}")
                continue
            sd = task_dir / "output" / f"slide_{slide_n:02d}"
            children = list(split_children(name, parent_bbox, spec))
            for cname, cbbox in children:
                try:
                    reextract(sd, cname, cbbox)
                    logs.append(f"split child {cname} at bbox {cbbox}")
                except Exception as exc:  # noqa: BLE001
                    logs.append(f"split extract failed for {cname}: {exc}")
            _reocr_split_children(sd, parent_bbox, children, logs)
    finally:
        sys.path.pop(0)


def _reocr_split_children(slide_dir, parent_bbox, children, logs):
    """When center-based attribution misses a child but the parent had OCR,
    re-run zh-TW OCR on the child's PNG and cache the result.

    Only fires for children whose slide-wide attribution finds 0 lines AND whose
    parent did have lines — so the normal case (a cut through whitespace, both
    children pick up their lines via center-in-bbox) pays nothing. Cache path:
        output/slide_##/edits/<child_stem>.ocr.json
    Shape mirrors composition.json's layer.ocr block: {text, confidence, line_count}.
    """
    ocr_path = slide_dir / "slide_ocr.json"
    if not ocr_path.exists():
        return
    try:
        slide_lines = json.loads(ocr_path.read_text(encoding="utf-8")).get("lines", [])
    except (json.JSONDecodeError, OSError):
        return
    if not slide_lines:
        return

    def _attributed(bbox, lines):
        x, y, w, h = bbox
        x2, y2 = x + w, y + h
        out = []
        for ln in lines:
            lx, ly, lw, lh = ln["bbox"]
            cx, cy = lx + lw / 2, ly + lh / 2
            if x <= cx < x2 and y <= cy < y2:
                out.append(ln)
        return out

    if not _attributed(parent_bbox, slide_lines):
        return  # parent had no OCR — nothing useful to recover for children
    needs = [(cname, cbbox) for cname, cbbox in children
             if not _attributed(cbbox, slide_lines)]
    if not needs:
        return
    try:
        from ocr_slides import ocr_image  # noqa: WPS433
    except ImportError as exc:
        logs.append(f"re-OCR unavailable (rapidocr not installed): {exc}")
        return
    for cname, _ in needs:
        png = slide_dir / "edits" / cname
        if not png.exists():
            continue
        try:
            payload = ocr_image(png)
        except Exception as exc:  # noqa: BLE001
            logs.append(f"re-OCR failed for {cname}: {exc}")
            continue
        lines = payload.get("lines", [])
        if lines:
            text = " ".join(l["text"] for l in lines if l.get("text"))
            conf = round(sum(l["confidence"] for l in lines) / len(lines), 3)
            cached = {"text": text, "confidence": conf, "line_count": len(lines)}
        else:
            cached = {"text": "", "confidence": 0.0, "line_count": 0}
        cache_path = slide_dir / "edits" / f"{Path(cname).stem}.ocr.json"
        cache_path.write_text(
            json.dumps(cached, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logs.append(f"re-OCR {cname}: {cached['line_count']} lines, conf={cached['confidence']}")


HISTORY_DIR_NAME = ".overrides_history"
HISTORY_MAX = 20


def _is_notes_only(incoming):
    """Notes-only payloads (typed into the slide notes box on every keystroke)
    don't deserve a history slot — they'd burn the ring buffer in seconds."""
    for k, v in (incoming or {}).items():
        if not k.startswith("slide_"):
            return False
        if not isinstance(v, dict):
            return False
        if any(sub != "notes" for sub in v.keys()):
            return False
    return True


def _snapshot_overrides(task_dir):
    """Snapshot the CURRENT overrides.json before it's overwritten.

    Ring buffer under .overrides_history/NNNN.json keeps the most recent
    HISTORY_MAX snapshots; /undo pops the highest-numbered one.
    """
    hd = task_dir / HISTORY_DIR_NAME
    hd.mkdir(parents=True, exist_ok=True)
    snap = load_json(task_dir / "overrides.json") or {}
    existing = sorted(hd.glob("*.json"))
    idx = (int(existing[-1].stem) if existing else 0) + 1
    write_json(hd / f"{idx:04d}.json", snap)
    # Trim oldest beyond HISTORY_MAX.
    keep_threshold = idx - HISTORY_MAX
    for old in existing:
        if int(old.stem) <= keep_threshold:
            old.unlink()


def undo_overrides(task_dir):
    """Pop the latest snapshot, restore it as overrides.json, rebuild.

    Re-runs TTS + timeline only when narration/voice differ between the
    current state and the snapshot (mirror of apply_overrides' audio_changed).
    """
    logs = []
    hd = task_dir / HISTORY_DIR_NAME
    snaps = sorted(hd.glob("*.json")) if hd.is_dir() else []
    if not snaps:
        logs.append("Nothing to undo.")
        return logs
    most_recent = snaps[-1]
    snap = load_json(most_recent) or {}
    cur = load_json(task_dir / "overrides.json") or {}
    slide_keys = {k for k in set(cur) | set(snap) if k.startswith("slide_")}
    audio_changed = (cur.get("voice") != snap.get("voice")) or any(
        (cur.get(k) or {}).get("narration") != (snap.get(k) or {}).get("narration")
        for k in slide_keys
    )
    if snap:
        write_json(task_dir / "overrides.json", snap)
    else:
        (task_dir / "overrides.json").unlink(missing_ok=True)
    most_recent.unlink()
    logs.append(f"Restored snapshot {most_recent.name} ({len(snaps) - 1} history entries left)")

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
        log("undo: narration/voice differ from snapshot -- re-running TTS + timeline")
        step("tts_edge.py", "TTS")
        step("build_timeline.py", "Timeline")
    step("build_composition.py", "composition.json")
    return logs


def history_depth(task_dir):
    hd = task_dir / HISTORY_DIR_NAME
    return len(list(hd.glob("*.json"))) if hd.is_dir() else 0


def apply_overrides(task_dir, incoming):
    """Persist edits to <task>/overrides.json and rebuild the resolved view.

    Generated artifacts stay pristine. Narration/voice edits also re-run
    TTS + timeline (they change the audio); layer/notes edits only need the
    composition rebuilt. Bbox edits trigger a per-layer re-extract so the
    rendered PNG matches the new frame.

    Snapshots the prior overrides.json into the .overrides_history/ ring
    buffer so /undo can restore it. Notes-only payloads are exempt — those
    fire on every keystroke and would saturate the buffer.
    """
    logs = []
    if not _is_notes_only(incoming):
        _snapshot_overrides(task_dir)
    ov_path = task_dir / "overrides.json"
    merged = deep_merge(load_json(ov_path) or {}, incoming)
    write_json(ov_path, merged)
    logs.append("Saved edits to overrides.json (generated artifacts untouched)")
    _reextract_layer_edits(task_dir, incoming, logs)

    # Trigger TTS only when THIS apply touches voice or a slide's narration —
    # not whenever the merged state happens to contain them. Otherwise once
    # voice is set, every later edit (hide, z-bump, bbox) queues a 40 s TTS run.
    audio_changed = bool(incoming.get("voice")) or any(
        isinstance(k, str) and k.startswith("slide_")
        and isinstance(v, dict) and "narration" in v
        for k, v in (incoming or {}).items()
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

    # Caption style is a task-level overlay onto caption_config.json. Render
    # reads config.CAPTION at startup, so the next MP4 export picks it up;
    # build_composition also bakes a copy into composition.json for UI.
    if isinstance(incoming.get("caption_style"), dict):
        cap_path = task_dir / "caption_config.json"
        merged_caps = deep_merge(load_json(cap_path) or {}, incoming["caption_style"])
        write_json(cap_path, merged_caps)
        logs.append("Updated caption_config.json")

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


def _parse_multipart(handler):
    """Pull file + text fields out of a multipart/form-data POST.

    Returns {field_name: (filename_or_None, bytes)} or None if not multipart.
    """
    ct = handler.headers.get("Content-Type", "")
    if "multipart/form-data" not in ct.lower():
        return None
    length = int(handler.headers.get("Content-Length", 0))
    if not length:
        return {}
    body = handler.rfile.read(length)
    raw = f"Content-Type: {ct}\r\n\r\n".encode() + body
    msg = BytesParser(policy=default_policy).parsebytes(raw)
    if not msg.is_multipart():
        return {}
    fields = {}
    for part in msg.iter_parts():
        name = part.get_param("name", header="Content-Disposition")
        if not name:
            continue
        filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        fields[name] = (filename, payload)
    return fields


def _add_to_task_index(name):
    """Register the task in task-index.json so the UI dropdown sees it.

    UI's loadTask now tolerates missing composition.json (renders a pending
    state with next-step instructions), so it's safe to list freshly ingested
    tasks here.
    """
    idx_path = ROOT / "task-index.json"
    try:
        idx = json.loads(idx_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        idx = []
    entry = {"path": f"task={name}", "label": name}
    if any(e.get("path") == entry["path"] for e in idx):
        return False
    idx.append(entry)
    idx_path.write_text(
        json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return True


def _remove_from_task_index(task_path):
    """Drop the task entry from task-index.json. Returns True if an entry was removed."""
    idx_path = ROOT / "task-index.json"
    try:
        idx = json.loads(idx_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return False
    kept = [e for e in idx if e.get("path") != task_path]
    if len(kept) == len(idx):
        return False
    idx_path.write_text(
        json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return True


def delete_task(task_dir):
    """Delete a task directory and unregister it from task-index.json.

    Guard: only a `task=<name>/` directory directly under ROOT may be removed —
    never ROOT itself or anything outside it.
    """
    import shutil

    if task_dir == ROOT or task_dir.parent != ROOT or not task_dir.name.startswith("task="):
        return 400, {"status": "error",
                     "message": "refusing to delete: not a task directory"}
    removed = _remove_from_task_index(task_dir.name)
    shutil.rmtree(task_dir, ignore_errors=True)
    return 200, {"status": "ok", "message": f"deleted {task_dir.name}",
                 "indexRemoved": removed}


def ingest_deck(task_name, filename, data):
    """Stage an uploaded deck under task=<name>/, converting PPTX → PDF if needed.

    Does not run the segmentation pipeline; that stays a manual step (it can be
    slow and needs network/TTS). Adds the task to task-index.json so the UI
    dropdown surfaces it immediately; the UI shows a pending state until
    composition.json appears.
    """
    name = (task_name or "").strip()
    if not name or not TASK_NAME_RE.fullmatch(name):
        return 400, {"status": "error",
                     "message": "invalid task name (use letters, digits, '.', '_', '-')"}
    if not filename:
        return 400, {"status": "error", "message": "no filename in upload"}
    suffix = Path(filename).suffix.lower()
    if suffix not in INGEST_SUFFIXES:
        return 400, {"status": "error",
                     "message": f"unsupported file type {suffix!r} (need .pdf, .pptx, .ppt)"}

    task_dir = ROOT / f"task={name}"
    task_dir.mkdir(parents=True, exist_ok=True)
    saved_path = task_dir / Path(filename).name
    saved_path.write_bytes(data)
    rel = lambda p: str(Path(p).relative_to(ROOT)).replace("\\", "/")
    logs = [f"Saved {saved_path.name} → {rel(saved_path)} ({len(data)//1024} KB)"]

    pdf_path = saved_path
    if suffix in {".pptx", ".ppt"}:
        sys.path.insert(0, str(SKILL_DIR))
        try:
            from convert_pptx_to_pdf import convert_to_pdf  # noqa: WPS433
            pdf_path = convert_to_pdf(saved_path, task_dir)
            logs.append(f"Converted → {rel(pdf_path)}")
        except Exception as exc:  # noqa: BLE001 — surface the message to the UI
            logs.append(f"Conversion failed: {exc}")
            return 500, {"status": "error", "message": "\n".join(logs)}
        finally:
            sys.path.pop(0)

    added = _add_to_task_index(name)
    logs.append(f"task-index.json: {'added' if added else 'already present'}")
    logs.append(
        "Next: run the pipeline in this directory: "
        "render_slides.py → tts_edge.py → segment_elements.py → "
        "build_timeline.py → build_composition.py. "
        "The task is in the dropdown now; it'll show a pending state until "
        "composition.json is built."
    )
    return 200, {
        "status": "ok",
        "task": f"task={name}",
        "pdf": rel(pdf_path),
        "logs": logs,
    }


_OCR_NUMERIC_RE = re.compile(r"^[\d.,:%+/=()\-\s]+$")  # chart axis ticks / bare numbers


def _clean_ocr_lines(raw_lines):
    """Drop OCR layout noise and dedupe, preserving order. Seeds a narration
    draft from slide OCR — filters single-char fragments, deck-footer
    watermarks, and pure-number lines (chart axis ticks like '9400' / '0.930').
    Formula soup and diagram labels can't be filtered safely and will remain."""
    seen, out = set(), []
    for ln in raw_lines:
        s = ln.strip()
        if len(s) < 2:                  # single-char / empty layout fragments
            continue
        if "notebook" in s.lower():     # deck footer watermark (OCR-mangled variants)
            continue
        if _OCR_NUMERIC_RE.match(s):    # chart axis ticks / bare numbers
            continue
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def make_starter_narration(task_dir):
    """Create narration/narration_script.md with one section per slide.

    Slide count comes from output/slide_##/ directories (created by
    render_slides.py). When slide_ocr.json exists (from ocr_slides.py) the
    section body is seeded with the OCR text so the user has real content to
    polish; otherwise a placeholder line is written. Either way the pipeline
    can complete; the user edits in the UI's narration editor afterward.
    """
    output_dir = task_dir / "output"
    slide_dirs = sorted(output_dir.glob("slide_*")) if output_dir.is_dir() else []
    if not slide_dirs:
        return None, "no slide_##/ directories under output/ — run render_slides first"
    narration_dir = task_dir / "narration"
    narration_dir.mkdir(parents=True, exist_ok=True)
    md_path = narration_dir / "narration_script.md"
    if md_path.exists():
        return None, f"{md_path.name} already exists — not overwriting"
    sections = [
        f"# {task_dir.name} — 旁白腳本\n"
        "\n以下為 OCR 自動草稿：只是投影片上的文字片段，尚未口語化。"
        "請在 UI 的旁白編輯器中改寫成順暢的旁白。（此行不會被唸出來）"
    ]
    ocr_hits = 0
    for sd in slide_dirs:
        num = sd.name.replace("slide_", "")
        title, body = None, None
        ocr_path = sd / "slide_ocr.json"
        if ocr_path.exists():
            try:
                ocr = json.loads(ocr_path.read_text(encoding="utf-8"))
                lines = _clean_ocr_lines((ocr.get("text") or "").splitlines())
                if lines:
                    title = lines[0]
                    body = "，".join(lines) + "。"
                    ocr_hits += 1
            except (json.JSONDecodeError, OSError):
                pass
        header = f"## Slide {num}" + (f" - {title}" if title else "")
        if not body:
            body = f"這是第 {int(num)} 張投影片的佔位旁白，請在 UI 的旁白編輯器中改寫成實際內容。"
        sections.append(f"\n{header}\n\n{body}")
    md_path.write_text("\n".join(sections), encoding="utf-8")
    src = (
        f"seeded from OCR for {ocr_hits}/{len(slide_dirs)} slides"
        if ocr_hits else "placeholder text (run pipeline first so OCR can seed real content)"
    )
    return md_path, f"wrote {len(slide_dirs)} sections to {md_path.relative_to(ROOT)} — {src}"


def run_pipeline(task_dir):
    """Run the five-step pipeline (render → tts → segment → timeline → composition).

    Each step runs as a subprocess with cwd=task_dir so it picks up the
    task's PDF, narration, output/ etc. Stops at the first hard error; TTS is
    skipped (not failed) when narration_script.md is missing — the UI surfaces
    that as the next action for a fresh deck.
    """
    import time

    pdfs = sorted(task_dir.glob("*.pdf"))
    if not pdfs:
        return [{"step": "find_pdf", "status": "error",
                 "log": "no .pdf in task directory — upload a deck first"}]
    pdf = pdfs[0]
    narration_md = task_dir / "narration" / "narration_script.md"

    plan = [
        ("render_slides.py", [str(pdf)], True, ""),
        ("ocr_slides.py", [], True, ""),
        ("auto_narration", [], True, ""),  # in-process: seed narration from OCR if missing
        ("tts_edge.py", [], True, ""),
        ("segment_elements.py", [], True, ""),
        ("build_timeline.py", [], True, ""),
        ("align_subtitles.py", [], True, ""),  # no-op when whisper-timestamped not installed
        ("build_composition.py", [], True, ""),
    ]

    results = []
    halted = False
    for name, args, cond, hint in plan:
        if halted:
            results.append({"step": name, "status": "skipped",
                            "log": "previous step failed"})
            continue
        if not cond:
            results.append({"step": name, "status": "skipped", "log": hint})
            halted = True  # downstream steps need this step's output
            continue
        if name == "auto_narration":
            if narration_md.exists():
                results.append({"step": "auto_narration", "status": "skipped",
                                "log": "narration_script.md present — using your script"})
            else:
                path, msg = make_starter_narration(task_dir)
                if path is None:
                    results.append({"step": "auto_narration", "status": "error", "log": msg})
                    halted = True
                else:
                    results.append({"step": "auto_narration", "status": "ok", "log": msg})
            continue
        script = SKILL_DIR / name
        if not script.exists():
            results.append({"step": name, "status": "error",
                            "log": f"script not found: {script}"})
            halted = True
            continue
        t0 = time.time()
        try:
            r = subprocess.run(
                [sys.executable, str(script)] + args,
                capture_output=True, text=True, cwd=str(task_dir), timeout=900,
            )
        except subprocess.TimeoutExpired:
            results.append({"step": name, "status": "error",
                            "elapsed": 900, "log": "timed out after 15 min"})
            halted = True
            continue
        elapsed = round(time.time() - t0, 1)
        out = (r.stdout or "").strip()
        err = (r.stderr or "").strip()
        log = (out + ("\n" + err if err else "")).strip()[:1200]
        ok = r.returncode == 0
        results.append({
            "step": name,
            "status": "ok" if ok else "error",
            "elapsed": elapsed,
            "log": log or ("(no output)" if ok else "(no output)"),
        })
        if not ok:
            halted = True
    return results


ASPECT_PRESETS = {
    "16:9": {"width": 1920, "height": 1080},
    "9:16": {"width": 1080, "height": 1920},
    "1:1":  {"width": 1080, "height": 1080},
}


def set_aspect(task_dir, aspect):
    """Rebuild a task at a new canvas aspect ratio.

    Pipeline coordinates are in pixel space, so changing canvas dimensions
    invalidates per-layer bboxes / splits / OCR caches / composition. We:
      1. Merge new {aspect, width, height} into task's project_config.json
         (preserves fps + render section).
      2. Delete stale artifacts (output/slide_*/, overrides.json, composition.json,
         narration/narration_timing.json). Keep audio/ and narration_script.md —
         narration text is aspect-agnostic.
      3. Run render → ocr → segment → timeline → composition. Skip TTS (audio
         doesn't depend on canvas). Returns per-step results like /pipeline.
    """
    import shutil
    import time

    preset = ASPECT_PRESETS.get(aspect)
    if not preset:
        return [{"step": "aspect", "status": "error",
                 "log": f"unknown aspect {aspect!r} (use {list(ASPECT_PRESETS)})"}]
    pdfs = sorted(task_dir.glob("*.pdf"))
    if not pdfs:
        return [{"step": "find_pdf", "status": "error",
                 "log": "no .pdf in task directory — upload a deck first"}]
    pdf = pdfs[0]

    cfg_path = task_dir / "project_config.json"
    cfg = load_json(cfg_path) or {}
    canvas = dict(cfg.get("canvas") or {})
    canvas.update({"aspect": aspect, "width": preset["width"], "height": preset["height"]})
    canvas.setdefault("fps", 30)
    cfg["canvas"] = canvas
    write_json(cfg_path, cfg)

    cleared = []
    for sd in sorted((task_dir / "output").glob("slide_*")) if (task_dir / "output").is_dir() else []:
        shutil.rmtree(sd, ignore_errors=True)
        cleared.append(sd.name)
    for stale in (task_dir / "overrides.json",
                  task_dir / "composition.json",
                  task_dir / "narration" / "narration_timing.json"):
        if stale.exists():
            stale.unlink()
            cleared.append(str(stale.relative_to(task_dir)).replace("\\", "/"))

    plan = [
        ("render_slides.py", [str(pdf)]),
        ("ocr_slides.py",    []),
        ("segment_elements.py", []),
        ("build_timeline.py", []),
        ("build_composition.py", []),
    ]
    results = [{"step": "set_aspect", "status": "ok",
                "log": f"canvas → {aspect} ({preset['width']}×{preset['height']}); "
                       f"cleared {len(cleared)} item(s)"}]
    halted = False
    for name, args in plan:
        if halted:
            results.append({"step": name, "status": "skipped",
                            "log": "previous step failed"})
            continue
        script = SKILL_DIR / name
        if not script.exists():
            results.append({"step": name, "status": "error",
                            "log": f"script not found: {script}"})
            halted = True
            continue
        t0 = time.time()
        try:
            r = subprocess.run(
                [sys.executable, str(script)] + args,
                capture_output=True, text=True, cwd=str(task_dir), timeout=900,
            )
        except subprocess.TimeoutExpired:
            results.append({"step": name, "status": "error",
                            "elapsed": 900, "log": "timed out after 15 min"})
            halted = True
            continue
        elapsed = round(time.time() - t0, 1)
        out = (r.stdout or "").strip()
        err = (r.stderr or "").strip()
        log = (out + ("\n" + err if err else "")).strip()[:1200]
        ok = r.returncode == 0
        results.append({"step": name, "status": "ok" if ok else "error",
                        "elapsed": elapsed,
                        "log": log or ("(no output)" if ok else "(no output)")})
        if not ok:
            halted = True
    return results


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


def _prune_task_index():
    """Drop entries whose directory no longer exists and rewrite the file."""
    idx_path = ROOT / "task-index.json"
    try:
        idx = json.loads(idx_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return
    if not isinstance(idx, list):
        return
    kept = [e for e in idx if isinstance(e, dict)
            and isinstance(e.get("path"), str)
            and (ROOT / e["path"]).is_dir()]
    if len(kept) != len(idx):
        idx_path.write_text(
            json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8"
        )


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0].lstrip("/")
        # Send bare `/` to the canonical UI root so relative <link>/<script>
        # paths in index.html resolve correctly (otherwise the browser asks
        # for /styles.css instead of /pipeline-ui/styles.css and the page
        # silently breaks).
        if not path:
            self.send_response(302)
            self.send_header("Location", "/pipeline-ui/")
            self.end_headers()
            return
        if path == "task-index.json":
            _prune_task_index()
        file_path = (ROOT / path).resolve()
        if file_path.is_dir():
            file_path = file_path / "index.html"
        if (ROOT in file_path.parents or file_path == ROOT) and file_path.is_file():
            try:
                self.send_response(200)
                self.send_header("Content-Type", MIME.get(file_path.suffix.lower(), "application/octet-stream"))
                self.end_headers()
                self.wfile.write(file_path.read_bytes())
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                pass  # client navigated away mid-response; ignore
        else:
            try:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"not found")
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                pass

    def _body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length)) if length else {}

    def do_POST(self):
        route = self.path.split("?")[0]
        if route == "/ingest":
            fields = _parse_multipart(self)
            if fields is None:
                return self._json(400, {"status": "error",
                                        "message": "expected multipart/form-data"})
            task_name = (fields.get("task", (None, b""))[1] or b"").decode("utf-8", "ignore")
            file_field = fields.get("file")
            if not file_field:
                return self._json(400, {"status": "error",
                                        "message": "missing 'file' field"})
            filename, data = file_field
            code, body_out = ingest_deck(task_name, filename, data)
            return self._json(code, body_out)
        try:
            body = self._body()
        except Exception as e:
            return self._json(400, {"status": "error", "message": str(e)})
        task_dir = safe_task(body.get("task"))
        if not task_dir:
            return self._json(400, {"status": "error", "message": "missing or invalid 'task'"})
        if route == "/apply":
            logs = apply_overrides(task_dir, body.get("overrides") or {})
            self._json(200, {"status": "ok", "logs": logs,
                              "history_depth": history_depth(task_dir)})
        elif route == "/undo":
            logs = undo_overrides(task_dir)
            self._json(200, {"status": "ok", "logs": logs,
                              "history_depth": history_depth(task_dir)})
        elif route == "/history-depth":
            self._json(200, {"status": "ok", "history_depth": history_depth(task_dir)})
        elif route == "/suggest":
            if not body.get("slide"):
                return self._json(400, {"status": "error", "message": "missing 'slide'"})
            self._json(200, {"status": "ok", "suggestions": suggest_slide(task_dir, int(body["slide"]))})
        elif route == "/render":
            out = render_task(task_dir, body.get("from", 1), body.get("to", 99))
            self._json(200, {"status": "ok", "output": out})
        elif route == "/pipeline":
            steps = run_pipeline(task_dir)
            self._json(200, {"status": "ok", "steps": steps})
        elif route == "/aspect":
            aspect = body.get("aspect")
            if not isinstance(aspect, str):
                return self._json(400, {"status": "error",
                                        "message": "missing 'aspect' (16:9 | 9:16 | 1:1)"})
            steps = set_aspect(task_dir, aspect)
            self._json(200, {"status": "ok", "steps": steps})
        elif route == "/delete-task":
            code, body_out = delete_task(task_dir)
            self._json(code, body_out)
        elif route == "/starter-narration":
            path, msg = make_starter_narration(task_dir)
            if path is None:
                self._json(400, {"status": "error", "message": msg})
            else:
                self._json(200, {"status": "ok", "path": str(path.relative_to(ROOT)).replace("\\", "/"), "message": msg})
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


def _lan_ip():
    """Return the LAN-facing IPv4 (works on Windows; doesn't actually send)."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip if ip and ip != "127.0.0.1" else None
    except Exception:
        return None


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9001
    host = sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1"
    acked_no_auth = len(sys.argv) > 3 and sys.argv[3] == "share"
    if host == "0.0.0.0" and not acked_no_auth:
        print(
            "WARNING: binding to 0.0.0.0 exposes this server (no auth, no\n"
            "         upload size cap) to your whole LAN. Re-run with the\n"
            "         literal third arg 'share' to acknowledge:\n"
            f"           python pipeline_server.py {port} 0.0.0.0 share\n"
            "         Or stay on 127.0.0.1 for local-only use.",
            file=sys.stderr,
        )
        sys.exit(1)
    server = HTTPServer((host, port), Handler)
    print(f"Pipeline server on http://localhost:{port}/pipeline-ui/")
    if host == "0.0.0.0":
        lan = _lan_ip()
        if lan:
            print(f"  LAN access:  http://{lan}:{port}/pipeline-ui/")
        print("  (no auth — only share this URL with people you trust)")
    print(f"Serving repo root: {ROOT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
