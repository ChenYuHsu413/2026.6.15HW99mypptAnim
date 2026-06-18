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
    local = Path.cwd() / "node_modules" / "ffprobe-static" / "bin" / "win32" / "x64" / "ffprobe.exe"
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
