# syntax=docker/dockerfile:1

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_HTTP_TIMEOUT=600 \
    UV_LINK_MODE=copy \
    GIT_HTTP_VERSION=HTTP/1.1 \
    PATH="/app/server/.venv/bin:$PATH" \
    V2A_SERVER_HOST=0.0.0.0 \
    V2A_SERVER_PORT=8080 \
    V2A_SERVER_UPLOAD_DIR=/data/uploads \
    V2A_SERVER_HUNYUAN_MODEL_PATH=/data/model-cache/hunyuan \
    HF_HOME=/data/model-cache/huggingface \
    TRANSFORMERS_CACHE=/data/model-cache/transformers \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility,video

WORKDIR /app/server


FROM base AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    git \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

RUN git config --global http.version HTTP/1.1

COPY server/pyproject.toml server/uv.lock server/README.md ./

RUN --mount=type=cache,target=/root/.cache/uv \
    for attempt in 1 2 3; do \
      uv sync --locked --no-dev --no-install-project && break; \
      if [ "$attempt" = "3" ]; then exit 1; fi; \
      sleep 10; \
    done

COPY server/src ./src

# uv_cache.json records the src directory's mtime, which would make the whole venv layer differ
# on every source change; only uv reads it, so drop it (and its RECORD line) from the image.
RUN --mount=type=cache,target=/root/.cache/uv \
    for attempt in 1 2 3; do \
      uv sync --locked --no-dev && break; \
      if [ "$attempt" = "3" ]; then exit 1; fi; \
      sleep 10; \
    done \
    && dist=$(echo .venv/lib/python3*/site-packages/v2a_inspect_server-*.dist-info) \
    && rm "$dist/uv_cache.json" && sed -i '/uv_cache\.json/d' "$dist/RECORD"


FROM base AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    espeak-ng \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

ARG APP_UID=1000
ARG APP_GID=1000

RUN groupadd --gid "${APP_GID}" appuser \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --create-home --shell /bin/bash appuser \
    && mkdir -p /data \
    && chown appuser:appuser /data

# The venv (torch, CUDA wheels; about 11 GB) is its own layer and depends only on the lock file,
# so a change in server/src reuses it instead of re-exporting and re-uploading it.
COPY --link --from=builder /app/server/.venv /app/server/.venv
COPY --link --from=builder /app/server/src /app/server/src
COPY --link --from=builder /app/server/pyproject.toml /app/server/uv.lock /app/server/README.md /app/server/

ADD --checksum=sha256:80461bd19c1f7bc1213445eba48aa69554e4b0230c57d3acc80625d2e2519dce --chown=appuser:appuser --chmod=0644 \
    https://raw.githubusercontent.com/facebookresearch/Motionformer/bf43d50/configs/SSV2/divided_224_16x4.yaml \
    /app/server/.venv/lib/python3.12/site-packages/hunyuanvideo_foley/models/synchformer/divided_224_16x4.yaml

EXPOSE 8080
VOLUME ["/data"]

USER appuser

CMD ["v2a-inspect-server", "serve"]
