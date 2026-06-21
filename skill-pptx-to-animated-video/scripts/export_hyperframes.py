"""Composition → HyperFrames export (SPECULATIVE — schema needed).

This file maps everything composition.json knows about a deck into a JSON
shape that *most* timeline-based video tools would accept. It's a stub —
the field names in `_HF_FIELD_MAP` and the animation names in
`_HF_ANIM_NAMES` are guesses. To finish:

  1. Open a vanilla HF (or whatever-target) project export.
  2. Run `python export_hyperframes.py --list-fields` to see what data
     this exporter has available + the current guessed field names.
  3. Update `_HF_FIELD_MAP` / `_HF_ANIM_NAMES` to match the real names.
  4. Drop the `_speculative` field and this warning paragraph.

What's already mapped (no need to rediscover):
  Per project:  canvas {width,height,fps}, transition, caption_style,
                global subtitles path (srt + vtt)
  Per scene:    index, duration, audio, narration text, background image,
                cue list, asset list, OCR text per asset
  Per asset:    src image, bbox xywh, z-order, in_time, in_duration,
                animation name, optional ocr.text/confidence

Usage:
    python export_hyperframes.py [output_path]
    python export_hyperframes.py --list-fields    # diagnostic: prints
                                                  # what data is available
                                                  # + the current name map
Env:
    HF_ABS_PATHS=1   → write absolute file paths instead of relative ones.
Default output: hyperframes/project.hf.json
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path.cwd()
COMP_PATH = ROOT / "composition.json"
HYPER_DIR = ROOT / "hyperframes"

# --- Speculative mapping. Adjust when you have the real HF schema. -----
_HF_FIELD_MAP = {
    "canvas_width":     "width",
    "canvas_height":    "height",
    "canvas_fps":       "fps",
    "scene_duration":   "duration",
    "scene_audio":      "audio_src",
    "scene_background": "background_src",
    "scene_cues":       "cues",
    "subtitles_srt":    "subtitles_srt_src",
    "subtitles_vtt":    "subtitles_vtt_src",
    "asset_src":        "src",
    "asset_x":          "x",
    "asset_y":          "y",
    "asset_w":          "width",
    "asset_h":          "height",
    "asset_z":          "z",
    "asset_in_time":    "in_time",
    "asset_in_dur":     "in_duration",
    "asset_in_anim":    "animation",
}

# semantic-name → HF animation name. Adjust to the HF dialect.
_HF_ANIM_NAMES = {
    "fade-in":      "fadeIn",
    "fade-in-up":   "fadeInUp",
    "fade-in-down": "fadeInDown",
    "zoom-in":      "zoomIn",
    "pop-in":       "popIn",
    "wipe-in":      "wipeIn",
    "draw-in":      "drawIn",
}


def _path(p, abs_paths):
    if p and abs_paths:
        return str((ROOT / p).resolve())
    return p


def _map_asset(layer, abs_paths):
    f = _HF_FIELD_MAP
    asset = {
        f["asset_src"]:     _path(layer["image"], abs_paths),
        f["asset_x"]:       layer["bbox"][0],
        f["asset_y"]:       layer["bbox"][1],
        f["asset_w"]:       layer["bbox"][2],
        f["asset_h"]:       layer["bbox"][3],
        f["asset_z"]:       layer["z"],
        f["asset_in_time"]: layer["start"],
        f["asset_in_dur"]:  layer["duration"],
        f["asset_in_anim"]: _HF_ANIM_NAMES.get(
            layer["enter"]["type"], layer["enter"]["type"]
        ),
    }
    if layer.get("ocr"):
        asset["ocr"] = {
            "text":       layer["ocr"].get("text"),
            "confidence": layer["ocr"].get("confidence"),
        }
    return asset


def _map_scene(slide, abs_paths):
    f = _HF_FIELD_MAP
    assets = [_map_asset(l, abs_paths)
              for l in slide.get("layers", [])
              if not l.get("hidden")]
    cues = [{
        "time":   a[f["asset_in_time"]],
        "asset":  a[f["asset_src"]],
        "action": a[f["asset_in_anim"]],
    } for a in assets]
    return {
        "index":                  slide["index"],
        f["scene_duration"]:      slide["duration"],
        f["scene_audio"]:         _path(slide.get("audio"), abs_paths),
        f["scene_background"]:    _path(slide.get("background"), abs_paths),
        "narration":              slide.get("narration"),
        f["scene_cues"]:          cues,
        "assets":                 assets,
    }


def _list_fields(comp):
    """Print a checklist of mappable fields so the user can match them
    against an actual HF export."""
    f = _HF_FIELD_MAP
    print("Composition data available for export:")
    print(f"  canvas:    {comp['canvas']['width']}x{comp['canvas']['height']} "
          f"@ {comp['canvas'].get('fps', 30)}fps")
    print(f"  scenes:    {len(comp.get('slides', []))}")
    print(f"  assets:    {sum(len(s.get('layers', [])) for s in comp.get('slides', []))}")
    print()
    print("Current field-name guesses (edit _HF_FIELD_MAP in this file):")
    longest = max(len(k) for k in f)
    for k, v in f.items():
        print(f"  {k.ljust(longest)} -> {v!r}")
    print()
    print("Animation-name guesses (edit _HF_ANIM_NAMES in this file):")
    longest = max(len(k) for k in _HF_ANIM_NAMES)
    for k, v in _HF_ANIM_NAMES.items():
        print(f"  {k.ljust(longest)} -> {v!r}")


def main():
    if not COMP_PATH.exists():
        sys.exit("composition.json not found — run build_composition.py first")
    comp = json.loads(COMP_PATH.read_text(encoding="utf-8"))
    if "--list-fields" in sys.argv:
        _list_fields(comp)
        return
    abs_paths = os.environ.get("HF_ABS_PATHS") == "1"
    f = _HF_FIELD_MAP
    srt = ROOT / "narration" / "subtitles.srt"
    vtt = ROOT / "narration" / "subtitles.vtt"
    hf = {
        "_speculative": (
            "This file is a guess at the HyperFrames schema. Verify against "
            "an actual HF export before importing — adjust _HF_FIELD_MAP in "
            "export_hyperframes.py and re-run. Run with --list-fields to see "
            "what data is available."
        ),
        f["canvas_width"]:    comp["canvas"]["width"],
        f["canvas_height"]:   comp["canvas"]["height"],
        f["canvas_fps"]:      comp["canvas"].get("fps", 30),
        "transition":         comp.get("transition"),
        "caption_style":      comp.get("caption_style"),
        f["subtitles_srt"]:   _path("narration/subtitles.srt", abs_paths) if srt.exists() else None,
        f["subtitles_vtt"]:   _path("narration/subtitles.vtt", abs_paths) if vtt.exists() else None,
        "scenes":             [_map_scene(s, abs_paths) for s in comp.get("slides", [])],
    }
    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else HYPER_DIR / "project.hf.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(hf, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        rel = out_path.relative_to(ROOT)
    except ValueError:
        rel = out_path
    print(f"hyperframes export -> {rel} ({len(hf['scenes'])} scenes, "
          f"{sum(len(s['assets']) for s in hf['scenes'])} assets)")
    print("[!] speculative schema -- run with --list-fields to see what's available.")


if __name__ == "__main__":
    main()
