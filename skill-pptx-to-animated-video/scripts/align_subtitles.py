"""Forced-alignment of subtitle cues to the actual MP3 audio.

Replaces the char-proportional timing in narration/subtitles.{srt,vtt}
with timings snapped to word boundaries detected by whisper-timestamped.

Run AFTER build_timeline.py. Pipeline-optional: if whisper-timestamped is
not installed (or the model can't be loaded), the script prints a friendly
message and exits 0 — the existing char-proportional SRT stays in place.

Install:  pip install whisper-timestamped     (pulls torch + a tiny model)
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path.cwd()
NARRATION = ROOT / "narration"
AUDIO = ROOT / "audio"

MODEL_NAME = "base"  # ~140 MB; "tiny" (75 MB) trades accuracy for speed.


def _ensure_ffmpeg_on_path():
    """openai-whisper subprocesses `ffmpeg` to decode audio. If ffmpeg isn't
    on PATH, prepend the bundled node_modules/ffmpeg-static (repo root or
    cwd) so the call finds it.
    """
    if os.environ.get("PATH") and any(
        (Path(p) / "ffmpeg.exe").exists() or (Path(p) / "ffmpeg").exists()
        for p in os.environ["PATH"].split(os.pathsep) if p
    ):
        return
    for candidate in (
        ROOT / "node_modules" / "ffmpeg-static",
        ROOT.parent / "node_modules" / "ffmpeg-static",
    ):
        if (candidate / "ffmpeg.exe").exists() or (candidate / "ffmpeg").exists():
            os.environ["PATH"] = str(candidate) + os.pathsep + os.environ.get("PATH", "")
            return


def _pt(t):
    """SRT timestamp ('HH:MM:SS,mmm') → seconds (float)."""
    p = re.split(r"[:,]", t)
    return int(p[0]) * 3600 + int(p[1]) * 60 + int(p[2]) + int(p[3]) / 1000


def _srt_time(t):
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t - h * 3600 - m * 60
    return f"{h:02d}:{m:02d}:{int(s):02d},{int(round((s - int(s)) * 1000)):03d}"


def _parse_srt(text):
    """Return cues = [{idx, start, end, text}]."""
    cues = []
    for block in re.split(r"\n\n+", text.strip()):
        lines = block.split("\n")
        if len(lines) < 3:
            continue
        m = re.match(r"([\d:,]+)\s*-->\s*([\d:,]+)", lines[1])
        if not m:
            continue
        cues.append({
            "idx": int(lines[0]),
            "start": _pt(m.group(1)),
            "end": _pt(m.group(2)),
            "text": "\n".join(lines[2:]),
        })
    return cues


def _slide_for_cue(cue_start, timing):
    """Find the slide key whose window contains this cue's start (-50ms slack)."""
    for key, t in timing.items():
        s, e = t.get("start"), t.get("end")
        if s is None or e is None:
            continue
        if s - 0.05 <= cue_start < e + 0.05:
            return key
    return None


def _align_within_slide(cues, words, slide_start):
    """Re-time slide-local cues using whisper word timestamps (MP3-local).

    Builds a per-character map cue_id, then walks `words` greedy-matching
    each word's text against the concatenated cue text. Each word assigns
    its start/end to whichever cue(s) its characters span. Cues without a
    matched word keep their original timing.

    Whisper for zh-TW typically emits one "word" per Chinese character;
    homophones / punctuation differences break the strict match — when too
    many words fail to match, we fall back to a proportional distribution
    over the first-word-start..last-word-end range.
    """
    if not cues or not words:
        return cues
    char_to_cue = []
    full = ""
    for ci, cue in enumerate(cues):
        for c in cue["text"]:
            if c.strip():
                full += c
                char_to_cue.append(ci)
    pos = 0
    matched = 0
    cue_words = [[] for _ in cues]
    for w in words:
        wt = "".join(c for c in (w.get("text") or "") if c.strip())
        if not wt:
            continue
        if pos + len(wt) > len(full):
            break
        if full[pos:pos + len(wt)] != wt:
            # Strict mismatch — skip this word but keep walking.
            continue
        for k in range(len(wt)):
            cue_words[char_to_cue[pos + k]].append(w)
        pos += len(wt)
        matched += 1

    coverage = matched / max(1, len(words))
    if coverage < 0.4:
        # Strict match broke down; fall back to time-proportional over the
        # actual speech range so we at least track silences/pauses.
        speech_lo = min(w["start"] for w in words)
        speech_hi = max(w["end"] for w in words)
        total = sum(len(c["text"].strip()) for c in cues) or 1
        out = []
        cursor = speech_lo
        for c in cues:
            share = len(c["text"].strip()) / total
            new_end = cursor + (speech_hi - speech_lo) * share
            out.append({**c, "start": cursor + slide_start, "end": new_end + slide_start})
            cursor = new_end
        return out

    out = []
    for ci, cue in enumerate(cues):
        ws = cue_words[ci]
        if not ws:
            out.append(cue)
            continue
        out.append({
            **cue,
            "start": min(w["start"] for w in ws) + slide_start,
            "end":   max(w["end"]   for w in ws) + slide_start,
        })
    return out


def main():
    _ensure_ffmpeg_on_path()
    try:
        import whisper_timestamped  # noqa: WPS433 — optional dep
    except ImportError:
        print("whisper-timestamped not installed — keeping the char-proportional SRT. "
              "Install with: pip install whisper-timestamped")
        return

    srt_path = NARRATION / "subtitles.srt"
    timing_path = NARRATION / "narration_timing.json"
    if not srt_path.exists() or not timing_path.exists():
        sys.exit("subtitles.srt or narration_timing.json missing — run build_timeline.py first")
    timing = json.loads(timing_path.read_text(encoding="utf-8"))
    cues = _parse_srt(srt_path.read_text(encoding="utf-8"))
    if not cues:
        return

    print(f"loading whisper model '{MODEL_NAME}' (first run downloads ~140 MB)…")
    model = whisper_timestamped.load_model(MODEL_NAME)

    # Group cues by slide.
    by_slide = {}
    for cue in cues:
        key = _slide_for_cue(cue["start"], timing)
        if key:
            by_slide.setdefault(key, []).append(cue)

    new_cues = []
    aligned = 0
    for key, slide_cues in by_slide.items():
        slide_start = timing[key]["start"]
        # Slide-local copies for the alignment routine (subtract slide_start).
        local_cues = [{**c, "start": c["start"] - slide_start, "end": c["end"] - slide_start}
                      for c in slide_cues]
        mp3 = AUDIO / f"{key}_voiceover.mp3"
        if not mp3.exists():
            new_cues.extend(slide_cues)
            continue
        try:
            result = whisper_timestamped.transcribe(model, str(mp3), language="zh")
        except Exception as exc:  # noqa: BLE001
            print(f"  {key}: whisper failed ({exc}) — keeping baked timing")
            new_cues.extend(slide_cues)
            continue
        words = [w for seg in result.get("segments", []) for w in seg.get("words", [])]
        if not words:
            new_cues.extend(slide_cues)
            continue
        aligned += 1
        new_cues.extend(_align_within_slide(local_cues, words, slide_start))

    new_cues.sort(key=lambda c: c["start"])
    srt_lines = []
    vtt_lines = ["WEBVTT", ""]
    for i, c in enumerate(new_cues, 1):
        srt_lines += [str(i), f"{_srt_time(c['start'])} --> {_srt_time(c['end'])}", c["text"], ""]
        vtt_lines += [f"{_srt_time(c['start']).replace(',', '.')} --> "
                      f"{_srt_time(c['end']).replace(',', '.')}", c["text"], ""]
    srt_path.write_text("\n".join(srt_lines), encoding="utf-8")
    (NARRATION / "subtitles.vtt").write_text("\n".join(vtt_lines), encoding="utf-8")
    print(f"forced-alignment done: {aligned}/{len(by_slide)} slides aligned, "
          f"{len(new_cues)} cues rewritten in subtitles.srt + .vtt")


if __name__ == "__main__":
    main()
