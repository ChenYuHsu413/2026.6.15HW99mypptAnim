# Deploying / running on another machine

The pipeline runs as a local web server (`pipeline_server.py`). Whoever runs
that server needs three things on their machine; **the people who only open the
browser need nothing.**

1. Python packages — `requirements.txt`
2. **LibreOffice** — converts uploaded `.pptx` / `.ppt` to PDF
3. **ffmpeg** (with `ffprobe`) — audio duration + final MP4 render

PDF uploads work without LibreOffice; it's only needed for PowerPoint files.

---

## Option A — Docker (recommended: one command, nothing to install by hand)

Bundles LibreOffice + ffmpeg + CJK fonts + all Python deps into one image.

```bash
docker build -t pptx-anim .

# Persist uploaded tasks on the host by mounting the project dir:
docker run -p 9001:9001 -v "$PWD:/app" pptx-anim
#   Windows PowerShell:  docker run -p 9001:9001 -v "${PWD}:/app" pptx-anim
```

Open http://localhost:9001/pipeline-ui/

The mount (`-v`) keeps your `task=*/` folders on the host so uploads and
renders survive container restarts. Without it, they live only inside the
container.

---

## Option B — Install directly on the machine (no Docker)

```bash
pip install -r requirements.txt
```

Then install the two system binaries:

| OS | LibreOffice | ffmpeg |
|----|-------------|--------|
| Windows | https://www.libreoffice.org/download/ (installer) | `winget install Gyan.FFmpeg` or https://www.gyan.dev/ffmpeg/builds/ |
| macOS | `brew install --cask libreoffice` | `brew install ffmpeg` |
| Debian/Ubuntu | `sudo apt install libreoffice` | `sudo apt install ffmpeg` |

Windows installs LibreOffice to `C:\Program Files\LibreOffice\program\soffice.exe`,
which the converter finds automatically. If it's elsewhere, set the `SOFFICE`
env var to the full `soffice` path. Make sure `ffmpeg`/`ffprobe` are on `PATH`.

Run:

```bash
python pipeline_server.py 9001
```

---

## Note on Chinese / CJK decks

LibreOffice needs CJK fonts installed to render Chinese text into the PDF
(otherwise text becomes boxes). The Docker image installs `fonts-noto-cjk`.
On a bare-metal machine that already runs the OS in Chinese this is usually
already present; otherwise install a Noto CJK / Microsoft JhengHei font.
