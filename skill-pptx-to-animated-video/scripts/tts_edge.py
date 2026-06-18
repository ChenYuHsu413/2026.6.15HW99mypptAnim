"""Generate per-slide voiceover MP3s from narration/narration_script.md.

The script file format (one section per slide):

    ## Slide 01 - <title>

    <narration text, one or more lines>

Usage: python tts_edge.py [voice] [rate]
Defaults: zh-TW-HsiaoChenNeural, -8%  (natural female zh-TW, teaching pace)
"""

import asyncio
import re
import sys
from pathlib import Path

import config

ROOT = Path.cwd()
AUDIO = ROOT / "audio"
SCRIPT = ROOT / "narration" / "narration_script.md"


def parse_script():
    text = SCRIPT.read_text(encoding="utf-8")
    sections = {}
    for m in re.finditer(
        r"^## Slide (\d+)[^\n]*\n+(.*?)(?=^## Slide |\Z)", text, re.M | re.S
    ):
        body = " ".join(line.strip() for line in m.group(2).splitlines() if line.strip())
        if body:
            sections[int(m.group(1))] = body
    return sections


async def main():
    import edge_tts

    voice = sys.argv[1] if len(sys.argv) > 1 else config.VOICE["voice"]
    rate = sys.argv[2] if len(sys.argv) > 2 else config.VOICE["rate"]
    AUDIO.mkdir(exist_ok=True)
    sections = parse_script()
    if not sections:
        sys.exit(f"no '## Slide NN' sections found in {SCRIPT}")
    for n, body in sorted(sections.items()):
        out = AUDIO / f"slide_{n:02d}_voiceover.mp3"
        await edge_tts.Communicate(text=body, voice=voice, rate=rate).save(str(out))
        print(f"slide_{n:02d}: {len(body)} chars -> {out.name}")


if __name__ == "__main__":
    asyncio.run(main())
