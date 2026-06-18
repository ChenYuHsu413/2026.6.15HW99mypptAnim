"""Render a slide deck PDF to output/slide_##/original.png at 1920x1080.

Usage: python render_slides.py path/to/deck.pdf

If you only have a PPTX, convert it first (PowerPoint export, or
`soffice --headless --convert-to pdf deck.pptx`).
"""

import sys
from pathlib import Path

import fitz  # pymupdf
import numpy as np
from PIL import Image

import config

WIDTH, HEIGHT = config.PROJECT["canvas"]["width"], config.PROJECT["canvas"]["height"]


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    src = Path(sys.argv[1])
    if src.suffix.lower() != ".pdf":
        sys.exit("expected a PDF (export the PPTX to PDF first)")
    out_root = Path.cwd() / "output"
    doc = fitz.open(src)
    for idx, page in enumerate(doc, 1):
        scale = min(WIDTH / page.rect.width, HEIGHT / page.rect.height)
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        canvas = Image.new("RGB", (WIDTH, HEIGHT), "white")
        canvas.paste(img, ((WIDTH - img.width) // 2, (HEIGHT - img.height) // 2))
        slide_dir = out_root / f"slide_{idx:02d}"
        slide_dir.mkdir(parents=True, exist_ok=True)
        canvas.save(slide_dir / "original.png")
    print(f"rendered {doc.page_count} slides to {out_root}")


if __name__ == "__main__":
    main()
