#!/usr/bin/env bash
# One-pod entrypoint: inference server (:8080), Audyssey tools server (:8700) and, optionally, UI (:8501).
# All three listen on 0.0.0.0. One bearer token (SOUNDPILOT_TOKEN, else AUDYSSEY_TOOL_TOKEN, else a
# generated one printed in the log) protects both GPU servers. The UI starts only when
# V2A_INSPECT_UI_PASSWORD is set; otherwise this is a GPU-only pod for a UI running elsewhere.
# Exits non-zero if any service dies.
set -euo pipefail

if [ -n "${V2A_INSPECT_UI_PASSWORD:-}" ]; then
  : "${GEMINI_API_KEY:?set GEMINI_API_KEY}"
  RUN_UI=1
else
  RUN_UI=0
  echo "[start] V2A_INSPECT_UI_PASSWORD is not set: starting the GPU servers only (no UI)"
fi

PY=/app/server/.venv/bin/python
export HF_HOME="${HF_HOME:-/workspace/hf}"
DATA="${DATA_DIR:-/workspace/data}"
mkdir -p "$HF_HOME" "$DATA/uploads" "$DATA/work"

# Weights: download only what is missing (needs HF_TOKEN); afterwards everything runs offline.
if ! "$PY" /app/runpod/fetch_weights.py --check; then
  [ -n "${HF_TOKEN:-}" ] || { echo "[start] weights missing and HF_TOKEN is not set" >&2; exit 1; }
  "$PY" /app/runpod/fetch_weights.py
fi
export HF_HUB_OFFLINE=1
unset HF_TOKEN  # keep it out of the three servers' environments
# The server base image sets TRANSFORMERS_CACHE=/data/model-cache/transformers; it would
# override HF_HOME for transformers (flan-t5 for SelSync and SAM Audio), so drop it.
unset TRANSFORMERS_CACHE

TOKEN_GENERATED=0
if [ -n "${SOUNDPILOT_TOKEN:-}" ]; then
  TOKEN="$SOUNDPILOT_TOKEN"
elif [ -n "${AUDYSSEY_TOOL_TOKEN:-}" ]; then
  TOKEN="$AUDYSSEY_TOOL_TOKEN"
else
  TOKEN="$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  TOKEN_GENERATED=1
fi
pids=()
svc() { # svc NAME CMD...: run in the background, every output line prefixed with [NAME]
  local name=$1; shift
  "$@" > >(sed -u "s/^/[$name] /") 2>&1 &
  pids+=($!)
}
trap 'kill "${pids[@]}" 2>/dev/null || true' EXIT
ready() { # ready URL SECONDS: poll until HTTP 200, give up early if a service died
  local i
  for ((i = 0; i < $2; i += 2)); do
    "$PY" -c "import urllib.request,sys; urllib.request.urlopen(sys.argv[1], timeout=2)" "$1" 2>/dev/null && return 0
    for p in "${pids[@]}"; do # `kill -0 a b` succeeds if any one pid is alive, so test each
      kill -0 "$p" 2>/dev/null || { echo "[start] a service exited while waiting for $1" >&2; return 1; }
    done
    sleep 2
  done
  echo "[start] timeout waiting for $1" >&2; return 1
}

svc inference env -C /app/server \
  V2A_SERVER_HOST=0.0.0.0 V2A_SERVER_PORT=8080 V2A_SERVER_API_TOKEN="$TOKEN" V2A_SERVER_UPLOAD_DIR="$DATA/uploads" \
  V2A_SERVER_HUNYUAN_ENABLED=false V2A_SERVER_KOKORO_DEVICE="${V2A_SERVER_KOKORO_DEVICE:-cpu}" \
  /app/server/.venv/bin/v2a-inspect-server serve

svc tools env -C /app \
  AUDYSSEY_TOOL_TOKEN="$TOKEN" \
  AUDYSSEY_SAM_AUDIO_RERANKING_CANDIDATES="${AUDYSSEY_SAM_AUDIO_RERANKING_CANDIDATES:-8}" \
  PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
  LD_LIBRARY_PATH=/app/gpu/.venv/lib/python3.12/site-packages/nvidia/npp/lib \
  /app/gpu/.venv/bin/audyssey tool-server --tool separation --tool generation \
  --impl real --host 0.0.0.0 --port 8700

ready http://127.0.0.1:8080/healthz 900
ready http://127.0.0.1:8700/health 900

if [ -n "${RUNPOD_POD_ID:-}" ]; then
  INFERENCE_URL="https://${RUNPOD_POD_ID}-8080.proxy.runpod.net"
  TOOLS_URL="https://${RUNPOD_POD_ID}-8700.proxy.runpod.net"
else
  HOST_ADDR="$(hostname -I 2>/dev/null | cut -d' ' -f1 || true)"
  INFERENCE_URL="http://${HOST_ADDR:-this-host}:8080"
  TOOLS_URL="http://${HOST_ADDR:-this-host}:8700"
fi
echo "[start] Connect a local UI with: Open video > Advanced > GPU servers"
echo "[start]   Inference server URL: $INFERENCE_URL"
echo "[start]   Audyssey tools URL:   $TOOLS_URL"
if [ "$TOKEN_GENERATED" = 1 ]; then
  echo "[start]   Token: $TOKEN"
else
  echo "[start]   Token: the value you set in SOUNDPILOT_TOKEN (or AUDYSSEY_TOOL_TOKEN)"
fi

if [ "$RUN_UI" = 1 ]; then
# UI. Defaults below are the v2a-inspect UI image's; the demo overrides follow compose.demo.yaml.
svc ui env \
  V2A_INSPECT_UI_HOST=0.0.0.0 V2A_INSPECT_UI_PORT=8501 \
  V2A_INSPECT_UI_WORK_DIR="$DATA/work" V2A_INSPECT_UI_STATIC_DIR=/app/web/dist \
  V2A_INSPECT_UI_SERVER_URL=http://127.0.0.1:8080 \
  V2A_INSPECT_CLIENT_SERVER_HOST=127.0.0.1 V2A_INSPECT_CLIENT_SERVER_PORT=8080 \
  V2A_INSPECT_CLIENT_TIMEOUT="${V2A_INSPECT_CLIENT_TIMEOUT:-300}" \
  V2A_INSPECT_AUDYSSEY_TOOLS_URL=http://127.0.0.1:8700 V2A_INSPECT_AUDYSSEY_TOOL_TOKEN="$TOKEN" V2A_INSPECT_CLIENT_SERVER_TOKEN="$TOKEN" \
  V2A_INSPECT_VIDEO_ENCODE_USE_NVENC=false \
  V2A_INSPECT_UI_SCENE_THRESHOLD=27.0 V2A_INSPECT_UI_MAX_KEYFRAMES_PER_SCENE=20 \
  V2A_INSPECT_LLM_SMALL_MODEL="${V2A_INSPECT_LLM_SMALL_MODEL:-gemini-3.1-flash-lite}" \
  V2A_INSPECT_LLM_MEDIUM_MODEL="${V2A_INSPECT_LLM_MEDIUM_MODEL:-gemini-3.5-flash}" \
  V2A_INSPECT_LLM_LARGE_MODEL="${V2A_INSPECT_LLM_LARGE_MODEL:-gemini-4-hydrogen}" \
  V2A_INSPECT_LLM_THINKING_LEVEL="${V2A_INSPECT_LLM_THINKING_LEVEL:-low}" \
  V2A_INSPECT_LLM_SMALL_THINKING_LEVEL="${V2A_INSPECT_LLM_SMALL_THINKING_LEVEL:-medium}" \
  V2A_INSPECT_LLM_MEDIUM_THINKING_LEVEL="${V2A_INSPECT_LLM_MEDIUM_THINKING_LEVEL:-low}" \
  V2A_INSPECT_LLM_LARGE_THINKING_LEVEL="${V2A_INSPECT_LLM_LARGE_THINKING_LEVEL:-low}" \
  V2A_INSPECT_LLM_TEMPERATURE="${V2A_INSPECT_LLM_TEMPERATURE:-1}" \
  V2A_INSPECT_LLM_TIMEOUT_SECONDS="${V2A_INSPECT_LLM_TIMEOUT_SECONDS:-600}" \
  V2A_INSPECT_LLM_MAX_RETRIES="${V2A_INSPECT_LLM_MAX_RETRIES:-5}" \
  V2A_INSPECT_AGENT_SOUND_TIMELINE_RECURSION_LIMIT=500 V2A_INSPECT_AGENT_SOUND_TIMELINE_MAX_WORKERS=3 \
  V2A_INSPECT_AGENT_SOUND_TIMELINE_SEGMENT_SECONDS=30 \
  V2A_INSPECT_SELSYNC_SEED="${V2A_INSPECT_SELSYNC_SEED:-42}" \
  V2A_INSPECT_TRACK_DESCRIPTION_DEFAULT="${V2A_INSPECT_TRACK_DESCRIPTION_DEFAULT:-label}" \
  V2A_INSPECT_TTS_PROVIDER="${V2A_INSPECT_TTS_PROVIDER:-kokoro}" \
  /opt/venv/bin/v2a ui
ready http://127.0.0.1:8501/healthz 60
echo "[start] all services are up: inference :8080, tools :8700, UI :8501"
else
  echo "[start] all services are up: inference :8080, tools :8700 (no UI)"
fi

# wait -n returns when the first service exits; treat any exit as failure.
wait -n "${pids[@]}" || true
echo "[start] a service exited; stopping" >&2
exit 1
