"""OCR each rendered slide so downstream steps have real text content.

Reads  output/slide_##/original.png
Writes output/slide_##/slide_ocr.json  with:
    {
      "text": "<full slide text, reading order, newline-separated>",
      "confidence": 0.91,           # mean of line confidences
      "lines": [{"text": ..., "confidence": ..., "bbox": [x,y,w,h]}, ...]
    }

Run after render_slides.py. Idempotent: skips slides that already have
slide_ocr.json unless OCR_FORCE=1 is set in the environment.

Uses RapidOCR 3.x with the CHINESE_CHT recognition model — explicitly
trained on traditional Chinese, so output matches zh-TW characters
(狀態, 預測, 特徵...) rather than simplified or misread forms. First
run downloads the model (~10 MB); subsequent runs are CPU-only and
fast (~0.5–2 s per slide).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _bbox_xywh(poly):
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return [int(min(xs)), int(min(ys)),
            int(max(xs) - min(xs)), int(max(ys) - min(ys))]


_engine = None


def get_engine():
    """Cached RapidOCR engine (zh-TW). Lazy so callers can avoid the cost."""
    global _engine
    if _engine is None:
        from rapidocr import RapidOCR, LangRec  # noqa: WPS433
        _engine = RapidOCR(params={"Rec.lang_type": LangRec.CHINESE_CHT})
    return _engine


def ocr_image(image_path):
    """Run zh-TW OCR on any image path, return slide_ocr.json-shaped payload."""
    return ocr_slide(get_engine(), image_path)


def ocr_slide(engine, image_path):
    """Run OCR on one slide image, return the slide_ocr.json payload."""
    result = engine(str(image_path))
    polys = list(result.boxes) if result.boxes is not None else []
    texts = list(result.txts) if result.txts is not None else []
    confs = list(result.scores) if result.scores is not None else []
    if not texts:
        return {"text": "", "confidence": 0.0, "lines": []}
    lines = []
    for poly, text, conf in zip(polys, texts, confs):
        text = (text or "").strip()
        if not text:
            continue
        lines.append({
            "text": text,
            "confidence": round(float(conf), 3),
            "bbox": _bbox_xywh(poly),
        })
    # Reading order: top-to-bottom, then left-to-right within a row.
    # Row-grouping tolerance: half the median line height.
    if lines:
        heights = sorted(l["bbox"][3] for l in lines)
        tol = max(8, heights[len(heights) // 2] // 2)
        lines.sort(key=lambda l: (l["bbox"][1] // tol, l["bbox"][0]))
    full_text = "\n".join(l["text"] for l in lines)
    mean_conf = round(sum(l["confidence"] for l in lines) / len(lines), 3) if lines else 0.0
    return {"text": full_text, "confidence": mean_conf, "lines": lines}


def main():
    out_root = Path.cwd() / "output"
    if not out_root.is_dir():
        sys.exit("no output/ directory — run render_slides.py first")
    slide_dirs = sorted(out_root.glob("slide_*"))
    if not slide_dirs:
        sys.exit("no slide_##/ directories under output/")

    force = os.environ.get("OCR_FORCE") == "1"
    # local import keeps script importable without the dep installed
    from rapidocr import RapidOCR, LangRec
    engine = RapidOCR(params={"Rec.lang_type": LangRec.CHINESE_CHT})

    done, skipped = 0, 0
    for sd in slide_dirs:
        out_path = sd / "slide_ocr.json"
        if out_path.exists() and not force:
            skipped += 1
            continue
        img = sd / "original.png"
        if not img.exists():
            print(f"  {sd.name}: missing original.png — skipped")
            continue
        payload = ocr_slide(engine, img)
        out_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        done += 1
        print(f"  {sd.name}: {len(payload['lines'])} lines, conf={payload['confidence']}")

    print(f"OCR done: {done} written, {skipped} cached (set OCR_FORCE=1 to redo)")


if __name__ == "__main__":
    main()
