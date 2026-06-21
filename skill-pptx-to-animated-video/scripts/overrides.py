"""Overrides layer -- user/AI edits kept separate from generated artifacts.

overrides.json (project root) stores ONLY the differences a user makes, keyed
by slide ("slide_02") then by what they touched. Generated metadata.json and
narration_script.md stay pristine; build_composition.py applies these edits
when producing the resolved composition.json.

Schema:
  {
    "slide_02": {
      "narration": "edited narration text",
      "notes": "free-form note for the agent",
      "layers": {
        "slide_02_table_01.png": { "start": 1.2, "duration": 0.9,
                                    "animation": "zoom-in", "z": 6,
                                    "hidden": false, "merge_group": "g1" }
      }
    }
  }

Segmentation edits (v1):
  - hidden: bool      -> drop the layer from composition entirely
  - merge_group: str  -> any layers sharing the same string in one slide enter
                         together (group members inherit start + animation from
                         the primary, which is the first member by name)
Segmentation edits (v2):
  - bbox: [x,y,w,h]   -> reframed; PNG re-extracted into edits/<name>
Segmentation edits (v3):
  - split: two shapes, both emit two children that inherit timing / z /
    animation / merge_group / hidden from the parent. Each child's PNG is
    re-extracted from original + background.
      cut    -> {axis: "x"|"y", at: 0..1}    single cut on the layer
      bboxes -> {bboxes: [[x,y,w,h],[x,y,w,h]]}    two explicit (possibly
                                                  non-adjacent) regions
Review annotations:
  - ocr_corrected: str  -> human-verified replacement for the layer's OCR text;
                           when present, build_composition emits it as
                           ocr.text with confidence 1.0 + ocr.corrected = true.
                           Stored OUTSIDE generated artifacts (in overrides.json
                           only), so OCR remains a pure derived view of the deck.
"""

import json
from pathlib import Path


def stable_id(slide, name):
    """slide_02_table_01.png -> s02-table-01 (stable while the filename is)."""
    parts = name.rsplit(".", 1)[0].split("_")
    return f"s{int(slide):02d}-" + "-".join(parts[2:])


def slide_key(n):
    return f"slide_{int(n):02d}"


def load(root=None):
    path = (root or Path.cwd()) / "overrides.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def layer(overrides, slide_n, name):
    """Return the override dict for one layer (empty if none)."""
    sl = overrides.get(slide_key(slide_n)) or {}
    return (sl.get("layers") or {}).get(name, {})


def narration(overrides, slide_n):
    """Return overridden narration text for a slide, or None if unedited."""
    sl = overrides.get(slide_key(slide_n)) or {}
    return sl.get("narration")


def voice(overrides):
    """Top-level voice override: {"voice": ..., "rate": ...} (empty if none)."""
    return overrides.get("voice") or {}


def is_hidden(layer_ov):
    """Layer is dropped from the composition entirely."""
    return bool(layer_ov.get("hidden"))


def merge_group_of(layer_ov):
    """The merge group key for a layer ('' if ungrouped)."""
    return layer_ov.get("merge_group") or ""


def split_of(layer_ov):
    """Return a normalised split spec or None.

    Two kinds:
      cut    -> {'kind': 'cut', 'axis': 'x'|'y', 'at': float in (0.05, 0.95)}
      bboxes -> {'kind': 'bboxes', 'bboxes': [[x,y,w,h], [x,y,w,h]]}

    `at` is clamped so cut children always have non-trivial width on each side.
    bbox values are int-coerced; degenerate bboxes (w<=0 or h<=0) cause None.
    """
    spec = layer_ov.get("split")
    if not isinstance(spec, dict):
        return None
    if "bboxes" in spec:
        bxs = spec["bboxes"]
        if not (isinstance(bxs, list) and len(bxs) == 2
                and all(isinstance(b, (list, tuple)) and len(b) == 4 for b in bxs)):
            return None
        try:
            bxs = [[int(v) for v in b] for b in bxs]
        except (TypeError, ValueError):
            return None
        if any(b[2] <= 0 or b[3] <= 0 for b in bxs):
            return None
        return {"kind": "bboxes", "bboxes": bxs}
    axis = spec.get("axis")
    if axis not in ("x", "y"):
        return None
    try:
        at = float(spec.get("at", 0.5))
    except (TypeError, ValueError):
        return None
    return {"kind": "cut", "axis": axis, "at": max(0.05, min(0.95, at))}


def split_children(name, bbox, spec):
    """Two (child_name, child_bbox) pairs from a parent bbox + split spec.

    For 'cut' specs children tile the parent (no overlap, no gap).
    For 'bboxes' specs children are the explicit user-provided regions;
    parent `bbox` is unused (the caller still passes it for symmetry).
    """
    base, _, ext = name.rpartition(".")
    if not ext:
        base, ext = name, "png"
    if spec["kind"] == "bboxes":
        a_bbox, b_bbox = spec["bboxes"]
        return [
            (f"{base}_split_a.{ext}", list(a_bbox)),
            (f"{base}_split_b.{ext}", list(b_bbox)),
        ]
    x, y, w, h = (int(v) for v in bbox)
    if spec["axis"] == "x":
        cut = max(1, min(w - 1, int(round(w * spec["at"]))))
        a_bbox = [x, y, cut, h]
        b_bbox = [x + cut, y, w - cut, h]
    else:
        cut = max(1, min(h - 1, int(round(h * spec["at"]))))
        a_bbox = [x, y, w, cut]
        b_bbox = [x, y + cut, w, h - cut]
    return [
        (f"{base}_split_a.{ext}", a_bbox),
        (f"{base}_split_b.{ext}", b_bbox),
    ]


def resolved_start(baked_start, layer_ov, slide_duration, layer_duration):
    """Layer entrance time: override if set, else baked, capped to the window.

    The cap only bites when the slide got shorter than the baked spread
    assumed (e.g. faster speech); with an unchanged window it is a no-op, so
    unedited output is identical.
    """
    start = layer_ov.get("start", baked_start)
    return min(start, round(slide_duration - layer_duration, 2))
