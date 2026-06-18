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

ROOT = Path.cwd()
OUT = ROOT / "output"
NARRATION = ROOT / "narration"


def stable_id(slide, name):
    """slide_02_table_01.png -> s02-table-01 (stable while the filename is)."""
    parts = name.rsplit(".", 1)[0].split("_")  # [slide, 02, table, 01]
    return f"s{int(slide):02d}-" + "-".join(parts[2:])


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
    slides = []
    for p in sorted(OUT.glob("slide_*/metadata.json")):
        meta = json.loads(p.read_text(encoding="utf-8"))
        n = meta["slide"]
        key = f"slide_{n:02d}"
        t = timing.get(key, {})
        layers = [
            {
                "id": stable_id(n, l["name"]),
                "image": f"output/{key}/{l['name']}",
                "bbox": [l["x"], l["y"], l["width"], l["height"]],
                "z": l["z_index"],
                "enter": {"type": l["animation"], "intensity": 1.0},
                "start": l["start"],
                "duration": l["duration"],
            }
            for l in meta["layers"]
        ]
        slides.append(
            {
                "index": n,
                "background": f"output/{key}/background.png",
                "audio": t.get("voiceover_file"),
                "start": t.get("start"),
                "end": t.get("end"),
                "duration": meta["duration"],
                "narration": t.get("script", ""),
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
