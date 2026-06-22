---
name: skill-pptx-to-animated-video
description: Convert an image-only slide deck (NotebookLM-style PPTX/PDF where every slide is one flat image) into an animated MP4 with TTS narration and subtitles. Segments each slide into element layers (cards, arrows, charts, highlight groups), schedules layer entrances from the narration timeline, previews in the browser, and renders via ffmpeg. Use when the user wants slide images turned into a narrated/animated video.
---

# PPTX → Animated Narrated Video

Treat every slide as a flat image — never assume editable PowerPoint elements.
All scripts run **from the project root** (they read/write `output/`, `audio/`,
`narration/`, `hyperframes/`, `final/` under the cwd).

## Prerequisites

```
pip install opencv-python pymupdf pillow edge-tts
```
ffmpeg: system install, or `npm i ffmpeg-static ffprobe-static` in the project,
or set `FFMPEG_PATH`. Scripts auto-discover it.

## Configuration

Canvas / voice / caption settings live in three JSON files under this skill's
`config/` folder (loaded by `scripts/config.py`):

- `project_config.json` — `canvas` (aspect/width/height/fps) + `render`
  (crf/preset/transition/sample_rate).
- `voice_config.json` — TTS `engine`/`voice`/`rate` (CLI args to `tts_edge.py`
  still override these).
- `caption_config.json` — subtitle chunking + burned-in ASS style + letterbox
  band.

These hold the project defaults. To customise one deck without touching the
skill, drop a file of the same name in the **task's project root** (cwd); it is
deep-merged over the defaults, so you only specify the keys you change. This is
the layer a UI writes per task.

### Per-edit overrides (`overrides.json`)

User/AI edits go in `overrides.json` at the project root (`scripts/overrides.py`),
NOT into the generated artifacts — `metadata.json` and `narration_script.md`
stay pristine. Schema is keyed by slide:

```json
{
  "voice": { "voice": "zh-TW-YunJheNeural", "rate": "-10%" },
  "slide_03": {
    "narration": "edited narration text",
    "notes": "free-form note for the agent",
    "layers": { "slide_03_chart_01.png": { "start": 1.2, "animation": "zoom-in" } }
  }
}
```

Per-layer segmentation edits live in the same `layers` map:

- `hidden: true` — drop the layer from the composition.
- `merge_group: "g1"` — group members enter together; the primary (first by name)
  donates start + animation.
- `bbox: [x,y,w,h]` — reframe the layer; the server cuts a fresh transparent PNG
  via `scripts/reextract.py` into `output/slide_##/edits/<name>` and
  `build_composition.py` prefers that file when present.
- `split` — two shapes, both emit two children that inherit timing /
  z / animation / merge_group / hidden from the parent (children also pick
  up their own overrides keyed by the synthetic child name, so split
  children are first-class layers):
    - `{ "axis": "x"|"y", "at": 0..1 }` — single cut into adjacent halves
    - `{ "bboxes": [[x,y,w,h], [x,y,w,h]] }` — two explicit regions,
      may be non-adjacent / overlap
  Server re-extracts each child PNG into `edits/<child>.png`; when
  center-attribution misses, also caches re-OCR in `edits/<stem>.ocr.json`.
- `ocr_corrected: str|null` — human-verified OCR text; promoted into
  composition.json's `layer.ocr` with `corrected: true` + `confidence: 1.0`.

`tts_edge.py` speaks the effective narration (override text if present) with the
voice override; `build_timeline.py` and `build_composition.py` apply overrides
when building subtitles/timing and the resolved `composition.json`. A slide's
duration is re-probed from its current audio **only when that slide's narration
or the voice changed** — so editing voice/text no longer requires re-running
segmentation, and unedited slides stay byte-identical. (Needs ffprobe; without
it, duration falls back to the baked value.)

### Per-layer OCR evidence (`composition.json` layers[*].ocr)

When `output/slide_##/slide_ocr.json` exists, `build_composition.py` attributes
each line to the layer whose bbox contains the line's center, and attaches:

```json
"ocr": { "text": "full joined text", "confidence": 0.97, "line_count": 2 }
```

Layers with no attributed lines simply have no `ocr` field (composition stays
byte-identical for decks where the OCR step never ran).

**Split children** prefer a per-child OCR cache at
`output/slide_##/edits/<child_stem>.ocr.json` when one exists; otherwise they
fall back to slide-wide center-in-bbox aggregation. The server writes that
cache only when center-attribution missed the child AND the parent had OCR —
so the common case (cut through whitespace, both children pick up lines via
center-in-bbox) pays nothing; the boundary case (a line straddles the cut) is
recovered by lazy `RapidOCR` re-run on the child crop.

**Manual corrections** live in `overrides.json` as
`slide_XX.layers.<name>.ocr_corrected`: a string replaces the auto-OCR text
and stamps `confidence = 1.0`, `corrected = true`; `null` (or an empty string,
or a string identical to the auto-OCR text) removes the correction.

UI affordances:
- `confidence < 0.60` with attributed lines → **red** layer-row + ⚠ flag.
- `type ∈ {text_block, key_point_card, annotation, table}` with `line_count == 0`
  → **orange** layer-row + ◑ flag ("no OCR text · click to add").
- ✎ badge marks a manually corrected layer.
- Clicking any OCR row opens a modal with the full text, type/conf/lines/bbox
  meta, a textarea, Save (writes `ocr_corrected`) and Reset (clears it).

### HyperFrames templates (single source of truth)

The draft-preview HTML/CSS/JS lives ONCE at
`skill-pptx-to-animated-video/hyperframes/{index.html, styles.css, animation.js}`.
`build_timeline.py` copies these three files verbatim into each task's
`hyperframes/` on every build. Editing the skill copy fixes every deck on the
next rebuild — no `task=*/hyperframes/animation.js` heredoc to keep in sync.

The preview reads `composition.json` (one directory up from `hyperframes/`)
directly, so it never drifts from the rendered MP4. No `project.json` is
written; that older intermediate is gone.

### Undo (`/undo`, `.overrides_history/`)

Every `/apply` snapshots the prior `overrides.json` into
`task=*/.overrides_history/NNNN.json` (ring buffer, cap 20) before the
new edit lands. `/undo` pops the most recent snapshot, restores it as
`overrides.json`, and rebuilds — re-running TTS + timeline only when
narration/voice differ between snapshots. Notes-only payloads are exempt
from snapshotting (they'd saturate the buffer on every keystroke).

`/history-depth` is the depth check the UI calls on task switch to
enable/disable the ↶ Undo button.

### Forced-alignment (`align_subtitles.py`, optional)

If `whisper-timestamped` is installed, the pipeline auto-rewrites
`narration/subtitles.{srt,vtt}` with timings snapped to word boundaries
detected from the actual MP3 audio. Strict char-by-char matching against
the cue text; when coverage drops below 40% (homophones / punctuation
divergence), falls back to time-proportional distribution over the
detected speech range. When `whisper-timestamped` is NOT installed, the
script prints a friendly message and returns 0 — the pre-existing
char-proportional SRT stays in place. `pip install whisper-timestamped`
to enable.

### HyperFrames adapter (`export_hyperframes.py`, speculative)

Reads `composition.json` and writes `hyperframes/project.hf.json` with a
best-effort guess at the HyperFrames project shape. The field map at the
top of the script (`_HF_FIELD_MAP`, `_HF_ANIM_NAMES`) is a placeholder —
you must verify against an actual HF export before importing. The output
carries a `_speculative` marker until that's done.

### Aspect ratio toggle (`/aspect`)

The UI exposes three pills — `16:9 / 9:16 / 1:1` — that rebuild a task at a
new canvas size. The server's `set_aspect(task_dir, aspect)` writes the new
`{aspect, width, height}` into the task's `project_config.json`, then wipes
artifacts that carry pixel coordinates (`output/slide_*/`, `overrides.json`,
`composition.json`, `narration/narration_timing.json`) and runs render →
ocr → segment → timeline → composition. **TTS is skipped** — audio is
aspect-agnostic, so the existing MP3s under `audio/` are reused.

Layer edits (split / bbox / merge / hide / OCR corrections) **are reset** —
their coordinates only make sense at the original aspect. The UI shows a
confirmation warning before triggering. Presets: 16:9 = 1920×1080, 9:16 =
1080×1920, 1:1 = 1080×1080. The render uses `min(W/page.w, H/page.h)` to
fit-inside-pad-white, so changing aspect on a deck designed for the
original ratio will produce visible letterbox; smart reflow is out of
scope.

## Workflow

`SKILL_DIR` = this skill's folder; run scripts as `python "<SKILL_DIR>/scripts/<name>.py"`.

1. **Slides → PNG**: need a PDF (export PPTX to PDF if necessary), then
   `render_slides.py deck.pdf` → `output/slide_##/original.png` (1920x1080).
2. **Write the narration** `narration/narration_script.md` yourself (the model)
   after looking at every slide image. Format — one section per slide:
   ```
   ## Slide 01 - <title>

   <口語化、教學型旁白，2-4 句>
   ```
   Match the deck's language (zh-TW deck → 繁體中文旁白). Pace ≈ 130–160
   chars/min; each slide's text should speak in roughly 10–16 s.
   The server's `/pipeline` has an `auto_narration` fallback that seeds this
   file from OCR text so a standalone run never dead-ends — but OCR drafts are
   only stitched-together slide fragments (no understanding) and read poorly,
   so always prefer writing the narration yourself.
3. **TTS**: `tts_edge.py [voice] [rate]` (defaults: `zh-TW-HsiaoChenNeural`,
   rate `-8%` — teaching pace). Pass a different voice for other languages.
   For a snappier delivery use `+38%` (≈1.5× the default speech speed) or
   higher; the user has previously asked for 1.5× faster narration. Audio
   durations drive all timing, so do this BEFORE segmenting. If running from
   a subdirectory and `audio_duration` returns None, the scripts couldn't
   find ffprobe — create a junction to the project's `node_modules`, e.g.:
   ```
   New-Item -ItemType Junction -Path .\node_modules `
     -Target ..\node_modules
   ```
4. **Segment**: `segment_elements.py [slide numbers]`. Prints per-slide layer
   count and a reconstruction diff — **any nonzero diff is a bug, stop and fix**.
   Outputs per slide: transparent layers + `background.png` + `metadata.json`,
   plus review artifacts:
   - `work_preview/element_debug/slide_##_debug.jpg` — original / detected
     boxes / background-after-cut / reconstruction
   - `work_preview/slide_##_layer_gallery.jpg` — each layer on a checkerboard
     with name/type/position/start time
5. **Review loop (do not skip)**: read several galleries yourself, then show
   the user the galleries for the most complex slides and ask if the cuts
   match their expectation. Iterate on `segment_elements.py` thresholds until
   approved. Re-run is cheap; renders are not.
6. **Timeline**: `build_timeline.py` → `narration_timing.json`, SRT/VTT,
   `hyperframes/` browser preview. Preview: `python -m http.server 8080` →
   `http://localhost:8080/hyperframes/index.html`.
   Then `build_composition.py` → `composition.json`, the renderer-neutral
   "resolved" contract (canvas + caption style + per-slide layers with
   semantic `enter` animations and stable layer ids). It is read-only w.r.t.
   all other artifacts; `validate_composition.py` checks it faithfully matches
   metadata + timing. This is the single document renderers/adapters consume.
7. **Render — only after the user approves the cuts** (it's the expensive
   step): `render_final_video.py` → `final/final_video_with_voiceover.mp4` +
   burned-subtitles version. It consumes `composition.json` for layer geometry,
   animation and timing (so any overrides take effect here) -- run
   `build_composition.py` first. Run it in the background; it prints one line
   per slide. (Audio MP3s and subtitles.srt are still produced by the
   TTS/timeline steps; wiring overrides through to those is a follow-up.)

## Quality bar for segmentation (learned from human review)

- **Human reading logic rules everything.** Title first, then rows top-to-
  bottom, left-to-right inside a row (row clustering by vertical centre — not
  fixed bands, they misorder at boundaries). On two-panel layouts **left
  panel must fully reveal before the right panel** — the default row-cluster
  sort already does this once each panel is one layer.
- **A sentence is ONE layer.** Never let words of one sentence appear as
  separate animated pieces. Word-gap merging must scale with font size
  (large display fonts have 35px+ word spacing).
- **Cards/flow boxes/tables/PANELS** are detected via enclosed interiors
  (holes in the ink mask, `cv2.RETR_CCOMP` children). Hand-drawn arrows touch
  box borders and defeat plain connected components, so the hole+border test
  is the only reliable signal. Adjacent table cells merge into one table.
  Size cap is 0.48×slide-area (NOT 0.35) so half-slide bounded regions like
  side-by-side "comparison" panels qualify as single panel-cards. Outer slide
  chrome is still rejected by the 0.9-width / 0.9-height caps.
- **A "collage" is ONE illustration.** A `collage_cluster` pass after the
  word/line merge groups 3+ pieces that touch or sit within 80px with axis
  overlap, where the joint bbox is ≤30% of slide, piece-bbox density is
  ≥30%, aspect ratio ≤4, and **raw ink ratio inside the bbox is ≥0.17**.
  That last threshold separates a real visual pile (paper pile measured
  ~0.19) from a structured icon-over-caption grid (~0.14). Cross-column
  corridor between two pieces still vetoes the merge so grid layouts don't
  collapse.
- **Stamps/highlights default to merging into the underlying element.**
  REJECTED stamps, approval marks, hand-drawn highlights are visually part
  of the thing they mark — let the collage/card pass include them. Only
  carve them out as their own layer when the user explicitly asks (use
  `exclude_red` in the OVERRIDES merge spec).
- **Arrows/icons/loose text** come from the ink that remains after erasing
  card rects — that's what keeps card-border slivers out of arrow crops.
  Dashed arrows need the dash-chain rule (small fragments, gap < ~42px).
- **Red circle/doodle/note over a card** → one `highlight_group` with that
  card. It must not be torn apart, and must not swallow neighbouring cards
  (zero the alpha over other cards' rects, keeping only red ink there).
- **Red annotations drawn on charts/tables** (note text + vector arrow/
  star/circle) become separate `annotation` layers with stroke-mask alpha
  (include the faint anti-aliased skirt or you get pink ghosts + broken
  glyphs), whitened out of the chart crop, fading in ~0.85s after the
  chart. Distinguish from same-coloured data curves by stroke thickness
  (≥6px half-width) and glyph size (≤70px); curves are thinner and wider.
- **Axis labels belong to the chart** (rotated y-label, x caption, ticks).
- **Tiny/thin fragments touching a card** (area <2000px² or min side <26px)
  are border residue — fold them back into the card. Real small elements
  (flow arrows ~55x39) sit clear of cards and stay separate.
- **Trim, don't blindly absorb, when a piece grazes a card.** The trim/
  absorb step (`segment_elements.py` end of `detect_elements`) absorbs a
  non-card piece into an overlapping card when the cut needed to remove the
  overlap is large enough to mean "this piece really continues inside the
  card". But the threshold must be fraction-of-piece, NOT a fixed pixel
  count — a wide footer banner grazing the bottom of a panel-card by 15px
  (12% of the footer's 124px height) would otherwise be absorbed and
  inflate the card across the whole slide. Rule: `cut > 14 AND cut > 0.20 *
  perpendicular_dim`.
- **No layer may span the whole slide.** `merge_pass` and the `absorb` loop
  (unlike `collage_cluster`/`detect_cards`) had no size cap, so dense layouts
  (a pyramid + its side notes, a ring of repeated icons) could fuse into one
  slide-wide blob that only animates as a single chunk. `absorb()` now refuses
  any union whose bbox exceeds 0.78×width AND 0.60×height (the MAX_LAYERS
  capping pass is the lone exception, `guard=False`). Same principle as the
  collage/card area caps, applied to the merge cascade.
- **Watermarks** (e.g. NotebookLM, bottom-right) stay in the background.
- **Verification is non-negotiable**: compositing background + all layers
  must reproduce the original with 0 px diff (>20 intensity) on every slide.

### Per-slide segmentation overrides vs algorithm changes

`OVERRIDES` (top of `segment_elements.py`) is the escape hatch for slides whose
ground truth doesn't generalize. It is **empty in the skill baseline** and
loaded per deck from `<task>/seg_overrides.json` (keyed by slide number) via
`_load_seg_overrides()` — deck-specific tuning lives in that JSON instead of
forking the script (mirrors the per-task `overrides.json` convention). NOTE this
is a *segmentation-time* file, distinct from the post-segmentation
`overrides.json`, which can only hide/move/retime already-cut layers — never
split or reshape them. **Prefer fixing the algorithm over adding an override**;
add one only when generalizing the pattern would mis-fire on other decks.

Each slide entry may contain:
- `merge`: `{box, ...}` list — collapse pieces in the region into one layer.
  Flags: `tight` (carve from raw ink, ignore absorbed bboxes — splits a welded
  piece); `absorb` (override the 0.6 coverage threshold — lower it to consume a
  too-wide detected piece); `exclude_red`; `type` (force-classify);
  `no_annot` (this chart/table must NOT auto-extract red `annotation` layers);
  `irregular` (below).
- `suppress`: regions whose pieces drop back to the background (corner doodles, noise).
- `order`: bucket regions for a non-standard reveal order.

**`irregular` — true non-rectangular cut.** Layers export as opaque rectangular
crops by default (alpha=255; only annotations/highlights get a mask), which is
why a layer bbox can visually cover a neighbour. `irregular: true` cuts the layer
to its real silhouette: foreground = pixels differing from the paper colour
(`fill_color`) by >22, morphologically closed into one region, then contours
filled — so flat PALE fills (a pyramid's grey base tier, which carries no
dark/saturated ink) are kept, not dropped. Background reconstruction removes only
the silhouette (not the full bbox) so surrounding paper texture survives.
Reconstruction must still be 0-px-diff.

## Timing rules

- Narration first: each slide's duration = its voiceover length + 0.55s;
  layer starts spread across the narration window; 0.5s crossfade between
  slides. Animations: title fade-in-down, cards fade-in-up, arrows wipe-in,
  icons pop-in, charts draw-in, annotations fade-in (no movement — they sit
  over whitened pixels).
- If there is no TTS available, still produce script/subtitles/timing and a
  README explaining how to plug in ElevenLabs/Azure/OpenAI/Google TTS; do not
  block the pipeline.

## Rendering and subtitle burn

- After threshold tweaks: re-run segment + timeline + galleries only.
  **Never re-render the MP4s unless the user asks** — say the videos are now
  stale and give the one-command re-render instead. If only the subtitle
  style or SRT changed (no audio/layer changes), re-burn just the subtitle
  pass on the existing unsubbed MP4 — much faster than re-rendering:
  ```
  ffmpeg -i final/final_video_with_voiceover.mp4 -vf "scale=...,pad=...,subtitles=..." \
    -c:v libx264 -preset veryfast -crf 18 -c:a copy \
    final/final_video_with_voiceover_and_subtitles.mp4
  ```
- Subtitle burn — **letterbox the slide into the top 960px** so a 120px
  dark band at the bottom is dedicated to subtitles. Decks routinely have
  content (footer banners, sub-questions, alert boxes) hugging the bottom
  of the slide; letterboxing means subtitles can NEVER overlap that
  content, no matter the deck. The unsubbed `final_video_with_voiceover.mp4`
  stays full-size — only the subbed version letterboxes:
  ```
  -vf "scale=1920:960,pad=1920:1080:0:0:color=0x101010,
       subtitles=narration/subtitles.srt:force_style='FontName=Microsoft JhengHei,
       FontSize=11,PrimaryColour=&H00FFFFFF,BorderStyle=3,Outline=8,Shadow=0,
       BackColour=&H66000000,MarginL=30,MarginR=30,MarginV=10'"
  ```
  (ASS sizes are relative to 288-line script resolution; 16+ overflows the
  frame.) `BorderStyle=3` + a semi-transparent `BackColour` paints a dark
  box behind the text. `Outline=8` widens the box padding around the
  glyphs. The 1920:960 scale is a mild ~11% vertical squish — viewers
  rarely notice, and the band gives the subtitle a clean home.
  **libass alpha is inverted**: `00` = fully opaque, `FF` = fully
  transparent — so `PrimaryColour=&H00FFFFFF` is opaque white and
  `BackColour=&H66000000` is a ~60%-opaque black box. Getting this wrong
  (e.g. `&HFFFFFFFF` for white) makes the text invisible.

## Subtitle chunking (a sentence is NOT a cue)

`build_timeline.py` splits each slide's narration into short cues, NOT one
giant cue per slide. The previous "one cue per slide" form produced 12-23s
blocks of 100-160 CJK chars that wrapped to 4-5 lines and climbed into the
slide content area. The chunker (`chunk_narration`):

1. Split on sentence endings (`。！？`).
2. If any sentence is still over `SUB_CHUNK_MAX = 32` CJK chars, split
   further on clause boundaries (`，：；、`), greedily packing clauses up to
   the limit.
3. Time each chunk proportionally to its char count within the slide's
   speech window (slide end minus 0.35s tail silence).

The result is 1-2 line subtitles that read in sync with the speech. Keep
`SUB_CHUNK_MAX` around 30-36 for CJK; bump higher for languages with
shorter character counts per spoken second.
