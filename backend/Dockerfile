# Backend + camera-service image (same code, different command).
# Multi-arch friendly: works on amd64, arm64 (Raspberry Pi 4/5, most NAS).
FROM python:3.12-slim

# FFmpeg does the heavy lifting (re-muxing only, no transcoding of video).
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg tini \
 && rm -rf /var/lib/apt/lists/*

RUN useradd --system --uid 1000 --create-home tapo
WORKDIR /app
COPY pyproject.toml ./
COPY tapocloud ./tapocloud
RUN pip install --no-cache-dir ".[postgres]"

ENV TAPOCLOUD_DATA_DIR=/data \
    PYTHONUNBUFFERED=1
RUN mkdir -p /data && chown tapo:tapo /data
VOLUME ["/data"]
USER tapo

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)" || exit 1

ENTRYPOINT ["tini", "--"]
CMD ["python", "-m", "tapocloud.api"]
