"""Element-level segmentation for image-based slides.

Cuts each slide into animatable element layers:
- individual cards / flow boxes (box border + inner text as one layer)
- individual arrows
- icons, charts, standalone text blocks
- highlight groups: red circle / annotation overlapping an element is merged
  with that element into a single layer instead of being torn apart

Run from the project root (the directory containing output/). Slides must
already exist as output/slide_##/original.png (see render_slides.py).
If audio/slide_##_voiceover.mp3 exists its duration drives the slide timing.

Usage: python segment_elements.py [slide numbers...]
"""

import json
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
DEBUG = ROOT / "work_preview" / "element_debug"
GALLERY = ROOT / "work_preview"
WIDTH, HEIGHT = 1920, 1080


def find_ffprobe():
    local = ROOT / "node_modules" / "ffprobe-static" / "bin" / "win32" / "x64" / "ffprobe.exe"
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


def slide_numbers():
    nums = []
    for p in sorted(OUT.glob("slide_*/original.png")):
        m = re.match(r"slide_(\d+)", p.parent.name)
        if m:
            nums.append(int(m.group(1)))
    return nums

MARGIN = 16
MAX_LAYERS = 16

# Per-slide fixes from the human review loop. A "merge" region collapses every
# detected piece inside it -- plus any ink the detector stranded in the
# background there (the hollow display "Z" on slide 1, "Mode]" on slide 9) --
# into one layer, because a sentence must animate as one unit. A "suppress"
# region drops junk pieces (patches of bare grid, frame slivers); their pixels
# stay in the background.
# Per-slide OVERRIDES are DECK-SPECIFIC. This baseline copy is empty -- add
# entries in the copy of this file that lives inside the deck's own scripts/
# directory (it gets created when you run from the project root). Each entry
# is keyed by 1-based slide number and may contain:
#   merge:    list of {"box": [x,y,w,h], ...flags}. Every detected piece whose
#             bbox overlaps the region by >= absorb (default 0.6) collapses
#             into one layer, plus any raw ink left in the region by the
#             detector. Flags:
#               type: force-classify the merged layer ("title", "table", ...)
#               tight: True -- carve from raw mask, ignore absorbed bboxes;
#                      use to SPLIT a piece welded by a thin divider line
#               exclude_red: True -- red pieces in the region stay separate
#                      (a stamp landing on top of an illustration later); the
#                      merged layer gets alpha holes where the red ink sits
#               absorb: override the default 0.6 coverage threshold
#   suppress: list of [x,y,w,h]; pieces >=60% inside are dropped (their pixels
#             stay in the background -- use for grid patches, frame slivers)
#   order:    list of [x,y,w,h] bucket regions; pieces are sorted by which
#             bucket holds their centre. Use for column-by-column reveals
#             (slide 4 A->caption->B->caption pattern) or to force a non-
#             standard reading order the row-cluster sort wouldn't pick up.
# Prefer fixing the algorithm over adding an override -- this skill is meant
# to be reusable. Only override when the same pattern would mis-fire on
# another deck if generalized.
OVERRIDES = {}


def load_slide(slide_num):
    path = OUT / f"slide_{slide_num:02d}" / "original.png"
    return np.array(Image.open(path).convert("RGB"))


def ink_mask(img):
    """Raw foreground: dark ink or saturated color, margins cleared."""
    hsv = cv2.cvtColor(img, cv2.COLOR_RGB2HSV)
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    # 190 keeps text and drawn strokes while dropping the faint graph-paper
    # grid (gray 200-236 on this deck), whose perfectly straight lines would
    # otherwise weld components together and fake card borders everywhere.
    mask = ((gray < 190) | (hsv[:, :, 1] > 40)).astype(np.uint8) * 255
    mask[:MARGIN, :] = 0
    mask[-MARGIN:, :] = 0
    mask[:, :MARGIN] = 0
    mask[:, -MARGIN:] = 0
    return mask


def red_mask(img):
    hsv = cv2.cvtColor(img, cv2.COLOR_RGB2HSV)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    return (((h < 12) | (h > 168)) & (s > 90) & (v > 70)).astype(np.uint8) * 255


def red_mask_loose(img):
    """Looser variant that also catches dark brick-red strokes; only used to
    decide whether a split red annotation belongs to a card it straddles."""
    hsv = cv2.cvtColor(img, cv2.COLOR_RGB2HSV)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    return (((h < 12) | (h > 168)) & (s > 55) & (v > 55)).astype(np.uint8) * 255


def connect_mask(mask):
    """Connect strokes of the same glyph/word without bridging real gaps."""
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    out = cv2.dilate(mask, kernel, iterations=1)
    close_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    out = cv2.morphologyEx(out, cv2.MORPH_CLOSE, close_kernel, iterations=1)
    return out


def rect_of(box):
    x, y, w, h = box
    return x, y, x + w, y + h


def union_box(a, b):
    ax1, ay1, ax2, ay2 = rect_of(a)
    bx1, by1, bx2, by2 = rect_of(b)
    x1, y1 = min(ax1, bx1), min(ay1, by1)
    x2, y2 = max(ax2, bx2), max(ay2, by2)
    return [x1, y1, x2 - x1, y2 - y1]


def intersection_area(a, b):
    ax1, ay1, ax2, ay2 = rect_of(a)
    bx1, by1, bx2, by2 = rect_of(b)
    iw = min(ax2, bx2) - max(ax1, bx1)
    ih = min(ay2, by2) - max(ay1, by1)
    return max(0, iw) * max(0, ih)


def axis_gap_overlap(a, b):
    """(gap_x, gap_y, overlap_x, overlap_y) between two boxes."""
    ax1, ay1, ax2, ay2 = rect_of(a)
    bx1, by1, bx2, by2 = rect_of(b)
    gap_x = max(0, max(ax1, bx1) - min(ax2, bx2))
    gap_y = max(0, max(ay1, by1) - min(ay2, by2))
    overlap_x = min(ax2, bx2) - max(ax1, bx1)
    overlap_y = min(ay2, by2) - max(ay1, by1)
    return gap_x, gap_y, overlap_x, overlap_y


def cross_column_gap(a, b, raw):
    """True when the horizontal gap between two text pieces is a whitespace
    corridor separating layout columns (empty over a taller span than the
    pieces themselves, with both columns continuing above or below), rather
    than ordinary word spacing inside one line."""
    ax1, ay1, ax2, ay2 = rect_of(a)
    bx1, by1, bx2, by2 = rect_of(b)
    if ax2 <= bx1:
        lx2, rx1 = ax2, bx1
    elif bx2 <= ax1:
        lx2, rx1 = bx2, ax1
    else:
        return False
    if rx1 - lx2 < 6:
        return False
    y1, y2 = min(ay1, by1), max(ay2, by2)
    ext = y2 - y1
    yy1, yy2 = max(0, y1 - ext), min(HEIGHT, y2 + ext)
    corridor = raw[yy1:yy2, lx2 + 2 : rx1 - 2]
    if corridor.size == 0 or float((corridor > 0).mean()) >= 0.01:
        return False

    def continues(x1, x2):
        above = raw[yy1:y1, x1:x2]
        below = raw[y2:yy2, x1:x2]
        return (above.size > 0 and float((above > 0).mean()) > 0.02) or (
            below.size > 0 and float((below > 0).mean()) > 0.02
        )

    return continues(ax1, ax2) and continues(bx1, bx2)


def should_merge(a, b, raw=None):
    area_a = a[2] * a[3]
    area_b = b[2] * b[3]
    inter = intersection_area(a, b)
    small = min(area_a, area_b)
    # Containment / heavy overlap: inner text inside a card border,
    # red circle over a box, crossing annotation strokes.
    if small > 0 and inter / small >= 0.45:
        return True
    gap_x, gap_y, overlap_x, overlap_y = axis_gap_overlap(a, b)
    max_h = max(a[3], b[3])
    min_h = min(a[3], b[3])
    min_w = min(a[2], b[2])
    # Title banner: display fonts space words 40-110px apart, and nothing
    # else lives in the title band, so a generous gap is safe there.
    if a[1] < 185 and b[1] < 185 and max_h < 110 and gap_x < 110 and overlap_y > 0.5 * min_h:
        return True
    # Words on the same text line. Cards are detected separately via their
    # enclosed interiors and never enter this merge, so a generous word gap
    # cannot chain arrows and boxes together any more. The 48px base covers
    # CJK punctuation spacing and the 1.1*h scaling covers display fonts,
    # where spacing around Latin glyphs reaches ~85px; the corridor veto is
    # what keeps this from chaining across layout columns. max_h < 220 lets a
    # word join a paragraph that already merged into two lines. Pieces taller
    # than 130px merge only when both are solid display text -- hollow
    # outlined letters (chevron glyphs) of that size must stay separate.
    word_gap = max(48, 1.1 * min_h)  # spacing scales with the font size
    if max_h < 220 and min_h < 160 and gap_x < word_gap and overlap_y > 0.6 * min_h:
        # Very small gaps on the same baseline are usually a sentence split
        # across two detected pieces, not separate layout columns.
        if gap_x <= 20 and overlap_y > 0.7 * min_h:
            return True
        if min_h >= 130 and raw is not None:
            def density(box):
                x, y, w, h = box
                region = raw[max(0, y) : y + h, max(0, x) : x + w]
                return float((region > 0).mean()) if region.size else 0.0
            if density(a) < 0.22 or density(b) < 0.22:
                return False
        if raw is None or not cross_column_gap(a, b, raw):
            return True
    # Stacked lines of the same paragraph.
    if max_h < 220 and min_h < 95 and gap_y < 20 and overlap_x > 0.6 * min_w:
        return True
    # Chains of dashes forming a hand-drawn dashed arrow: the segments are
    # small, disconnected, and offset diagonally, so the word/line rules
    # above never catch them. Merge small fragments separated by a small gap
    # in any direction; the size cap lets a partially merged arrow keep
    # absorbing its remaining dashes without ever reaching card size.
    if max(a[2], a[3]) < 170 and max(b[2], b[3]) < 170 and gap_x + gap_y < 42:
        return True
    return False


def merge_pass(boxes, predicate):
    changed = True
    while changed:
        changed = False
        result = []
        used = [False] * len(boxes)
        for i in range(len(boxes)):
            if used[i]:
                continue
            current = list(boxes[i])
            used[i] = True
            grew = True
            while grew:
                grew = False
                for j in range(len(boxes)):
                    if used[j]:
                        continue
                    if predicate(current, boxes[j]):
                        current = union_box(current, boxes[j])
                        used[j] = True
                        grew = True
                        changed = True
            result.append(current)
        boxes = result
    return boxes


def collage_cluster(pieces, raw):
    """Group spatially-tight illustration pieces into single collage layers.

    Pairwise merge rules can't see that 5 overlapping paper outlines, a
    sticker spray, or any pile of irregular shapes reads as ONE illustration
    -- they only judge adjacent pairs and big shapes don't satisfy the
    word-gap or dash-chain rules. This pass finds connected clusters of
    pieces that touch or overlap, fill a non-trivial fraction of their joint
    bbox, and live inside a bounded region, and collapses each cluster into
    one piece. A column corridor between two pieces still vetoes the merge
    so grid layouts (icon over caption x 6) don't collapse into one blob.
    """
    n = len(pieces)
    if n < 3:
        return pieces

    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def adjacent(a, b):
        gap_x, gap_y, ovx, ovy = axis_gap_overlap(a, b)
        # Overlapping shapes (a paper pile, a sticker stack).
        if ovx > 0 and ovy > 0:
            return True
        # Nearby shapes that align on at least one axis: a tiled illustration
        # rarely has pieces more than ~80px apart, while a grid layout
        # (chevron-A vs chevron-B at 179px gap) stays well outside this.
        # Cross-column corridors and the bbox/density caps below veto the
        # case where two genuinely distinct elements happen to fall within
        # 80px of each other.
        if gap_x + gap_y < 80 and max(ovx, ovy) > 0:
            if cross_column_gap(a, b, raw):
                return False
            return True
        return False

    for i in range(n):
        for j in range(i + 1, n):
            if adjacent(pieces[i], pieces[j]):
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj

    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    merged = []
    used = set()
    for indices in groups.values():
        if len(indices) < 3:
            continue
        members = [pieces[i] for i in indices]
        x1 = min(m[0] for m in members)
        y1 = min(m[1] for m in members)
        x2 = max(m[0] + m[2] for m in members)
        y2 = max(m[1] + m[3] for m in members)
        bw, bh = x2 - x1, y2 - y1
        bbox_area = bw * bh
        # Not the whole slide (would collapse legit multi-element regions),
        # not a stray dot pile either.
        if bbox_area > 0.30 * WIDTH * HEIGHT or bbox_area < 60000:
            continue
        # Density: members must fill enough of their joint bbox. A loose
        # constellation of axis labels or stray annotations leaves most
        # of the bbox empty.
        member_area = sum(m[2] * m[3] for m in members)
        if member_area / bbox_area < 0.30:
            continue
        # A very elongated cluster is more likely a chain of caption text or
        # a connector run than an illustration collage.
        if min(bw, bh) > 0 and max(bw, bh) / min(bw, bh) > 4:
            continue
        # Raw ink density distinguishes a real collage (paper pile, sticker
        # spray -- ink fills most of the bbox) from a structured grid layout
        # (chevron letters + caption text -- mostly empty space with hollow
        # outlines and sparse glyphs). Measured: slide 4 A/B grid ~0.14,
        # slide 2 paper pile ~0.19. 0.17 keeps the pile, rejects the grid.
        bbox_region = raw[y1:y2, x1:x2]
        if bbox_region.size == 0 or float((bbox_region > 0).mean()) < 0.17:
            continue
        merged.append([x1, y1, bw, bh])
        used.update(indices)

    if not merged:
        return pieces
    return [pieces[i] for i in range(n) if i not in used] + merged


def tight_refine(box, raw_mask, pad=5):
    x, y, w, h = box
    x, y = max(0, x), max(0, y)
    w, h = min(WIDTH - x, w), min(HEIGHT - y, h)
    region = raw_mask[y : y + h, x : x + w]
    ys, xs = np.where(region > 0)
    if len(xs) == 0:
        return None
    x1 = max(0, x + int(xs.min()) - pad)
    y1 = max(0, y + int(ys.min()) - pad)
    x2 = min(WIDTH, x + int(xs.max()) + 1 + pad)
    y2 = min(HEIGHT, y + int(ys.max()) + 1 + pad)
    return [x1, y1, x2 - x1, y2 - y1]


def border_ring_fraction(connected, box):
    """Evidence that a drawn rectangular border surrounds a hole: for each
    side, the best single row/column of the thin band just outside the hole
    bbox must be almost fully covered in ink -- a real border stroke is one
    continuous straight line there. Aggregating the whole band instead would
    let dilated neighbouring text fake a border, which is exactly how phantom
    holes (empty space fenced in by frames, connector webs, curved arrows)
    sneak in and chain unrelated cards together."""
    x, y, w, h = box
    t = 16
    sides = []
    top = connected[max(0, y - t) : max(0, y - 2), x : x + w]
    bottom = connected[min(HEIGHT, y + h + 2) : min(HEIGHT, y + h + t), x : x + w]
    left = connected[y : y + h, max(0, x - t) : max(0, x - 2)]
    right = connected[y : y + h, min(WIDTH, x + w + 2) : min(WIDTH, x + w + t)]
    for band, axis in ((top, 1), (bottom, 1), (left, 0), (right, 0)):
        if band.size == 0:
            sides.append(0.0)
            continue
        coverage = (band > 0).mean(axis=axis)
        sides.append(float(coverage.max()))
    return min(sides)


def detect_cards(connected, raw):
    """Cards / flow boxes / table cells: their interiors are holes enclosed by
    drawn borders, which survive even when arrow tips touch the borders."""
    contours, hierarchy = cv2.findContours(connected, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    cards = []
    if hierarchy is None:
        return cards
    for idx, c in enumerate(contours):
        if hierarchy[0][idx][3] == -1:
            continue  # outer contour, not a hole
        x, y, w, h = cv2.boundingRect(c)
        if w * h < 7000 or w < 110 or h < 55:
            continue
        if cv2.contourArea(c) < 0.55 * w * h:
            continue  # crescent-shaped hole, not a box interior
        # Blueprint decks wrap whole slides/diagrams in decorative frames whose
        # interiors are also holes; a near-slide-spanning hole is a container,
        # not a card. Its border ink stays connective and lands in the
        # background. 0.48 admits half-slide panel cards (slide 2's left and
        # right panels are ~38% each with ring=1.0) while still rejecting
        # outer slide chrome (>0.9 width or height) and >=50% containers.
        if w > 0.9 * WIDTH or h > 0.9 * HEIGHT or w * h > 0.48 * WIDTH * HEIGHT:
            continue
        # Phantom holes (empty space fenced in by frames, connector webs or
        # curved arrows) pass the rectangularity test but have no drawn border
        # of their own; merging them would chain unrelated cards together.
        if border_ring_fraction(connected, (x, y, w, h)) < 0.85:
            continue
        # A corridor between two panels is fenced by real borders on all four
        # sides and passes the ring test, and the arrows crossing it give it a
        # little ink; a real card is filled with text (>=10% ink in practice).
        interior = raw[y + 12 : y + h - 12, x + 12 : x + w - 12]
        if interior.size == 0 or float((interior > 0).mean()) < 0.06:
            continue
        cards.append([x, y, w, h])

    def card_adjacent(a, b):
        # Unpadded interiors: true table cells are separated only by their
        # shared border stroke (<8px); separate stacked cards sit ~30px apart
        # and must NOT chain (padding first would eat that distinction).
        if intersection_area(a, b) > 0:
            return True  # shared border (table cells)
        gap_x, gap_y, overlap_x, overlap_y = axis_gap_overlap(a, b)
        if gap_x < 8 and overlap_y > 0.5 * min(a[3], b[3]):
            return True
        if gap_y < 8 and overlap_x > 0.5 * min(a[2], b[2]):
            return True
        return False

    cards = merge_pass(cards, card_adjacent)
    padded = []
    for x, y, w, h in cards:
        pad = 12  # cover the border stroke around the interior
        x1, y1 = max(0, x - pad), max(0, y - pad)
        x2, y2 = min(WIDTH, x + w + pad), min(HEIGHT, y + h + pad)
        padded.append([x1, y1, x2 - x1, y2 - y1])
    return padded


def red_ratio_of(img, box):
    x, y, w, h = box
    region = red_mask(img[y : y + h, x : x + w])
    ink = ink_mask(img)[y : y + h, x : x + w]
    ink_count = int((ink > 0).sum())
    if ink_count == 0:
        return 0.0
    return int((region > 0).sum()) / ink_count


def apply_overrides(items, raw, img, slide_num):
    ov = OVERRIDES.get(slide_num)
    if not ov:
        return items

    def coverage(box, region):
        return intersection_area(box, region) / max(1, box[2] * box[3])

    for region in ov.get("suppress", []):
        items = [it for it in items if coverage(it["box"], region) < 0.6]
    for spec in ov.get("merge", []):
        region = spec["box"]
        threshold = spec.get("absorb", 0.6)
        inside, excluded = [], []
        for it in items:
            if coverage(it["box"], region) < threshold:
                continue
            # With exclude_red, a red piece inside the region (the REJECTED
            # stamp on the paper pile) keeps its own layer; the merged layer
            # gets alpha holes there so the stamp can land on top later.
            if spec.get("exclude_red") and red_ratio_of(img, it["box"]) > 0.4:
                excluded.append(it)
            else:
                inside.append(it)
        inside_ids = {id(it) for it in inside}
        items = [it for it in items if id(it) not in inside_ids]
        rx, ry, rw, rh = region
        ys, xs = np.where(raw[ry : ry + rh, rx : rx + rw] > 0)
        ink_box = None
        if len(xs):
            # Ink the detector left in the background inside this region
            # belongs to the merged layer too.
            ink_box = [
                rx + int(xs.min()), ry + int(ys.min()),
                int(xs.max() - xs.min()) + 1, int(ys.max() - ys.min()) + 1,
            ]
        if spec.get("tight"):
            # Split mode: the region carves its own ink out of a consumed
            # wider piece, so the absorbed piece's bbox must not widen it.
            boxes = [ink_box] if ink_box else []
        else:
            boxes = [it["box"] for it in inside] + ([ink_box] if ink_box else [])
        if not boxes:
            continue
        box = boxes[0]
        for b in boxes[1:]:
            box = union_box(box, b)
        item = {
            "box": list(box),
            "sort_box": list(box),
            "card": any(it["card"] for it in inside),
            "highlight": any(it["highlight"] for it in inside),
            "force_type": spec.get("type"),
        }
        if excluded:
            # Hole out ALL red ink in the region (the stamp's frame reaches
            # beyond its detected bbox) and grow the excluded piece's box to
            # cover every hole, so the full stamp appears in one moment and
            # reconstruction stays perfect.
            red = cv2.dilate(
                red_mask_loose(img),
                cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)),
            )
            region_red = np.zeros_like(red)
            region_red[ry : ry + rh, rx : rx + rw] = red[ry : ry + rh, rx : rx + rw]
            ys2, xs2 = np.where(region_red > 0)
            if len(xs2):
                red_box = [
                    int(xs2.min()), int(ys2.min()),
                    int(xs2.max() - xs2.min()) + 1, int(ys2.max() - ys2.min()) + 1,
                ]
                mask = np.full((HEIGHT, WIDTH), 255, dtype=np.uint8)
                mask[region_red > 0] = 0
                item["force_mask"] = mask
                for ex in excluded:
                    ex["box"] = union_box(ex["box"], red_box)
                    ex["sort_box"] = list(ex["box"])
        items.append(item)
    return items


def detect_elements(img, slide_num):
    raw = ink_mask(img)
    connected = connect_mask(raw)
    cards = detect_cards(connected, raw)

    # Everything outside cards: arrows, icons, standalone text, highlights.
    remaining = connected.copy()
    raw_remaining = raw.copy()
    for cx, cy, cw, ch in cards:
        cv2.rectangle(remaining, (cx, cy), (cx + cw, cy + ch), 0, -1)
        cv2.rectangle(raw_remaining, (cx, cy), (cx + cw, cy + ch), 0, -1)
    contours, _ = cv2.findContours(remaining, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    pieces = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if w * h < 160:
            continue
        # NotebookLM watermark in the bottom-right corner stays in background.
        if y > HEIGHT - 110 and x > WIDTH - 360 and h < 70:
            continue
        # Connective skeleton: an outer frame / connector-line web left after
        # erasing the cards it links. Its bbox spans most of the slide, so any
        # bbox-based merge would swallow every real element inside it. The
        # lines themselves stay in the background (blueprint chrome shows from
        # frame one), but dense content islands welded into the web -- node
        # boxes, label text -- are rescued back out as their own pieces.
        if w * h > 0.40 * WIDTH * HEIGHT:
            comp = np.zeros_like(remaining)
            cv2.drawContours(comp, [c], -1, 255, -1)
            comp_raw = cv2.bitwise_and(raw_remaining, comp)
            bond = cv2.morphologyEx(
                comp_raw, cv2.MORPH_CLOSE,
                cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9)),
            )
            n_lbl, lbls, stats, _ = cv2.connectedComponentsWithStats(bond, connectivity=8)
            cv2.drawContours(remaining, [c], -1, 0, -1)
            cv2.drawContours(raw_remaining, [c], -1, 0, -1)
            for i in range(1, n_lbl):
                bx, by, bw, bh = (int(v) for v in stats[i][:4])
                # Lines and curves bond into huge or hair-thin blobs with
                # sparse ink; text lines and node boxes are compact and dense.
                if bw * bh < 2500 or min(bw, bh) < 16 or bw * bh > 0.08 * WIDTH * HEIGHT:
                    continue
                blob = lbls[by : by + bh, bx : bx + bw] == i
                sub = comp_raw[by : by + bh, bx : bx + bw]
                if float((sub[blob] > 0).mean() if blob.any() else 0) * blob.mean() < 0.15:
                    continue
                raw_remaining[by : by + bh, bx : bx + bw][blob] = sub[blob]
                remaining[by : by + bh, bx : bx + bw][blob] = 255
                pieces.append([bx, by, bw, bh])
            continue
        # Full-width status bars hugging the top/bottom slide edges are deck
        # chrome, not content; leave them in the background too. Real content
        # banners end >=40px from the edge -- only skim the outermost strip.
        if w > 1200 and h < 40 and (y < 40 or y + h > HEIGHT - 30):
            continue
        pieces.append([x, y, w, h])
    pieces = merge_pass(pieces, lambda a, b: should_merge(a, b, raw_remaining))
    pieces = collage_cluster(pieces, raw_remaining)

    # Group red annotation strokes (circle, doodle arrow, note text) together.
    def red_pair(a, b):
        gap_x, gap_y, _, _ = axis_gap_overlap(a, b)
        if gap_x + gap_y > 60:
            return False
        return red_ratio_of(img, a) > 0.55 and red_ratio_of(img, b) > 0.55

    pieces = merge_pass(pieces, red_pair)

    refined = []
    for box in pieces:
        tight = tight_refine(box, raw_remaining, pad=4)
        if tight is None:
            continue
        ink = raw_remaining[tight[1] : tight[1] + tight[3], tight[0] : tight[0] + tight[2]]
        if tight[2] * tight[3] < 900 or int((ink > 0).sum()) < 140:
            continue
        # Fragments of big decorative curves / connector webs are large but
        # nearly empty boxes (<9% ink); real text and drawings run >=16%.
        # They are chrome -- leave them in the background.
        if tight[2] * tight[3] > 12000 and float((ink > 0).mean()) < 0.09:
            continue
        # Long hair-thin slivers are remnants of divider lines, not content.
        if min(tight[2], tight[3]) < 22 and max(tight[2], tight[3]) > 380:
            continue
        refined.append(tight)

    # A red highlight overlapping a card becomes one highlight group with it.
    # The circle's bbox contains the card's black ink, so the red ratio of the
    # combined region is well below a pure-red piece; use a low threshold.
    # sort_box keeps the core element's position so a highlight group is
    # ordered where its wrapped card sits, not where its annotation floats.
    items = [
        {"box": list(c), "sort_box": list(c), "card": True, "highlight": False}
        for c in cards
    ]
    for piece in refined:
        if red_ratio_of(img, piece) > 0.22:
            target = None
            piece_area = piece[2] * piece[3]
            for item in items:
                if not item["card"]:
                    continue
                card_area = item["box"][2] * item["box"][3]
                inter = intersection_area(piece, item["box"])
                # Merge when the red mark wraps the card (circle around a box)
                # or sits mostly on top of it -- not when a red note merely
                # grazes a big chart's bbox.
                if inter / piece_area > 0.4 or inter / card_area > 0.8:
                    target = item
                    break
            if target is not None:
                target["box"] = union_box(target["box"], piece)
                target["highlight"] = True
                continue
        items.append({"box": piece, "sort_box": list(piece), "card": False, "highlight": False})

    # Red note text that points at a highlight (e.g. "HW6 v4") joins its group.
    changed = True
    while changed:
        changed = False
        for item in list(items):
            if item["card"] or item["highlight"]:
                continue
            if red_ratio_of(img, item["box"]) <= 0.5:
                continue
            for group in items:
                if not group["highlight"]:
                    continue
                gap_x, gap_y, _, _ = axis_gap_overlap(item["box"], group["box"])
                if gap_x + gap_y < 90:
                    group["box"] = union_box(group["box"], item["box"])
                    items.remove(item)
                    changed = True
                    break
            if changed:
                break

    def absorb(keep, other):
        keep["box"] = union_box(keep["box"], other["box"])
        keep["card"] = keep["card"] or other["card"]
        keep["highlight"] = keep["highlight"] or other["highlight"]

    # Rectangular bboxes may graze each other (highlight group over the next
    # card, arrow pad touching a card border); only merge substantial overlap.
    changed = True
    while changed:
        changed = False
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                a, b = items[i]["box"], items[j]["box"]
                small = min(a[2] * a[3], b[2] * b[3])
                if small <= 0:
                    continue
                ratio = intersection_area(a, b) / small
                if items[i]["highlight"] or items[j]["highlight"]:
                    # A highlight group's bbox legitimately overlaps its
                    # neighbours; only swallow loose pieces buried inside it,
                    # never another card.
                    other = items[j] if items[i]["highlight"] else items[i]
                    if other["card"] or other["highlight"] or ratio <= 0.6:
                        continue
                elif ratio <= 0.4:
                    continue
                else:
                    # A sprawling sparse piece (a connector fan, a dotted web)
                    # has a bbox that covers neighbouring cards entirely; a
                    # card must never disappear into a non-card piece.
                    smaller, bigger = (items[i], items[j]) if a[2] * a[3] < b[2] * b[3] else (items[j], items[i])
                    if smaller["card"] and not bigger["card"]:
                        continue
                absorb(items[i], items[j])
                items.pop(j)
                changed = True
                break
            if changed:
                break

    # Trim small loose pieces (arrows) so they don't carry slivers of a
    # neighbouring card border inside their crop. But if the piece's strokes
    # run right up to the cut line (an annotation written across a chart
    # edge), trimming would slice the drawing in half -- merge it into the
    # card instead. We probe a thin strip just outside the card boundary.
    full_red = red_mask_loose(img)

    def red_strip(x1, y1, x2, y2):
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(WIDTH, x2), min(HEIGHT, y2)
        if x2 <= x1 or y2 <= y1:
            return 0
        return int((full_red[y1:y2, x1:x2] > 0).sum())

    for item in list(items):
        if item["card"]:
            continue
        box = item["box"]
        for card_item in items:
            if not card_item["card"] or card_item is item:
                continue
            card = card_item["box"]
            if intersection_area(box, card) == 0:
                continue
            x1, y1, x2, y2 = rect_of(box)
            cx1, cy1, cx2, cy2 = rect_of(card)
            # A red annotation whose strokes continue inside the card rect was
            # split by the card-erase step; reunite it with the card layer.
            # (Card borders and flow arrows are black, so red is decisive.)
            if red_ratio_of(img, box) > 0.35:
                inside = max(
                    red_strip(cx2 - 14, y1, cx2, y2),
                    red_strip(cx1, y1, cx1 + 14, y2),
                    red_strip(x1, cy2 - 14, x2, cy2),
                    red_strip(x1, cy1, x2, cy1 + 14),
                )
                if inside > 60:
                    absorb(card_item, item)
                    items.remove(item)
                    break
            cut_left = cx2 - x1 if cx2 > x1 and cx1 <= x1 else None
            cut_right = x2 - cx1 if cx1 < x2 and cx2 >= x2 else None
            cut_top = cy2 - y1 if cy2 > y1 and cy1 <= y1 else None
            cut_bottom = y2 - cy1 if cy1 < y2 and cy2 >= y2 else None
            options = []
            if cut_left is not None and cut_left < 0.3 * box[2]:
                options.append((cut_left, "left"))
            if cut_right is not None and cut_right < 0.3 * box[2]:
                options.append((cut_right, "right"))
            if cut_top is not None and cut_top < 0.3 * box[3]:
                options.append((cut_top, "top"))
            if cut_bottom is not None and cut_bottom < 0.3 * box[3]:
                options.append((cut_bottom, "bottom"))
            small = box[2] < 280 and box[3] < 120
            if not options:
                # Overlap too deep to trim away: the piece genuinely spans the
                # card, so they belong together.
                absorb(card_item, item)
                items.remove(item)
                break
            cut, side = min(options)
            # A small piece (arrow) never has ink inside the card rect -- its
            # overlap is only bbox padding, so trimming is always safe. A big
            # piece overlapping by more than a sliver is an annotation whose
            # strokes continue inside the card crop; keep them together. But
            # the cut must be a meaningful fraction of the piece -- otherwise
            # a wide footer banner grazing the bottom of a panel card by 15px
            # gets falsely absorbed and inflates the card across the slide.
            perp = box[3] if side in ("top", "bottom") else box[2]
            if not small and cut > 14 and cut > 0.20 * perp:
                absorb(card_item, item)
                items.remove(item)
                break
            if side == "left":
                box[0] += cut
                box[2] -= cut
            elif side == "right":
                box[2] -= cut
            elif side == "top":
                box[1] += cut
                box[3] -= cut
            else:
                box[3] -= cut

    # Hand-drawn card borders wobble outside the detected card box, and the
    # trim step can leave thin slivers of that border ink hugging the card.
    # A sliver or tiny fragment touching a card is not a meaningful animation
    # element of its own -- fold it back into the card. Real small elements
    # (flow arrows ~55x39, dashed arrows) stay above these thresholds or sit
    # clear of any card.
    for item in list(items):
        if item["card"] or item["highlight"]:
            continue
        x, y, w, h = item["box"]
        if min(w, h) >= 26 and w * h >= 2000:
            continue
        for card_item in items:
            if not card_item["card"]:
                continue
            gap_x, gap_y, _, _ = axis_gap_overlap(item["box"], card_item["box"])
            if gap_x + gap_y < 8:
                absorb(card_item, item)
                items.remove(item)
                break

    # Axis tick labels, rotated axis names and captions belong to the big
    # chart next to them ("R-squared" is ~61x186, "Number of Features" is
    # ~333x57, tick labels are smaller still).
    def axis_label_shaped(w, h):
        return (w < 420 and h < 200) or (w < 200 and h < 420)

    changed = True
    while changed:
        changed = False
        for item in list(items):
            box = item["box"]
            if item["card"] or item["highlight"] or not axis_label_shaped(box[2], box[3]):
                continue
            for big in items:
                bb = big["box"]
                if big is item or big["highlight"]:
                    continue
                if bb[2] > 600 and bb[3] > 300:
                    gap_x, gap_y, _, _ = axis_gap_overlap(box, bb)
                    if gap_x + gap_y < 50:
                        absorb(big, item)
                        items.remove(item)
                        changed = True
                        break
            if changed:
                break

    items = apply_overrides(items, raw, img, slide_num)

    # Keep layer count manageable: merge nearest pieces.
    while len(items) > MAX_LAYERS:
        best = None
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                gx, gy, _, _ = axis_gap_overlap(items[i]["box"], items[j]["box"])
                d = gx + gy
                if best is None or d < best[0]:
                    best = (d, i, j)
        _, i, j = best
        absorb(items[i], items[j])
        items.pop(j)

    return items, raw


def classify(box, img, raw_mask, is_card, is_highlight):
    x, y, w, h = box
    region_raw = raw_mask[y : y + h, x : x + w]
    fill = float((region_raw > 0).mean())
    if is_highlight:
        return "highlight_group"
    # Real titles are wide banners; w > 700 keeps small status badges in the
    # top corners from stealing the title slot.
    if y < 185 and w > 700 and h < 260:
        return "title"
    if is_card:
        if w > 700 and h > 350 and fill < 0.13:
            return "chart"  # plot area whose crossing curves enclosed holes
        if w > 900 and h > 380:
            return "table"
        return "key_point_card"
    if w < 280 and h < 120 and (fill < 0.32 or w > 1.15 * h):
        return "arrow"
    if w < 210 and h < 210:
        return "icon"
    if w > 560 and h > 300 and fill < 0.16:
        return "chart"
    if w > 1100 and h > 380:
        return "chart"
    if min(w, h) < 100:
        return "text_block"
    return "illustration"


def extract_red_annotations(img, red_loose, box):
    """Find red annotation marks (note text, vector arrows, stars) drawn on a
    chart, separable from same-coloured data curves by glyph size and stroke
    thickness. Returns groups of {box, mask} in global coordinates."""
    x, y, w, h = box
    sub = (red_loose[y : y + h, x : x + w] > 0).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(sub, connectivity=8)
    if n <= 1:
        return []
    dist = cv2.distanceTransform(sub, cv2.DIST_L2, 5)
    members = []
    for i in range(1, n):
        cx, cy, cw, ch, area = stats[i]
        if area < 25:
            continue  # keep tiny marks like decimal points, drop pixel noise
        thick = float(dist[labels == i].max())
        if cw <= 70 and ch <= 70:
            members.append(i)  # note text character / punctuation
        elif thick >= 6.0 and cw <= 0.35 * w:
            members.append(i)  # thick annotation stroke (arrow / star / circle)
    if not members:
        return []
    boxes = [
        [int(stats[i][0]), int(stats[i][1]), int(stats[i][2]), int(stats[i][3]), i]
        for i in members
    ]

    def near(a, b):
        gap_x, gap_y, _, _ = axis_gap_overlap(a[:4], b[:4])
        return gap_x + gap_y < 80

    clusters = []
    remaining = list(boxes)
    while remaining:
        seed = remaining.pop()
        cluster = [seed]
        grew = True
        while grew:
            grew = False
            for other in list(remaining):
                if any(near(other, m) for m in cluster):
                    cluster.append(other)
                    remaining.remove(other)
                    grew = True
        clusters.append(cluster)

    groups = []
    for cluster in clusters:
        ids = [c[4] for c in cluster]
        area = sum(stats[i][4] for i in ids)
        if area < 800:
            continue
        mask = np.isin(labels, ids).astype(np.uint8) * 255
        # Pull in the faint anti-aliased skirt of the strokes (saturation too
        # low for the detection mask) so neither a pink ghost stays in the
        # chart nor the annotation glyphs come out with missing strokes.
        hsv = cv2.cvtColor(img[y : y + h, x : x + w], cv2.COLOR_RGB2HSV)
        hh, ss, vv = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
        faint = (((hh < 14) | (hh > 165)) & (ss > 25) & (vv > 45)).astype(np.uint8) * 255
        near = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (13, 13)))
        mask = cv2.bitwise_or(mask, cv2.bitwise_and(faint, near))
        mask = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
        ys, xs = np.where(mask > 0)
        gx1, gy1 = x + int(xs.min()), y + int(ys.min())
        gx2, gy2 = x + int(xs.max()) + 1, y + int(ys.max()) + 1
        full = np.zeros((HEIGHT, WIDTH), dtype=np.uint8)
        full[y : y + h, x : x + w] = mask
        groups.append({"box": [gx1, gy1, gx2 - gx1, gy2 - gy1], "mask": full})
    return groups


ANIMATION = {
    "title": "fade-in-down",
    "key_point_card": "fade-in-up",
    "table": "fade-in-up",
    "text_block": "fade-in-up",
    "chart": "draw-in",
    "icon": "pop-in",
    "arrow": "wipe-in",
    "illustration": "zoom-in",
    "highlight_group": "pop-in",
    "annotation": "fade-in",
}


def background_fill_color(img, raw_mask):
    bg_pixels = img[raw_mask == 0]
    if len(bg_pixels) == 0:
        return (255, 255, 255)
    return tuple(int(v) for v in np.median(bg_pixels, axis=0))


def segment_slide(slide_num, debug=True):
    img = load_slide(slide_num)
    slide_dir = OUT / f"slide_{slide_num:02d}"

    # Remove previous layer exports, keep original.png.
    for old in slide_dir.glob("*.png"):
        if old.name != "original.png":
            old.unlink()

    items, raw = detect_elements(img, slide_num)
    # Reading order: cluster items into rows by vertical centre, then go
    # left-to-right inside each row. (Fixed-size banding misorders rows whose
    # centres straddle a band boundary.)
    items = sorted(items, key=lambda it: it["sort_box"][1] + it["sort_box"][3] / 2)
    rows = []
    for it in items:
        cy = it["sort_box"][1] + it["sort_box"][3] / 2
        if rows and cy - rows[-1][0] <= 110:
            rows[-1][1].append(it)
            rows[-1][0] = sum(
                i["sort_box"][1] + i["sort_box"][3] / 2 for i in rows[-1][1]
            ) / len(rows[-1][1])
        else:
            rows.append([cy, [it]])
    items = [
        it
        for _, row in rows
        for it in sorted(row, key=lambda i: i["sort_box"][0])
    ]
    # An "order" override re-buckets items by which region holds their centre
    # (e.g. slide 4 reveals column by column: letter, then the words below).
    # The sort is stable, so ties keep the reading order computed above.
    order_regions = OVERRIDES.get(slide_num, {}).get("order")
    if order_regions:
        def order_key(it):
            x, y, w, h = it["sort_box"]
            cx, cy = x + w / 2, y + h / 2
            for idx, (rx, ry, rw, rh) in enumerate(order_regions):
                if rx <= cx < rx + rw and ry <= cy < ry + rh:
                    return idx
            return len(order_regions)
        items.sort(key=order_key)
    fill_color = background_fill_color(img, raw)

    duration = audio_duration(AUDIO / f"slide_{slide_num:02d}_voiceover.mp3")
    duration = round((duration or 6.0) + 0.55, 2)

    # Expand items with red annotations extracted from chart/table layers so
    # a note + vector arrow drawn over a chart becomes its own layer that can
    # appear later than the chart itself.
    red_loose = red_mask_loose(img)
    entries = []
    for item in items:
        layer_type = item.get("force_type") or classify(
            item["box"], img, raw, item["card"], item["highlight"]
        )
        entry = dict(
            item, type=layer_type, annot_masks=[],
            mask=item.get("force_mask"), src=item,
        )
        entries.append(entry)
        if layer_type in ("chart", "table"):
            for group in extract_red_annotations(img, red_loose, item["box"]):
                entry["annot_masks"].append(group["mask"])
                entries.append(
                    {
                        "box": group["box"],
                        "type": "annotation",
                        "mask": group["mask"],
                        "annot_masks": [],
                        "card": False,
                        "highlight": False,
                        "parent": entry,
                    }
                )

    # Humans read the main title first, wherever the band sort put it.
    entries.sort(key=lambda e: 0 if e["type"] == "title" else 1)

    background = img.copy()
    layers = []
    counts = {}
    cue_gap = max(0.55, (duration - 2.2) / max(1, len(entries)))
    for i, item in enumerate(entries):
        box = item["box"]
        x, y, w, h = box
        layer_type = item["type"]
        counts[layer_type] = counts.get(layer_type, 0) + 1
        filename = f"slide_{slide_num:02d}_{layer_type}_{counts[layer_type]:02d}.png"
        crop = img[y : y + h, x : x + w].copy()
        alpha = np.full((h, w), 255, dtype=np.uint8)
        if item["mask"] is not None:
            alpha = item["mask"][y : y + h, x : x + w]
        for annot in item["annot_masks"]:
            region = annot[y : y + h, x : x + w] > 0
            crop[region] = fill_color
        if item["highlight"]:
            # Where the group's bbox covers a neighbouring card, keep only the
            # red annotation ink so that card doesn't ghost in early.
            red = red_mask(img)
            red = cv2.dilate(red, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
            for other in items:
                if other is item.get("src") or not other["card"]:
                    continue
                ox, oy, ow, oh = other["box"]
                ix1, iy1 = max(x, ox), max(y, oy)
                ix2, iy2 = min(x + w, ox + ow), min(y + h, oy + oh)
                if ix2 > ix1 and iy2 > iy1:
                    sub = alpha[iy1 - y : iy2 - y, ix1 - x : ix2 - x]
                    sub[:] = red[iy1:iy2, ix1:ix2]
        Image.fromarray(np.dstack([crop, alpha])).save(slide_dir / filename)
        cv2.rectangle(background, (x, y), (x + w - 1, y + h - 1), fill_color, -1)
        cue_text = layer_type  # refine narration cues in narration_timing.json
        if layer_type == "annotation":
            # The chart it is drawn on must be fully visible first.
            start = round(min(item["parent"]["_start"] + 0.85, duration - 0.8), 2)
        else:
            start = round(min(0.45 + i * cue_gap, duration - 1.0), 2)
        item["_start"] = start
        layers.append(
            {
                "name": filename,
                "type": layer_type,
                "x": int(x),
                "y": int(y),
                "width": int(w),
                "height": int(h),
                "z_index": 5 + i,
                "animation": ANIMATION.get(layer_type, "fade-in"),
                "start": start,
                "duration": 0.7,
                "narration_cue": cue_text,
            }
        )
    Image.fromarray(background).save(slide_dir / "background.png")
    metadata = {
        "slide": slide_num,
        "width": WIDTH,
        "height": HEIGHT,
        "duration": duration,
        "layers": layers,
    }
    (slide_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if debug:
        boxes = [[l["x"], l["y"], l["width"], l["height"]] for l in layers]
        save_debug(slide_num, img, background, boxes, layers)
    return metadata


def save_debug(slide_num, img, background, boxes, layers):
    DEBUG.mkdir(parents=True, exist_ok=True)
    annotated = img.copy()
    palette = [
        (220, 40, 40), (40, 120, 220), (30, 160, 60), (200, 120, 20),
        (140, 60, 200), (0, 150, 160), (200, 30, 120), (100, 100, 30),
    ]
    for i, (box, layer) in enumerate(zip(boxes, layers)):
        x, y, w, h = box
        color = palette[i % len(palette)]
        cv2.rectangle(annotated, (x, y), (x + w, y + h), color, 3)
        cv2.putText(
            annotated, f"{i+1}:{layer['type']}", (x + 4, max(20, y - 8)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2, cv2.LINE_AA,
        )
    half = (WIDTH // 2, HEIGHT // 2)
    sheet = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
    sheet[: half[1], : half[0]] = cv2.resize(img, half)
    sheet[: half[1], half[0] :] = cv2.resize(annotated, half)
    sheet[half[1] :, : half[0]] = cv2.resize(background, half)
    sheet[half[1] :, half[0] :] = cv2.resize(reconstruct(slide_num, layers), half)
    Image.fromarray(sheet).save(DEBUG / f"slide_{slide_num:02d}_debug.jpg", quality=88)


def reconstruct(slide_num, layers):
    d = OUT / f"slide_{slide_num:02d}"
    recon = np.array(Image.open(d / "background.png").convert("RGB")).copy()
    for layer in layers:
        rgba = np.array(Image.open(d / layer["name"]).convert("RGBA"))
        x, y = layer["x"], layer["y"]
        h, w = rgba.shape[:2]
        a = rgba[:, :, 3:4].astype(np.float32) / 255
        roi = recon[y : y + h, x : x + w].astype(np.float32)
        recon[y : y + h, x : x + w] = (roi * (1 - a) + rgba[:, :, :3] * a).astype(np.uint8)
    return recon


def verify_slide(slide_num, layers):
    """Layers composited over the background must reproduce the original."""
    orig = np.array(
        Image.open(OUT / f"slide_{slide_num:02d}" / "original.png").convert("RGB")
    ).astype(int)
    recon = reconstruct(slide_num, layers).astype(int)
    return int((np.abs(orig - recon).max(axis=2) > 20).sum())


def checkerboard(h, w, cell=16):
    yy, xx = np.mgrid[0:h, 0:w]
    c = (((yy // cell) + (xx // cell)) % 2 * 28 + 200).astype(np.uint8)
    return np.dstack([c, c, c])


def layer_gallery(slide_num, layers):
    """One tile per layer over a checkerboard, for human review."""
    GALLERY.mkdir(parents=True, exist_ok=True)
    d = OUT / f"slide_{slide_num:02d}"
    tiles = []
    tw, th = 460, 300
    for layer in layers:
        rgba = np.array(Image.open(d / layer["name"]).convert("RGBA"))
        h, w = rgba.shape[:2]
        a = rgba[:, :, 3:4].astype(np.float32) / 255
        comp = (checkerboard(h, w) * (1 - a) + rgba[:, :, :3] * a).astype(np.uint8)
        s = min((tw - 16) / w, (th - 58) / h, 1.0)
        comp = cv2.resize(comp, (max(1, int(w * s)), max(1, int(h * s))))
        tile = np.full((th, tw, 3), 255, np.uint8)
        oy = 46 + (th - 58 - comp.shape[0]) // 2
        ox = 8 + (tw - 16 - comp.shape[1]) // 2
        tile[oy : oy + comp.shape[0], ox : ox + comp.shape[1]] = comp
        info = (
            f"type={layer['type']}  pos=({layer['x']},{layer['y']})  "
            f"{layer['width']}x{layer['height']}  start={layer['start']}s"
        )
        cv2.putText(tile, layer["name"], (10, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.48,
                    (20, 20, 160), 1, cv2.LINE_AA)
        cv2.putText(tile, info, (10, 38), cv2.FONT_HERSHEY_SIMPLEX, 0.42,
                    (60, 60, 60), 1, cv2.LINE_AA)
        cv2.rectangle(tile, (0, 0), (tw - 1, th - 1), (180, 180, 180), 1)
        tiles.append(tile)
    cols = 3
    while len(tiles) % cols:
        tiles.append(np.full((th, tw, 3), 255, np.uint8))
    rows = [np.hstack(tiles[i : i + cols]) for i in range(0, len(tiles), cols)]
    Image.fromarray(np.vstack(rows)).save(
        GALLERY / f"slide_{slide_num:02d}_layer_gallery.jpg", quality=92
    )


def main():
    only = [int(a) for a in sys.argv[1:]] or slide_numbers()
    if not only:
        sys.exit("no output/slide_##/original.png found -- run render_slides.py first")
    metadatas = []
    for n in only:
        meta = segment_slide(n)
        diff = verify_slide(n, meta["layers"])
        layer_gallery(n, meta["layers"])
        flag = "" if diff == 0 else f"  !! reconstruction diff {diff}px"
        print(f"slide_{n:02d}: {len(meta['layers'])} layers, duration {meta['duration']}s{flag}")
        metadatas.append(meta)
    return metadatas


if __name__ == "__main__":
    main()
