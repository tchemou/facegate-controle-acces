# syntax=docker/dockerfile:1
# NOTE: written and reviewed but NOT built on the development machine (Docker was not available there).
# Build it once and run the checks in docs/DEPLOIEMENT.md before relying on it.

FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PYTHONPATH=/app/src
WORKDIR /app
RUN useradd --system --uid 10001 --home-dir /nonexistent --shell /usr/sbin/nologin facegate \
    && mkdir /data && chown facegate /data


# --- administration web app: no dlib, no camera ----------------------------------------------------
FROM base AS web
COPY requirements-web.txt .
RUN pip install -r requirements-web.txt
COPY src ./src
USER facegate
ENV FACEGATE_DB=/data/facegate.db FACEGATE_HOST=0.0.0.0 FACEGATE_PORT=8000 FACEGATE_LIVE_DIR=/data/live
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('FACEGATE_PORT', '8000'), timeout=4)"
CMD ["python", "-m", "facerec.web"]


# --- recognition service: owns the camera (Linux: pass --device /dev/video0) ------------------------
FROM base AS service
RUN apt-get update && apt-get install -y --no-install-recommends libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements-web.txt .
RUN pip install -r requirements-web.txt \
    && pip install "dlib-bin>=19.24" "face-recognition-models>=0.3.0" "pillow>=10"
COPY src ./src
COPY scripts/download_models.py ./scripts/download_models.py
# OpenCV Zoo detector and recogniser, verified by SHA-256 by the script. No `|| true`: a failed download must fail the build.
RUN python scripts/download_models.py
USER facegate
ENV FACEGATE_DB=/data/facegate.db
VOLUME /data
# the key comes from FACEGATE_KEY or a mounted secret (--key-file); the window is off: no display in a container
CMD ["python", "-m", "facerec", "run", "--db", "/data/facegate.db", "--live-dir", "/data/live", "--no-window", "--challenge"]
