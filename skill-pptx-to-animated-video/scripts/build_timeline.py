"""Build the narration-first timeline and browser preview from per-slide
metadata.json (written by segment_elements.py) and narration_script.md.

Writes: narration/narration_timing.json, subtitles.srt, subtitles.vtt,
hyperframes/{project.json,index.html,styles.css,animation.js}.

Run AFTER segment_elements.py, and re-run whenever audio or layers change.
"""

import json
import math
import re
from pathlib import Path

ROOT = Path.cwd()
OUT = ROOT / "output"
AUDIO = ROOT / "audio"
NARRATION = ROOT / "narration"
HYPER = ROOT / "hyperframes"
WIDTH, HEIGHT, FPS = 1920, 1080, 30


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


SUB_CHUNK_MAX = 32  # CJK chars per subtitle cue (~1-2 lines at FontSize=11)


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


def main():
    scripts = parse_script()
    metadatas = load_metadatas()
    timing = {}
    srt, vtt = [], ["WEBVTT", ""]
    cursor = 0.0
    cue_idx = 0
    for meta in metadatas:
        n = meta["slide"]
        key = f"slide_{n:02d}"
        script = scripts.get(n, "")
        start = round(cursor, 2)
        end = round(cursor + meta["duration"], 2)
        timing[key] = {
            "voiceover_file": f"audio/slide_{n:02d}_voiceover.mp3",
            "start": start,
            "end": end,
            "script": script,
            "cues": [
                {
                    "time": round(start + l["start"], 2),
                    "layer": l["name"],
                    "action": l["animation"],
                    "spoken_content": l["narration_cue"],
                }
                for l in meta["layers"]
            ],
        }
        # Subtitle: split the slide's narration into short cues timed
        # proportionally to their char count across the speech window.
        speak_end = end - 0.35  # tail silence stays uncaptioned
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
    write_hyperframes(metadatas, timing)
    print(f"timeline: {sum(len(m['layers']) for m in metadatas)} layers, ends {max(t['end'] for t in timing.values())}s")


def write_hyperframes(metadatas, timing):
    HYPER.mkdir(exist_ok=True)
    (HYPER / "project.json").write_text(
        json.dumps(
            {"width": WIDTH, "height": HEIGHT, "fps": FPS, "slides": metadatas, "timing": timing},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    (HYPER / "index.html").write_text(
        """<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>HyperFrames Preview</title>
  <link rel="stylesheet" href="styles.css">
</head>
<body>
  <main id="stage" aria-label="video preview">
    <div id="slide"></div>
    <div id="caption"></div>
  </main>
  <div id="controls">
    <button id="play">Play</button>
    <button id="subtitles">Subtitles On</button>
  </div>
  <script src="animation.js"></script>
</body>
</html>
""",
        encoding="utf-8",
    )
    (HYPER / "styles.css").write_text(
        """html, body { margin: 0; min-height: 100%; background: #202124; font-family: Arial, "Noto Sans TC", sans-serif; }
#stage { position: relative; width: min(100vw, calc(100vh * 16 / 9)); aspect-ratio: 16 / 9; margin: 0 auto; overflow: hidden; background: white; }
#slide, .bg, .layer { position: absolute; inset: 0; }
.bg, .layer { width: 100%; height: 100%; object-fit: contain; user-select: none; pointer-events: none; }
.layer { inset: auto; opacity: 0; transform-origin: center; }
.show.fade-in-down { animation: fadeDown var(--dur) ease forwards; }
.show.fade-in-up { animation: fadeUp var(--dur) ease forwards; }
.show.fade-in { animation: fade var(--dur) ease forwards; }
.show.pop-in { animation: pop var(--dur) cubic-bezier(.2,.85,.25,1.2) forwards; }
.show.zoom-in { animation: zoom var(--dur) ease forwards; }
.show.wipe-in { animation: wipe var(--dur) ease forwards; }
.show.draw-in { animation: draw var(--dur) ease forwards; }
#caption { position: absolute; left: 8%; right: 8%; bottom: 4%; min-height: 48px; padding: 10px 16px; display: none; align-items: center; justify-content: center; text-align: center; color: #fff; background: rgba(0,0,0,.58); font-size: 28px; line-height: 1.35; }
#caption.on { display: flex; }
#controls { position: fixed; left: 16px; bottom: 16px; display: flex; gap: 8px; }
button { border: 0; border-radius: 6px; padding: 10px 14px; background: #f6f7f8; cursor: pointer; }
@keyframes fadeDown { from { opacity: 0; transform: translateY(-22px); } to { opacity: 1; transform: none; } }
@keyframes fadeUp { from { opacity: 0; transform: translateY(24px); } to { opacity: 1; transform: none; } }
@keyframes fade { from { opacity: 0; } to { opacity: 1; } }
@keyframes pop { from { opacity: 0; transform: scale(.86); } to { opacity: 1; transform: scale(1); } }
@keyframes zoom { from { opacity: 0; transform: scale(.94); } to { opacity: 1; transform: scale(1); } }
@keyframes wipe { from { opacity: 0; clip-path: inset(0 100% 0 0); } to { opacity: 1; clip-path: inset(0); } }
@keyframes draw { from { opacity: 0; transform: translateY(10px) scale(.98); } to { opacity: 1; transform: none; } }
""",
        encoding="utf-8",
    )
    (HYPER / "animation.js").write_text(
        """const slideRoot = document.getElementById('slide');
const caption = document.getElementById('caption');
const playButton = document.getElementById('play');
const subtitleButton = document.getElementById('subtitles');
let subtitlesOn = true;

subtitleButton.addEventListener('click', () => {
  subtitlesOn = !subtitlesOn;
  subtitleButton.textContent = subtitlesOn ? 'Subtitles On' : 'Subtitles Off';
  caption.classList.toggle('on', subtitlesOn);
});

const sleep = ms => new Promise(r => setTimeout(r, ms));
const pad = n => String(n).padStart(2, '0');

async function loadProject() {
  const res = await fetch('project.json');
  return res.json();
}

function showSlide(slide, timing) {
  slideRoot.innerHTML = '';
  const base = document.createElement('img');
  base.className = 'bg';
  base.src = `../output/slide_${pad(slide.slide)}/background.png`;
  slideRoot.appendChild(base);
  for (const layer of slide.layers) {
    const img = document.createElement('img');
    img.className = `layer ${layer.animation}`;
    img.src = `../output/slide_${pad(slide.slide)}/${layer.name}`;
    img.style.left = `${layer.x / slide.width * 100}%`;
    img.style.top = `${layer.y / slide.height * 100}%`;
    img.style.width = `${layer.width / slide.width * 100}%`;
    img.style.height = `${layer.height / slide.height * 100}%`;
    img.style.zIndex = layer.z_index;
    img.style.setProperty('--dur', `${layer.duration}s`);
    slideRoot.appendChild(img);
    window.setTimeout(() => img.classList.add('show'), layer.start * 1000);
  }
  caption.textContent = timing.script;
  caption.classList.toggle('on', subtitlesOn);
}

async function play() {
  playButton.disabled = true;
  const project = await loadProject();
  for (const slide of project.slides) {
    const timing = project.timing[`slide_${pad(slide.slide)}`];
    showSlide(slide, timing);
    const audio = new Audio(`../audio/slide_${pad(slide.slide)}_voiceover.mp3`);
    try { await audio.play(); } catch (e) {}
    await sleep(slide.duration * 1000 + 500);
  }
  playButton.disabled = false;
}

playButton.addEventListener('click', play);
loadProject().then(p => showSlide(p.slides[0], p.timing[`slide_${pad(p.slides[0].slide)}`]));
""",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
