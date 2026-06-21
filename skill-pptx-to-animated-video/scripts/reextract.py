"""Re-extract a single layer's transparent PNG from original + background.

When a user drags a layer's bbox in the UI, the existing layer PNG no longer
matches the new region. This helper crops a fresh transparent PNG for the
new bbox so the rendered layer stays visually correct.

Output goes to  output/slide_##/edits/<layer_name>  — a sibling location so
the generated artifacts (output/slide_##/<layer_name>) stay pristine. The
composition builder prefers the edited version when present.

CLI: python reextract.py <slide_dir> <layer_name> <x> <y> <w> <h>
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

# Pixels whose diff vs. background exceeds this magnitude become foreground.
# Calibrated so anti-aliased edges feather instead of being binary-cut.
DIFF_FLOOR = 6      # noise floor
DIFF_CEIL = 60      # at/above this magnitude → full opacity


def reextract(slide_dir: Path, layer_name: str, bbox) -> Path:
    """Cut a fresh transparent PNG for `layer_name` at `bbox` (x,y,w,h)."""
    slide_dir = Path(slide_dir)
    orig_path = slide_dir / "original.png"
    bg_path = slide_dir / "background.png"
    if not orig_path.exists() or not bg_path.exists():
        raise FileNotFoundError(f"missing original.png or background.png in {slide_dir}")
    x, y, w, h = (int(v) for v in bbox)
    if w <= 0 or h <= 0:
        raise ValueError(f"degenerate bbox {bbox!r}")

    orig = np.asarray(Image.open(orig_path).convert("RGB"), dtype=np.int16)
    bg = np.asarray(Image.open(bg_path).convert("RGB"), dtype=np.int16)
    H, W = orig.shape[:2]
    # Clamp bbox into the canvas.
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(W, x + w), min(H, y + h)
    if x1 <= x0 or y1 <= y0:
        raise ValueError(f"bbox {bbox!r} falls entirely outside the {W}x{H} canvas")

    orig_crop = orig[y0:y1, x0:x1]
    bg_crop = bg[y0:y1, x0:x1]
    diff = np.abs(orig_crop - bg_crop).sum(axis=2)  # 0..765
    # Feather: floor→0, ceil→255, linear between.
    alpha = np.clip((diff - DIFF_FLOOR) * 255 / (DIFF_CEIL - DIFF_FLOOR), 0, 255)
    rgba = np.dstack([orig_crop.astype(np.uint8), alpha.astype(np.uint8)])

    # The PNG itself must have the SAME size as the bbox (W=w, H=h) so the
    # renderer's pixel positioning matches. If the bbox clipped to the canvas,
    # pad with transparency.
    out = np.zeros((h, w, 4), dtype=np.uint8)
    out[y0 - y:y0 - y + (y1 - y0), x0 - x:x0 - x + (x1 - x0)] = rgba

    out_path = slide_dir / "edits" / layer_name
    out_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(out, "RGBA").save(out_path)
    return out_path


def main():
    if len(sys.argv) != 7:
        sys.exit(__doc__)
    slide_dir = Path(sys.argv[1])
    layer_name = sys.argv[2]
    bbox = tuple(int(v) for v in sys.argv[3:7])
    out = reextract(slide_dir, layer_name, bbox)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
