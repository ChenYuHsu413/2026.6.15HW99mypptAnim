FROM python:3.12-slim

# System binaries the pipeline shells out to (cannot come from pip):
#   libreoffice          PPTX/PPT -> PDF on upload (/ingest)
#   ffmpeg / ffprobe     audio duration + final MP4 render
#   fonts-noto-cjk       so LibreOffice renders Chinese/CJK decks correctly
#   libgl1, libglib2.0-0 runtime libs needed by opencv-python
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice \
        ffmpeg \
        fonts-noto-cjk \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 9001
# 0.0.0.0 so the port is reachable from the host; "share" acknowledges the
# server has no auth (the container/host firewall is the boundary).
CMD ["python", "pipeline_server.py", "9001", "0.0.0.0", "share"]
