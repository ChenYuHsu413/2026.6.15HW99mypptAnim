"""Validate composition.json faithfully represents the source artifacts.

Checks that every slide/layer in composition.json matches
output/slide_##/metadata.json (bbox, z, animation, start, duration) and
narration_timing.json (slide start/end/duration), and that all layer ids are
globally unique. Exits non-zero on any mismatch.

Run from the project root, after build_composition.py.
"""

import json
import sys
from pathlib import Path

ROOT = Path.cwd()
OUT = ROOT / "output"
NARRATION = ROOT / "narration"


def main():
    comp = json.loads((ROOT / "composition.json").read_text(encoding="utf-8"))
    timing = json.loads((NARRATION / "narration_timing.json").read_text(encoding="utf-8"))
    metas = {}
    for p in OUT.glob("slide_*/metadata.json"):
        m = json.loads(p.read_text(encoding="utf-8"))
        metas[m["slide"]] = m

    errors = []
    seen_ids = set()

    comp_slides = {s["index"]: s for s in comp["slides"]}
    if set(comp_slides) != set(metas):
        errors.append(f"slide set mismatch: comp={sorted(comp_slides)} meta={sorted(metas)}")

    for n, meta in sorted(metas.items()):
        s = comp_slides.get(n)
        if not s:
            continue
        key = f"slide_{n:02d}"
        t = timing.get(key, {})
        if s["duration"] != meta["duration"]:
            errors.append(f"{key}: duration {s['duration']} != {meta['duration']}")
        if s["start"] != t.get("start") or s["end"] != t.get("end"):
            errors.append(f"{key}: window ({s['start']},{s['end']}) != ({t.get('start')},{t.get('end')})")
        if len(s["layers"]) != len(meta["layers"]):
            errors.append(f"{key}: layer count {len(s['layers'])} != {len(meta['layers'])}")
            continue
        for cl, ml in zip(s["layers"], meta["layers"]):
            if cl["id"] in seen_ids:
                errors.append(f"{key}: duplicate id {cl['id']}")
            seen_ids.add(cl["id"])
            if cl["bbox"] != [ml["x"], ml["y"], ml["width"], ml["height"]]:
                errors.append(f"{key}/{cl['id']}: bbox {cl['bbox']} != {[ml['x'], ml['y'], ml['width'], ml['height']]}")
            if cl["z"] != ml["z_index"]:
                errors.append(f"{key}/{cl['id']}: z {cl['z']} != {ml['z_index']}")
            if cl["enter"]["type"] != ml["animation"]:
                errors.append(f"{key}/{cl['id']}: enter {cl['enter']['type']} != {ml['animation']}")
            if cl["start"] != ml["start"] or cl["duration"] != ml["duration"]:
                errors.append(f"{key}/{cl['id']}: timing ({cl['start']},{cl['duration']}) != ({ml['start']},{ml['duration']})")

    if errors:
        print(f"FAIL ({len(errors)} issue(s)):")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    print(f"OK: {len(comp['slides'])} slides, {len(seen_ids)} layers, all faithful")


if __name__ == "__main__":
    main()
