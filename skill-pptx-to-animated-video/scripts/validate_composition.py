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

import media
import overrides

ROOT = Path.cwd()
OUT = ROOT / "output"
NARRATION = ROOT / "narration"


def main():
    comp = json.loads((ROOT / "composition.json").read_text(encoding="utf-8"))
    timing = json.loads((NARRATION / "narration_timing.json").read_text(encoding="utf-8"))
    ov = overrides.load()
    metas = {}
    for p in OUT.glob("slide_*/metadata.json"):
        m = json.loads(p.read_text(encoding="utf-8"))
        metas[m["slide"]] = m

    voice_changed = bool(overrides.voice(ov))
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
        refresh = voice_changed or overrides.narration(ov, n) is not None
        audio_path = ROOT / f"audio/slide_{n:02d}_voiceover.mp3"
        exp_slide_dur = media.slide_duration(audio_path, meta["duration"]) if refresh else meta["duration"]
        if s["duration"] != exp_slide_dur:
            errors.append(f"{key}: duration {s['duration']} != {exp_slide_dur}")
        if s["start"] != t.get("start") or s["end"] != t.get("end"):
            errors.append(f"{key}: window ({s['start']},{s['end']}) != ({t.get('start')},{t.get('end')})")
        if len(s["layers"]) != len(meta["layers"]):
            errors.append(f"{key}: layer count {len(s['layers'])} != {len(meta['layers'])}")
            continue
        for cl, ml in zip(s["layers"], meta["layers"]):
            o = overrides.layer(ov, n, ml["name"])  # expected = generated (+) override
            exp_id = overrides.stable_id(n, ml["name"])
            exp_z = o.get("z", ml["z_index"])
            exp_anim = o.get("animation", ml["animation"])
            exp_dur = o.get("duration", ml["duration"])
            exp_start = overrides.resolved_start(ml["start"], o, exp_slide_dur, exp_dur)
            if cl["id"] != exp_id:
                errors.append(f"{key}: id {cl['id']} != {exp_id}")
            if cl["id"] in seen_ids:
                errors.append(f"{key}: duplicate id {cl['id']}")
            seen_ids.add(cl["id"])
            if cl["type"] != ml["type"]:
                errors.append(f"{key}/{cl['id']}: type {cl['type']} != {ml['type']}")
            if cl["bbox"] != [ml["x"], ml["y"], ml["width"], ml["height"]]:
                errors.append(f"{key}/{cl['id']}: bbox {cl['bbox']} != {[ml['x'], ml['y'], ml['width'], ml['height']]}")
            if cl["z"] != exp_z:
                errors.append(f"{key}/{cl['id']}: z {cl['z']} != {exp_z}")
            if cl["enter"]["type"] != exp_anim:
                errors.append(f"{key}/{cl['id']}: enter {cl['enter']['type']} != {exp_anim}")
            if cl["start"] != exp_start or cl["duration"] != exp_dur:
                errors.append(f"{key}/{cl['id']}: timing ({cl['start']},{cl['duration']}) != ({exp_start},{exp_dur})")

    if errors:
        print(f"FAIL ({len(errors)} issue(s)):")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    print(f"OK: {len(comp['slides'])} slides, {len(seen_ids)} layers, all faithful")


if __name__ == "__main__":
    main()
