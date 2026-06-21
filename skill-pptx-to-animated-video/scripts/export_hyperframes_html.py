"""Composition → HeyGen HyperFrames project (HTML + GSAP + assets).

Reads composition.json from the current task dir and writes a self-contained
HF project to `<task>/hyperframes-export/`. The output passes
`npm run check` (HF lint + validate + inspect) and can be rendered with
`npm run render`.

Layout:
    hyperframes-export/
        package.json, hyperframes.json, meta.json
        index.html                    # root composition, embeds each slide
        compositions/slide_NN.html    # one sub-composition per slide
        assets/                       # copied PNG / MP3 (renamed flat)

Usage:
    python export_hyperframes_html.py [output_dir]

Default output_dir = ./hyperframes-export
"""

from __future__ import annotations

import html
import json
import shutil
import sys
from pathlib import Path

ROOT = Path.cwd()
COMP_PATH = ROOT / "composition.json"
DEFAULT_OUT = ROOT / "hyperframes-export"


def _anim_for(enter_type: str, start: float, duration: float) -> str:
    """Map composition enter.type → a GSAP tl.from(...) line."""
    e = {"opacity": 0}
    dur = max(0.3, duration)
    t = enter_type or "fade-in"
    if t == "fade-in":
        pass
    elif t == "fade-in-up":
        e["y"] = 50
    elif t == "fade-in-down":
        e["y"] = -50
    elif t == "zoom-in":
        e["scale"] = 0.6
    elif t == "pop-in":
        e["scale"] = 0.85
        dur = 0.5
    elif t in ("wipe-in", "draw-in"):
        e["x"] = -40
    e["duration"] = round(dur, 3)
    parts = ", ".join(f"{k}: {v}" for k, v in e.items())
    return f"    tl.from(\"#{{id}}\", {{ {parts} }}, {round(start, 3)});"


def _flatten_asset(src_rel: str) -> str:
    """Turn 'output/slide_01/slide_01_title_01.png' into 'slide_01_title_01.png'.
    Filenames already prefixed with their slide stay as-is; bare 'background.png'
    gets its slide-dir prefix attached so it's globally unique."""
    p = Path(src_rel)
    parent = p.parent.name  # e.g. 'slide_01'
    if parent and not p.name.startswith(parent + "_") and parent.startswith("slide_"):
        return f"{parent}_{p.name}"
    return p.name


def _copy_asset(task_root: Path, assets_dir: Path, src_rel: str) -> str | None:
    """Copy task_root/src_rel into assets_dir, return the flat filename or None."""
    if not src_rel:
        return None
    src = task_root / src_rel
    if not src.exists():
        return None
    flat = _flatten_asset(src_rel)
    dst = assets_dir / flat
    if not dst.exists() or src.stat().st_mtime > dst.stat().st_mtime:
        shutil.copyfile(src, dst)
    return flat


def _slide_html(slide: dict, comp: dict, task_root: Path, assets_dir: Path) -> str:
    canvas = comp["canvas"]
    W, H = canvas["width"], canvas["height"]
    idx = slide["index"]
    sid = f"slide_{idx:02d}"
    slide_dur = float(slide["duration"])

    # audio handled in root index.html (HF lint flattens sub-comp audio onto
    # the global track; placing it in root with proper cursor offsets avoids
    # false-positive overlap warnings)
    bg_name = _copy_asset(task_root, assets_dir, slide.get("background") or "")

    body_parts: list[str] = []
    track = 0

    if bg_name:
        body_parts.append(
            f'      <img id="{sid}-bg" class="clip" data-start="0" data-duration="{slide_dur}" '
            f'data-track-index="{track}" '
            f'src="../assets/{bg_name}" '
            f'style="position:absolute;left:0;top:0;width:{W}px;height:{H}px;" />'
        )
        track += 1

    anim_lines: list[str] = []
    for layer in slide.get("layers", []):
        if layer.get("hidden"):
            continue
        if layer.get("type") == "key_point_card":
            continue
        img_name = _copy_asset(task_root, assets_dir, layer["image"])
        if not img_name:
            continue
        x, y, w, h = layer["bbox"]
        lstart = float(layer["start"])
        ldur = max(0.05, slide_dur - lstart)
        lid = layer["id"]
        body_parts.append(
            f'      <img id="{lid}" class="clip" '
            f'data-start="{lstart}" data-duration="{round(ldur, 3)}" '
            f'data-track-index="{track}" '
            f'src="../assets/{img_name}" '
            f'style="position:absolute;left:{x}px;top:{y}px;width:{w}px;height:{h}px;" />'
        )
        track += 1
        enter = layer.get("enter") or {}
        anim_lines.append(
            _anim_for(enter.get("type", "fade-in"), lstart,
                      float(layer.get("duration", 0.7))).replace("{id}", lid)
        )

    narration = html.escape(slide.get("narration", ""))

    body = "\n".join(body_parts)
    anim_block = "\n".join(anim_lines) if anim_lines else "    // no layers"

    return f"""<!doctype html>
<html lang="zh-Hant">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width={W}, height={H}" />
    <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
    <style>
      * {{ margin: 0; padding: 0; box-sizing: border-box; }}
      html, body {{ width: {W}px; height: {H}px; overflow: hidden; background: #000; }}
    </style>
  </head>
  <body>
    <div id="root"
         data-composition-id="{sid}"
         data-start="0"
         data-duration="{slide_dur}"
         data-width="{W}"
         data-height="{H}">
      <!-- narration: {narration} -->
{body}
    </div>
    <script>
      window.__timelines = window.__timelines || {{}};
      const tl = gsap.timeline({{ paused: true }});
{anim_block}
      window.__timelines["{sid}"] = tl;
    </script>
  </body>
</html>
"""


def _index_html(comp: dict, task_root: Path, assets_dir: Path) -> str:
    canvas = comp["canvas"]
    W, H = canvas["width"], canvas["height"]
    fps = canvas.get("fps", 30)
    total = sum(float(s["duration"]) for s in comp.get("slides", []))

    embeds: list[str] = []
    audios: list[str] = []
    cursor = 0.0
    n = len(comp.get("slides", []))
    for i, s in enumerate(comp.get("slides", [])):
        idx = s["index"]
        sid = f"slide_{idx:02d}"
        d = float(s["duration"])
        embeds.append(
            f'      <div data-composition-id="{sid}-embed" '
            f'data-composition-src="compositions/{sid}.html" '
            f'data-start="{round(cursor, 3)}" data-duration="{d}" '
            f'data-track-index="{n + i}"></div>'
        )
        audio_rel = s.get("audio") or ""
        if audio_rel:
            audio_name = _copy_asset(task_root, assets_dir, audio_rel)
            if audio_name:
                audios.append(
                    f'      <audio id="{sid}-audio" class="clip" '
                    f'data-start="{round(cursor, 3)}" '
                    f'data-duration="{d}" data-track-index="{i}" data-volume="1" '
                    f'src="assets/{audio_name}"></audio>'
                )
        cursor += d

    embeds_html = "\n".join(audios + embeds)

    return f"""<!doctype html>
<html lang="zh-Hant">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width={W}, height={H}" />
    <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
    <style>
      * {{ margin: 0; padding: 0; box-sizing: border-box; }}
      html, body {{ width: {W}px; height: {H}px; overflow: hidden; background: #000; }}
    </style>
  </head>
  <body>
    <div id="root"
         data-composition-id="root"
         data-start="0"
         data-duration="{round(total, 3)}"
         data-width="{W}"
         data-height="{H}"
         data-fps="{fps}">
{embeds_html}
    </div>
    <script>
      window.__timelines = window.__timelines || {{}};
      window.__timelines["root"] = gsap.timeline({{ paused: true }});
    </script>
  </body>
</html>
"""


def main():
    if not COMP_PATH.exists():
        sys.exit("composition.json not found — run build_composition.py first")
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
    comp = json.loads(COMP_PATH.read_text(encoding="utf-8"))

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "compositions").mkdir(exist_ok=True)
    assets_dir = out_dir / "assets"
    assets_dir.mkdir(exist_ok=True)

    name = out_dir.name
    (out_dir / "package.json").write_text(json.dumps({
        "name": name, "private": True, "type": "module",
        "scripts": {
            "dev":     "npx --yes hyperframes@0.6.93 preview",
            "check":   "npx --yes hyperframes@0.6.93 lint && "
                       "npx --yes hyperframes@0.6.93 validate && "
                       "npx --yes hyperframes@0.6.93 inspect",
            "render":  "npx --yes hyperframes@0.6.93 render",
            "publish": "npx --yes hyperframes@0.6.93 publish",
        },
    }, indent=2), encoding="utf-8")
    (out_dir / "hyperframes.json").write_text(json.dumps({
        "$schema": "https://hyperframes.heygen.com/schema/hyperframes.json",
        "registry": "https://raw.githubusercontent.com/heygen-com/hyperframes/main/registry",
        "paths": {
            "blocks": "compositions",
            "components": "compositions/components",
            "assets": "assets",
        },
    }, indent=2), encoding="utf-8")
    (out_dir / "meta.json").write_text(json.dumps({
        "id": name, "name": name,
    }, indent=2), encoding="utf-8")

    for slide in comp.get("slides", []):
        sid = f"slide_{slide['index']:02d}"
        html_str = _slide_html(slide, comp, ROOT, assets_dir)
        (out_dir / "compositions" / f"{sid}.html").write_text(html_str, encoding="utf-8")

    (out_dir / "index.html").write_text(_index_html(comp, ROOT, assets_dir), encoding="utf-8")

    n_slides = len(comp.get("slides", []))
    n_assets = sum(1 for _ in assets_dir.iterdir())
    print(f"HF project -> {out_dir.relative_to(ROOT) if out_dir.is_relative_to(ROOT) else out_dir}")
    print(f"  {n_slides} slide compositions, {n_assets} assets")
    print(f"  Next: cd {out_dir.name} && npm run check")


if __name__ == "__main__":
    main()
