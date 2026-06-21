"""Build the narration-first timeline and browser preview from per-slide
metadata.json (written by segment_elements.py) and narration_script.md.

Writes: narration/narration_timing.json, subtitles.srt, subtitles.vtt,
hyperframes/{index.html,styles.css,animation.js}.

The hyperframes draft preview reads composition.json directly (built by
build_composition.py), so the three static template files are not generated
here — they're copied verbatim from the skill's canonical
`skill-pptx-to-animated-video/hyperframes/` directory. Edit those templates
once and every task picks up the change on the next build.

Run AFTER segment_elements.py, and re-run whenever audio or layers change.
"""

import json
import math
import re
import shutil
from pathlib import Path

import config
import media
import overrides

ROOT = Path.cwd()
OUT = ROOT / "output"
AUDIO = ROOT / "audio"
NARRATION = ROOT / "narration"
HYPER = ROOT / "hyperframes"
HYPER_TEMPLATES = Path(__file__).resolve().parent.parent / "hyperframes"
WIDTH = config.PROJECT["canvas"]["width"]
HEIGHT = config.PROJECT["canvas"]["height"]
FPS = config.PROJECT["canvas"]["fps"]


def parse_script():
    text = (NARRATION / "narration_script.md").read_text(encoding="utf-8")
    sections = {}
    for m in re.finditer(
        r"^## Slide (\d+)[^\n]*\n+(.*?)(?=^## Slide |\Z)", text, re.M | re.S
    ):
        body = " ".join(line.strip() for line in m.group(2).splitlines() if line.strip())
        sections[int(m.group(1))] = body
    return sections


def load_metadatas():
    metas = []
    for p in sorted(OUT.glob("slide_*/metadata.json")):
        metas.append(json.loads(p.read_text(encoding="utf-8")))
    return sorted(metas, key=lambda m: m["slide"])


def srt_time(seconds):
    ms = int(round((seconds - math.floor(seconds)) * 1000))
    whole = int(seconds)
    return f"{whole // 3600:02d}:{whole % 3600 // 60:02d}:{whole % 60:02d},{ms:03d}"


SUB_CHUNK_MAX = config.CAPTION["chunk_max_chars"]  # CJK chars per subtitle cue (~1-2 lines at FontSize=11)


def chunk_narration(text, max_chars=SUB_CHUNK_MAX):
    """Split a slide's narration into short subtitle cues.

    Prefers sentence boundaries (。！？), then clause boundaries (，：；、)
    when a sentence is too long. Each cue stays under max_chars CJK
    characters so the burned subtitle fits 1-2 lines at the bottom and
    doesn't climb into the slide content.
    """
    text = text.strip()
    if not text:
        return []
    sentences = [s.strip() for s in re.split(r"(?<=[。！？])\s*", text) if s.strip()]
    chunks = []
    for s in sentences:
        if len(s) <= max_chars:
            chunks.append(s)
            continue
        parts = [p.strip() for p in re.split(r"(?<=[，：；、])\s*", s) if p.strip()]
        current = ""
        for p in parts:
            if not current:
                current = p
            elif len(current) + len(p) <= max_chars:
                current += p
            else:
                chunks.append(current)
                current = p
        if current:
            chunks.append(current)
    return chunks


def recompute_layer_starts(layers, duration):
    """Return {layer_name: start} computed from the *current* slide duration.

    Mirrors the formula segment_elements.py originally baked into metadata:
        cue_gap   = max(0.55, (duration - 2.2) / N_entries)
        baseline  = round(min(0.45 + i * cue_gap, duration - 1.0), 2)
        annotation -> round(min(parent_baseline + 0.85, duration - 0.8), 2)

    Works without re-segmenting: each layer carries its entry_index, and
    annotation layers carry parent_index. Layers from older deck builds (no
    entry_index) fall back to their baked metadata `start` — preserving
    byte-identical output for tasks frozen at an earlier pipeline version.
    """
    if not layers:
        return {}
    if not all("entry_index" in l for l in layers):
        return {l["name"]: l["start"] for l in layers}
    n = max((l["entry_index"] for l in layers), default=0) + 1
    cue_gap = max(0.55, (duration - 2.2) / max(1, n))
    baselines = {}
    for l in layers:
        baselines[l["entry_index"]] = round(min(0.45 + l["entry_index"] * cue_gap, duration - 1.0), 2)
    starts = {}
    for l in layers:
        if l["type"] == "annotation" and l.get("parent_index") in baselines:
            starts[l["name"]] = round(min(baselines[l["parent_index"]] + 0.85, duration - 0.8), 2)
        else:
            starts[l["name"]] = baselines[l["entry_index"]]
    return starts


def main():
    scripts = parse_script()
    metadatas = load_metadatas()
    ov = overrides.load()
    voice_changed = bool(overrides.voice(ov))
    timing = {}
    srt, vtt = [], ["WEBVTT", ""]
    cursor = 0.0
    cue_idx = 0
    for meta in metadatas:
        n = meta["slide"]
        key = f"slide_{n:02d}"
        ov_narr = overrides.narration(ov, n)
        script = ov_narr if ov_narr is not None else scripts.get(n, "")
        # Refresh duration from the current audio only for edited slides;
        # unedited slides keep their baked duration (so output is identical).
        refresh = voice_changed or ov_narr is not None
        audio_path = ROOT / f"audio/slide_{n:02d}_voiceover.mp3"
        duration = media.slide_duration(audio_path, meta["duration"]) if refresh else meta["duration"]
        start = round(cursor, 2)
        end = round(cursor + duration, 2)
        layer_starts = recompute_layer_starts(meta["layers"], duration)
        timing[key] = {
            "voiceover_file": f"audio/slide_{n:02d}_voiceover.mp3",
            "start": start,
            "end": end,
            "script": script,
            "layer_starts": layer_starts,
            "cues": [
                {
                    "time": round(start + layer_starts[l["name"]], 2),
                    "layer": l["name"],
                    "action": l["animation"],
                    "spoken_content": l["narration_cue"],
                }
                for l in meta["layers"]
            ],
        }
        # Subtitle: split the slide's narration into short cues timed
        # proportionally to their char count across the speech window.
        speak_end = end - config.CAPTION["tail_silence"]  # tail silence stays uncaptioned
        chunks = chunk_narration(script)
        if chunks:
            total_chars = sum(len(c) for c in chunks)
            span = max(0.5, speak_end - start)
            t = start
            for i, chunk in enumerate(chunks):
                ratio = len(chunk) / total_chars if total_chars else 1.0
                t_end = speak_end if i == len(chunks) - 1 else min(t + span * ratio, speak_end)
                cue_idx += 1
                srt += [
                    str(cue_idx),
                    f"{srt_time(t)} --> {srt_time(t_end)}",
                    chunk,
                    "",
                ]
                vtt += [
                    f"{srt_time(t).replace(',', '.')} --> {srt_time(t_end).replace(',', '.')}",
                    chunk,
                    "",
                ]
                t = t_end
        cursor = end + 0.5
    NARRATION.mkdir(exist_ok=True)
    (NARRATION / "narration_timing.json").write_text(
        json.dumps(timing, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (NARRATION / "subtitles.srt").write_text("\n".join(srt), encoding="utf-8")
    (NARRATION / "subtitles.vtt").write_text("\n".join(vtt), encoding="utf-8")
    copy_hyperframes_templates()
    print(f"timeline: {sum(len(m['layers']) for m in metadatas)} layers, ends {max(t['end'] for t in timing.values())}s")


def copy_hyperframes_templates():
    """Drop the three static draft-preview files into the task's hyperframes/.

    The templates live in the skill's `hyperframes/` directory and are the
    single source of truth; every task gets a verbatim byte copy on each
    build, so editing the skill copy fixes every deck on the next rebuild.
    No `project.json` is written — the preview reads composition.json directly.
    """
    HYPER.mkdir(exist_ok=True)
    for name in ("index.html", "styles.css", "animation.js"):
        shutil.copyfile(HYPER_TEMPLATES / name, HYPER / name)


if __name__ == "__main__":
    main()
