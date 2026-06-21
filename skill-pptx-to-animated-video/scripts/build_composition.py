"""Build composition.json -- the renderer-neutral "resolved" contract.

Merges project/caption config + per-slide metadata + narration timing into a
single document that any renderer can consume: the custom ffmpeg renderer, the
browser draft preview, or a future HyperFrames adapter. It is READ-ONLY with
respect to every existing pipeline artifact -- the only file it writes is
composition.json at the project root.

Animations are kept as semantic names (fade-in-up, zoom-in, ...) plus an
intensity (1.0 = each renderer's default magnitude); no ffmpeg/CSS dialect
leaks into the contract.

Run from the project root, after build_timeline.py.
"""

import json
from pathlib import Path

import config
import media
import overrides

ROOT = Path.cwd()
OUT = ROOT / "output"
NARRATION = ROOT / "narration"


def _layer_ocr(bbox, ocr_lines):
    """Aggregate slide_ocr.json lines that fall inside the layer bbox.

    A line is attributed to a layer when its bbox center is inside the layer's
    bbox (each line attaches to exactly one layer; non-overlapping segmentations
    don't double-count). The text is the joined per-line text; the UI truncates
    for display and shows the full version in an expand modal.
    """
    x, y, w, h = bbox
    x2, y2 = x + w, y + h
    inside = []
    for line in ocr_lines:
        lx, ly, lw, lh = line["bbox"]
        cx, cy = lx + lw / 2, ly + lh / 2
        if x <= cx < x2 and y <= cy < y2:
            inside.append(line)
    if not inside:
        return None  # caller omits the ocr field when there's nothing to attach
    text = " ".join(l["text"] for l in inside if l.get("text"))
    conf = round(sum(l["confidence"] for l in inside) / len(inside), 3)
    return {"text": text, "confidence": conf, "line_count": len(inside)}


def _apply_corrected_ocr(layer_ov, ocr_block):
    """Promote `overrides.layers.<name>.ocr_corrected` into the layer's ocr.

    When a corrected string is present we treat it as ground truth: replace
    `text`, set `confidence` to 1.0, mark `corrected: true`. If the layer has
    no auto-attributed OCR at all (line_count == 0 / None), the correction
    still wins and produces an ocr block with line_count = 0.
    """
    corrected = (layer_ov or {}).get("ocr_corrected")
    if corrected is None:
        return ocr_block
    text = str(corrected).strip()
    if not text:
        return ocr_block
    base = dict(ocr_block) if ocr_block else {"line_count": 0}
    base["text"] = text
    base["confidence"] = 1.0
    base["corrected"] = True
    return base


def _emit_split_child(parent_entry, cname, cbbox, ov, n, key, ocr_lines, duration):
    """Build one split child's composition entry from its parent's; recurse
    when the child itself has a split spec so grandchildren land in the
    flat layer list."""
    child = dict(parent_entry)
    child["id"] = overrides.stable_id(n, cname)
    child_ov = overrides.layer(ov, n, cname)
    # Bbox: child-level override wins over the cut-derived bbox.
    child_bbox_o = child_ov.get("bbox")
    if child_bbox_o:
        cbbox = [int(v) for v in child_bbox_o]
    edit_c = OUT / key / "edits" / cname
    child["image"] = (
        f"output/{key}/edits/{cname}" if edit_c.exists() else parent_entry["image"]
    )
    child["bbox"] = cbbox
    if child_ov.get("z") is not None:
        child["z"] = child_ov["z"]
    if child_ov.get("animation"):
        child["enter"] = {
            "type": child_ov["animation"],
            "intensity": child_ov.get("intensity", parent_entry["enter"]["intensity"]),
        }
    if child_ov.get("duration") is not None:
        child["duration"] = child_ov["duration"]
    if child_ov.get("start") is not None:
        child["start"] = overrides.resolved_start(
            child["start"], child_ov, duration, child["duration"]
        )
    if overrides.is_hidden(child_ov):
        child["hidden"] = True
    elif child_ov.get("hidden") is False:
        child.pop("hidden", None)
    cg = overrides.merge_group_of(child_ov)
    if cg:
        child["merge_group"] = cg
    # Prefer the per-child re-OCR cache (written by the server when
    # center-attribution misses) over slide-wide center-in-bbox aggregation.
    cache_path = OUT / key / "edits" / f"{Path(cname).stem}.ocr.json"
    auto_ocr = None
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if cached.get("line_count"):
                auto_ocr = cached
        except (json.JSONDecodeError, OSError):
            pass
    if auto_ocr is None:
        auto_ocr = _layer_ocr(cbbox, ocr_lines)
    c_ocr = _apply_corrected_ocr(child_ov, auto_ocr)
    if c_ocr:
        child["ocr"] = c_ocr
    else:
        child.pop("ocr", None)
    # Recurse: if this child has its own split spec, expand grandchildren.
    child_spec = overrides.split_of(child_ov)
    if child_spec:
        out = []
        for gcname, gcbbox in overrides.split_children(cname, cbbox, child_spec):
            out.extend(_emit_split_child(child, gcname, gcbbox, ov, n, key, ocr_lines, duration))
        return out
    return [child]


def caption_style():
    cap = config.CAPTION
    return {
        "font": cap["font"],
        "size": cap["size"],
        "primary_colour": cap["primary_colour"],
        "back_colour": cap["back_colour"],
        "border_style": cap["border_style"],
        "outline": cap["outline"],
        "shadow": cap["shadow"],
        "margins": [cap["margin_l"], cap["margin_r"], cap["margin_v"]],
        "letterbox_height": cap["letterbox_height"],
        "band_color": cap["band_color"],
        "chunk_max_chars": cap["chunk_max_chars"],
        "tail_silence": cap["tail_silence"],
    }


def main():
    timing = json.loads((NARRATION / "narration_timing.json").read_text(encoding="utf-8"))
    ov = overrides.load()
    voice_changed = bool(overrides.voice(ov))
    slides = []
    for p in sorted(OUT.glob("slide_*/metadata.json")):
        meta = json.loads(p.read_text(encoding="utf-8"))
        n = meta["slide"]
        key = f"slide_{n:02d}"
        t = timing.get(key, {})
        # Per-slide OCR (optional) lets us attribute lines to layers. Layers
        # without OCR lines (or whose slide_ocr.json is missing) get an empty
        # ocr block; the UI treats it as "no OCR evidence" and doesn't flag.
        ocr_path = OUT / key / "slide_ocr.json"
        ocr_lines = []
        if ocr_path.exists():
            try:
                ocr_lines = json.loads(ocr_path.read_text(encoding="utf-8")).get("lines", [])
            except (json.JSONDecodeError, OSError):
                ocr_lines = []
        narration = overrides.narration(ov, n)
        # Refresh duration from current audio only for edited slides.
        refresh = voice_changed or narration is not None
        audio_path = ROOT / f"audio/slide_{n:02d}_voiceover.mp3"
        duration = media.slide_duration(audio_path, meta["duration"]) if refresh else meta["duration"]
        raw = [(l, overrides.layer(ov, n, l["name"])) for l in meta["layers"]]
        # P1 #5: per-layer starts now live in timing (recomputed from the
        # current audio duration on every build_timeline run). Fall back to
        # metadata's baked start if the timing file pre-dates this field.
        layer_starts = t.get("layer_starts") or {}
        # Pass 1: collect group primaries (first member by name).
        primaries = {}
        for l, o in raw:
            g = overrides.merge_group_of(o)
            if g and g not in primaries:
                primaries[g] = (l, o)
        # Pass 2: build resolved layers, group members inherit start/anim from primary.
        layers = []
        for l, o in raw:
            g = overrides.merge_group_of(o)
            primary_l, primary_o = primaries[g] if g else (l, o)
            layer_dur = o.get("duration", l["duration"])
            anim = o.get("animation", primary_o.get("animation", primary_l["animation"]))
            primary_name = primary_l["name"] if g else l["name"]
            baked_start = layer_starts.get(primary_name, primary_l["start"] if g else l["start"])
            start_o = o if o.get("start") is not None else primary_o
            # bbox override + matching edits/<name> PNG come together via /apply.
            # Fall back to the original PNG + baked bbox when either is missing.
            edit_path = OUT / key / "edits" / l["name"]
            bbox_o = o.get("bbox")
            if bbox_o and edit_path.exists():
                image = f"output/{key}/edits/{l['name']}"
                bbox = [int(v) for v in bbox_o]
            else:
                image = f"output/{key}/{l['name']}"
                bbox = [l["x"], l["y"], l["width"], l["height"]]
            entry = {
                "id": overrides.stable_id(n, l["name"]),
                "image": image,
                "type": l["type"],
                "bbox": bbox,
                "z": o.get("z", l["z_index"]),
                "enter": {"type": anim, "intensity": o.get("intensity", 1.0)},
                "start": overrides.resolved_start(baked_start, start_o, duration, layer_dur),
                "duration": layer_dur,
            }
            ocr = _apply_corrected_ocr(o, _layer_ocr(bbox, ocr_lines))
            if ocr:
                entry["ocr"] = ocr
            if g:
                entry["merge_group"] = g
            if overrides.is_hidden(o):
                entry["hidden"] = True
            # Split: emit children that inherit from the parent entry.
            # Recursive: if a child has its own split spec, expand its children
            # too (and so on). Each level resolves its own bbox / hide / z /
            # anim / start / duration / merge_group / OCR overrides.
            spec = overrides.split_of(o)
            if spec:
                for cname, cbbox in overrides.split_children(l["name"], bbox, spec):
                    layers.extend(
                        _emit_split_child(entry, cname, cbbox, ov, n, key, ocr_lines, duration)
                    )
            else:
                layers.append(entry)
        slides.append(
            {
                "index": n,
                "background": f"output/{key}/background.png",
                "audio": t.get("voiceover_file"),
                "start": t.get("start"),
                "end": t.get("end"),
                "duration": duration,
                "narration": narration if narration is not None else t.get("script", ""),
                "layers": layers,
            }
        )
    composition = {
        "canvas": config.PROJECT["canvas"],
        "transition": {"type": "crossfade", "duration": config.PROJECT["render"]["transition"]},
        "caption_style": caption_style(),
        "slides": slides,
    }
    (ROOT / "composition.json").write_text(
        json.dumps(composition, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"composition.json: {len(slides)} slides, {sum(len(s['layers']) for s in slides)} layers")


if __name__ == "__main__":
    main()
