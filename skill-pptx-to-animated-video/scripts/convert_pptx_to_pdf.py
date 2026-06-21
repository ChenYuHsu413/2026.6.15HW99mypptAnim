"""Convert .pptx / .ppt to .pdf via LibreOffice headless mode.

Usage:
    python convert_pptx_to_pdf.py path/to/deck.pptx [out_dir]

`render_slides.py` only consumes PDFs by design (keeps the core single-format).
This helper sits one layer above it, so scope-2 PPTX input is handled before
the engine ever runs.

Requires LibreOffice (`soffice`). On Windows the default install lives at
`C:\\Program Files\\LibreOffice\\program\\soffice.exe`. Override the location
with the SOFFICE env var if needed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

_WIN_DEFAULTS = [
    Path(r"C:\Program Files\LibreOffice\program\soffice.exe"),
    Path(r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"),
]


def find_soffice() -> Path | None:
    """Locate the LibreOffice executable.

    Order: SOFFICE env var → PATH (`soffice`) → known Windows install dirs.
    """
    env = os.environ.get("SOFFICE")
    if env:
        p = Path(env)
        if p.exists():
            return p
    found = shutil.which("soffice") or shutil.which("soffice.exe")
    if found:
        return Path(found)
    for cand in _WIN_DEFAULTS:
        if cand.exists():
            return cand
    return None


def convert_to_pdf(src, out_dir=None) -> Path:
    """Convert a .pptx/.ppt file to PDF, writing it into `out_dir`.

    Returns the path to the produced PDF. Raises with a clear message on
    missing soffice or conversion failure.
    """
    src = Path(src).resolve()
    if not src.exists():
        raise FileNotFoundError(f"source not found: {src}")
    if src.suffix.lower() not in {".pptx", ".ppt"}:
        raise ValueError(
            f"unsupported source extension {src.suffix!r} (need .pptx or .ppt)"
        )
    out_dir = Path(out_dir or src.parent).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    soffice = find_soffice()
    if soffice is None:
        raise RuntimeError(
            "LibreOffice 'soffice' not found. Install LibreOffice or set the "
            "SOFFICE env var to the soffice executable path."
        )

    r = subprocess.run(
        [
            str(soffice), "--headless", "--convert-to", "pdf",
            "--outdir", str(out_dir), str(src),
        ],
        capture_output=True, text=True, timeout=300,
    )
    pdf_path = out_dir / (src.stem + ".pdf")
    if r.returncode or not pdf_path.exists():
        raise RuntimeError(
            f"soffice conversion failed (rc={r.returncode})\n"
            f"stdout: {r.stdout.strip()}\nstderr: {r.stderr.strip()}"
        )
    return pdf_path


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    src = Path(sys.argv[1])
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    pdf = convert_to_pdf(src, out_dir)
    print(f"converted: {pdf}")


if __name__ == "__main__":
    main()
