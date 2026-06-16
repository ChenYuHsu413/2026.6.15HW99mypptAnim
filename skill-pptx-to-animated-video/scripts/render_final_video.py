"""Render the HyperFrames layer animation to MP4 with narration audio.

Replays the same per-layer animations defined in hyperframes/styles.css
(fade/pop/zoom/wipe/draw variants) by compositing frames in numpy and piping
them to ffmpeg. Finished layers are baked into a settled canvas and fully
static frames reuse cached bytes, so rendering stays fast.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ROOT = Path.cwd()
OUT = ROOT / "output"
AUDIO = ROOT / "audio"
NARRATION = ROOT / "narration"
FINAL = ROOT / "final"
WIDTH, HEIGHT, FPS = 1920, 1080, 30
TRANSITION = 0.5
SAMPLE_RATE = 48000


def find_ffmpeg():
    candidates = [
        os.environ.get("FFMPEG_PATH"),
        str(ROOT / "node_modules" / "ffmpeg-static" / "ffmpeg"),
        str(ROOT / "node_modules" / "ffmpeg-static" / "ffmpeg.exe"),
        str(ROOT.parent / "node_modules" / "ffmpeg-static" / "ffmpeg"),
        str(ROOT.parent / "node_modules" / "ffmpeg-static" / "ffmpeg.exe"),
        shutil.which("ffmpeg"),
    ]
    for c in candidates:
        if c and Path(c).exists():
            return Path(c)
    raise SystemExit(
        "ffmpeg not found: set FFMPEG_PATH, install ffmpeg, or `npm i ffmpeg-static`"
    )


FFMPEG = find_ffmpeg()


def slide_numbers():
    nums = []
    for p in sorted(OUT.glob("slide_*/metadata.json")):
        m = re.match(r"slide_(\d+)", p.parent.name)
        if m:
            nums.append(int(m.group(1)))
    return nums


def ease(p):
    return p * p * (3 - 2 * p)


def load_timing():
    return json.loads((NARRATION / "narration_timing.json").read_text(encoding="utf-8"))


def load_slides():
    slides = []
    for n in slide_numbers():
        d = OUT / f"slide_{n:02d}"
        meta = json.loads((d / "metadata.json").read_text(encoding="utf-8"))
        bg = cv2.cvtColor(np.array(Image.open(d / "background.png").convert("RGB")), cv2.COLOR_RGB2BGR)
        layers = []
        for layer in meta["layers"]:
            if layer.get("type") == "key_point_card":
                continue
            rgba = np.array(Image.open(d / layer["name"]).convert("RGBA"))
            bgr = cv2.cvtColor(rgba[:, :, :3], cv2.COLOR_RGB2BGR)
            alpha = rgba[:, :, 3].astype(np.float32) / 255.0
            layers.append({"meta": layer, "bgr": bgr, "alpha": alpha})
        slides.append({"meta": meta, "bg": bg, "layers": layers})
    return slides


def paste(frame, bgr, alpha, x, y, opacity=1.0):
    h, w = bgr.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(WIDTH, x + w), min(HEIGHT, y + h)
    if x1 <= x0 or y1 <= y0:
        return
    sx, sy = x0 - x, y0 - y
    piece = bgr[sy : sy + (y1 - y0), sx : sx + (x1 - x0)].astype(np.float32)
    a = (alpha[sy : sy + (y1 - y0), sx : sx + (x1 - x0)] * opacity)[:, :, None]
    roi = frame[y0:y1, x0:x1].astype(np.float32)
    frame[y0:y1, x0:x1] = (roi * (1 - a) + piece * a).astype(np.uint8)


def draw_layer(frame, layer, p):
    """Composite one layer at animation progress p in [0, 1]."""
    meta = layer["meta"]
    p = ease(min(1.0, max(0.0, p)))
    kind = meta["animation"]
    x, y = meta["x"], meta["y"]
    bgr, alpha = layer["bgr"], layer["alpha"]
    opacity = p
    if kind == "fade-in-down":
        y -= int(round(22 * (1 - p)))
    elif kind == "fade-in-up":
        y += int(round(24 * (1 - p)))
    elif kind == "draw-in":
        y += int(round(10 * (1 - p)))
    elif kind in ("pop-in", "zoom-in"):
        base = 0.86 if kind == "pop-in" else 0.94
        scale = base + (1 - base) * p
        h0, w0 = bgr.shape[:2]
        w1, h1 = max(1, int(w0 * scale)), max(1, int(h0 * scale))
        bgr = cv2.resize(bgr, (w1, h1), interpolation=cv2.INTER_AREA)
        alpha = cv2.resize(layer["alpha"], (w1, h1), interpolation=cv2.INTER_AREA)
        x += (w0 - w1) // 2
        y += (h0 - h1) // 2
    elif kind == "wipe-in":
        w0 = bgr.shape[1]
        reveal = max(1, int(w0 * p))
        bgr = bgr[:, :reveal]
        alpha = alpha[:, :reveal]
        opacity = min(1.0, p * 2)
    paste(frame, bgr, alpha, x, y, opacity)


def slide_frame(slide, t, cache):
    """Frame of one slide at local time t. cache holds the settled canvas."""
    layers = slide["layers"]
    settled_n = cache.get("settled_n", 0)
    # Bake layers (in z order) whose animation has fully finished.
    while settled_n < len(layers):
        meta = layers[settled_n]["meta"]
        if t >= meta["start"] + meta["duration"]:
            draw_layer(cache["settled"], layers[settled_n], 1.0)
            settled_n += 1
        else:
            break
    cache["settled_n"] = settled_n
    active = [
        l for l in layers[settled_n:]
        if l["meta"]["start"] <= t
    ]
    if not active:
        return cache["settled"], True
    frame = cache["settled"].copy()
    for layer in active:
        meta = layer["meta"]
        p = (t - meta["start"]) / max(0.01, meta["duration"])
        draw_layer(frame, layer, p)
    return frame, False


def build_audio(timing, total_seconds):
    total = int(total_seconds * SAMPLE_RATE)
    track = np.zeros((total, 2), dtype=np.float32)
    for key, info in timing.items():
        mp3 = ROOT / info["voiceover_file"]
        raw = subprocess.run(
            [str(FFMPEG), "-v", "error", "-i", str(mp3), "-f", "s16le",
             "-ar", str(SAMPLE_RATE), "-ac", "2", "pipe:1"],
            capture_output=True, check=True,
        ).stdout
        samples = np.frombuffer(raw, dtype=np.int16).reshape(-1, 2).astype(np.float32) / 32768.0
        start = int(info["start"] * SAMPLE_RATE)
        end = min(total, start + len(samples))
        track[start:end] += samples[: end - start]
    # Normalize to a consistent loudness ceiling.
    peak = np.abs(track).max()
    if peak > 0:
        track *= 0.85 / peak
    pcm = (track * 32767).astype(np.int16)
    wav_path = FINAL / "narration_full.wav"
    write_wav(wav_path, pcm)
    return wav_path


def write_wav(path, pcm):
    import struct
    data = pcm.tobytes()
    with open(path, "wb") as f:
        f.write(b"RIFF")
        f.write(struct.pack("<I", 36 + len(data)))
        f.write(b"WAVEfmt ")
        f.write(struct.pack("<IHHIIHH", 16, 1, 2, SAMPLE_RATE, SAMPLE_RATE * 4, 4, 16))
        f.write(b"data")
        f.write(struct.pack("<I", len(data)))
        f.write(data)


def main():
    FINAL.mkdir(exist_ok=True)
    timing = load_timing()
    slides = load_slides()
    keys = [f"slide_{n:02d}" for n in slide_numbers()]
    total_seconds = timing[keys[-1]]["end"] + TRANSITION
    total_frames = int(round(total_seconds * FPS))
    print(f"total video: {total_seconds:.2f}s, {total_frames} frames")

    audio_path = build_audio(timing, total_seconds)
    print("audio track written")

    out_path = FINAL / "final_video_with_voiceover.mp4"
    proc = subprocess.Popen(
        [str(FFMPEG), "-y", "-v", "error",
         "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{WIDTH}x{HEIGHT}",
         "-r", str(FPS), "-i", "pipe:0",
         "-i", str(audio_path),
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
         "-movflags", "+faststart", "-shortest", str(out_path)],
        stdin=subprocess.PIPE,
    )

    cached_bytes = None
    cached_static = False
    for n, key in enumerate(keys):
        info = timing[key]
        slide = slides[n]
        cache = {"settled": slide["bg"].copy(), "settled_n": 0}
        start_f = int(round(info["start"] * FPS))
        end_f = int(round(info["end"] * FPS))
        trans_f = int(round((info["end"] + TRANSITION) * FPS))
        last_frame = None
        for f in range(start_f, end_f):
            t = f / FPS - info["start"]
            frame, static = slide_frame(slide, t, cache)
            if static and cached_static and cached_bytes is not None:
                proc.stdin.write(cached_bytes)
                continue
            data = frame.tobytes()
            proc.stdin.write(data)
            cached_bytes, cached_static = data, static
            last_frame = frame
        if last_frame is None:
            last_frame = cache["settled"]
        # Transition: crossfade into the next slide's background.
        if n + 1 < len(keys):
            nxt = slides[n + 1]["bg"]
        else:
            nxt = last_frame
        steps = trans_f - end_f
        for i in range(steps):
            q = ease((i + 1) / max(1, steps))
            blended = cv2.addWeighted(last_frame, 1 - q, nxt, q, 0)
            proc.stdin.write(blended.tobytes())
        cached_static = False
        print(f"{key} rendered")
    proc.stdin.close()
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg encode failed")
    print(f"wrote {out_path}")

    # Burned-subtitles version.
    subbed = FINAL / "final_video_with_voiceover_and_subtitles.mp4"
    result = subprocess.run(
        [str(FFMPEG), "-y", "-v", "error", "-i", str(out_path),
         # Letterbox: shrink slide to top, dark band fills the bottom 120px
         # for subtitles -- subtitles never overlap slide content this way.
         "-vf", "scale=1920:960,pad=1920:1080:0:0:color=0x101010,subtitles=narration/subtitles.srt:force_style='FontName=Microsoft JhengHei,FontSize=11,PrimaryColour=&H00FFFFFF,BorderStyle=3,Outline=8,Shadow=0,BackColour=&H66000000,MarginL=30,MarginR=30,MarginV=10'",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
         "-c:a", "copy", str(subbed)],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    if result.returncode != 0:
        print("subtitle burn failed:", result.stderr[-2000:])
        sys.exit(1)
    print(f"wrote {subbed}")


if __name__ == "__main__":
    main()
