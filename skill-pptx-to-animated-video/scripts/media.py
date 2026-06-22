"""Audio probing shared by the timeline + composition steps.

Lets those steps recompute a slide's duration from the *current* voiceover
mp3, so editing narration/voice no longer requires re-running segmentation.
Falls back gracefully (returns the caller's baseline) when ffprobe or the
audio file is unavailable.
"""

import shutil
import subprocess
from pathlib import Path

SLIDE_TAIL = 0.55  # silence pad after speech; matches segment_elements


def find_ffprobe():
    # Steps run with cwd=task dir, but node_modules sits at the project root one
    # level up, so check the task dir AND its ancestors -- otherwise the probe
    # is never found and every slide silently falls back to the placeholder
    # duration (6.55s) instead of its real narration length.
    rel = Path("node_modules") / "ffprobe-static" / "bin" / "win32" / "x64" / "ffprobe.exe"
    here = Path.cwd()
    for base in (here, *here.parents):
        local = base / rel
        if local.exists():
            return str(local)
    return shutil.which("ffprobe")


def audio_duration(path):
    probe = find_ffprobe()
    if not probe or not Path(path).exists():
        return None
    result = subprocess.run(
        [probe, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=False,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def slide_duration(audio_path, fallback):
    """Speech length + tail pad, or `fallback` if the audio can't be probed."""
    d = audio_duration(audio_path)
    return round(d + SLIDE_TAIL, 2) if d else fallback
