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
import overrides

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


def slides_to_speak():
    """Effective narration per slide: override text if edited, else the script."""
    sections = parse_script()
    ov = overrides.load()
    out = {}
    for n, body in sections.items():
        text = overrides.narration(ov, n)
        out[n] = text if text is not None else body
    return out


async def main():
    import edge_tts

    ov = overrides.load()
    vo = overrides.voice(ov)
    # Precedence: CLI arg > voice override > config default.
    voice = sys.argv[1] if len(sys.argv) > 1 else vo.get("voice", config.VOICE["voice"])
    rate = sys.argv[2] if len(sys.argv) > 2 else vo.get("rate", config.VOICE["rate"])
    AUDIO.mkdir(exist_ok=True)
    sections = slides_to_speak()
    if not sections:
        sys.exit(f"no '## Slide NN' sections found in {SCRIPT}")
    for n, body in sorted(sections.items()):
        out = AUDIO / f"slide_{n:02d}_voiceover.mp3"
        await edge_tts.Communicate(text=body, voice=voice, rate=rate).save(str(out))
        print(f"slide_{n:02d}: {len(body)} chars -> {out.name}")


if __name__ == "__main__":
    asyncio.run(main())
