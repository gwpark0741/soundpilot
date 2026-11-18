# V2A Inspect

V2A Inspect turns a video into an editable sound timeline (SAM3 object tracking
plus a Gemini sound-timeline agent), routes each track to an audio model, and
mixes the result back into the video.

Top-level audio mode:

- **Generate** (default): per-track routing.
  - Video-synced SFX: SelVA w/ SelSync (Audyssey tools server, one stem per track).
  - Ambience/background and off-screen sources: ElevenLabs SFX (t2a).
  - Speech: ElevenLabs TTS (Kokoro via `V2A_INSPECT_TTS_PROVIDER=kokoro`). On hold:
    the current ElevenLabs plan returns 402 `paid_plan_required` for the default voice.
- **Separate** (needs original audio): SAM Audio separation via the Audyssey tools
  server. The residual (original minus separated stems) can be toggled.

Any track can be regenerated (generate or separate) with a new seed; on failure the
previous audio is kept. Details: [docs/usage.md](docs/usage.md) (Korean).

## Entrypoints

- `uv run v2a run VIDEO`: analyze a video and export a `VideoAsset` JSON.
- `uv run v2a ui`: open the upload, timeline-editing, generation, and preview UI.
- `uv run v2a synthesize`: generate audio from an exported asset or timeline.
- `uv run --project server v2a-inspect-server serve`: run the inference API.
- Audyssey tools server (separate image `audyssey-tools:demo-1117`): separation and
  SelVA w/ SelSync generation. Set `V2A_INSPECT_AUDYSSEY_TOOLS_URL` and
  `V2A_INSPECT_AUDYSSEY_TOOL_TOKEN`; Generate/Separate fail without it.

The commands are one-shot CLI/UI entrypoints. The sound-timeline inference stage
uses LangGraph internally; it is not an interactive CLI loop.

## Local setup

```bash
cp .env.example .env
uv sync --extra ui
uv sync --project server
(cd web && npm ci && npm run build)
```

The UI serves the built frontend from `web/dist`, so the build step is required
before `v2a ui` shows anything. Use Node 20.19+ or 22.12+ (Vite 7).

Add a Gemini API key to `.env`, then start the inference server and UI in
the repository-owned virtual environments with one command:

```bash
./scripts/start-local.sh
```

This runs both processes as the invoking host user, using `.venv` for the UI
and `server/.venv` for GPU inference. Press `Ctrl+C` to stop both. Docker
containers use image-local environments instead of these host environments.

Open `http://127.0.0.1:8501` (password login if `V2A_INSPECT_UI_PASSWORD` is set).
The UI flow is:

1. **Open video**: "Analyze a new video" (runs SAM3 + agent) or "Load a saved project"
   (a bundle zip).
2. Choose **Generate** or **Separate** in the top bar, then press **Generate audio** /
   **Separate audio**.
3. Timeline lanes are the track list: select a lane for the selected-track panel
   (Description, Regenerate / Re-separate / Generate instead, residual switch), use
   Play and Solo per lane, and edit events.
4. Preview the mixed video; Open video > Advanced has **Download video (MP4)**.
   **Save bundle** exports the whole project as a zip for **Load a saved project**.

Switching the mode only changes the stored mode; tracks are re-routed on the next
main-button run. Kokoro downloads its model on first use when selected.

## Sample data

`samples/` holds a 7-second clip and its analyzed `VideoAsset` (scenes, object
tracks, and an agent-generated sound timeline). The UI dialog takes only a bundle
zip, so load the sample through the API (`POST /api/asset/import` still exists):

```bash
curl -F video=@samples/sample.mp4 -F asset=@samples/sample.video-asset.json \
  http://127.0.0.1:8501/api/asset/import
```

If `V2A_INSPECT_UI_PASSWORD` is set, add `-H "Authorization: Bearer $V2A_INSPECT_UI_PASSWORD"`.
Keyframe images and the SAM3 overlay are not included, so those previews stay empty
until you run the analysis yourself. Generate/Separate need the tools server (and the
inference server for analysis).

## Frontend development

Run the UI backend and the Vite dev server separately:

```bash
uv run v2a ui                  # terminal 1, port 8501
cd web && npm ci && npm run dev   # terminal 2, proxies /api and /events to 8501
```

## Tests

```bash
uv run --extra ui --with pytest python -m pytest tests   # 128 passed here
uv run --project server python -m unittest discover server/tests
(cd web && npm test)                                     # needs Node 22
```

`tests/test_bundle.py` and `tests/test_demo_preflight.py` import `pytest`, so plain
`unittest discover tests` reports two import errors in the project venv.

## Prerequisites

- Linux with an NVIDIA GPU for the inference server (SAM3) and the tools server.
- A Gemini API key (`GEMINI_API_KEY`) for the analysis and timeline agent.
- The Audyssey tools server (see Entrypoints) and `ELEVENLABS_API_KEY` for t2a/TTS.
- Optional: Hunyuan V2A (`V2A_SERVER_HUNYUAN_ENABLED`, default true; weights about 8 GB
  download on first use) and Kokoro TTS (downloads on first use).
- SAM3 is installed from `facebookresearch/sam3`; its checkpoint may need a
  Hugging Face account with access (set `HF_TOKEN` in `.env`).

## Docker Compose

```bash
cp .env.example .env
docker compose up --build
```

This starts both services with the UI available only on
`http://127.0.0.1:8501`. The inference API is reachable only by the UI on the
private Compose network. Set `V2A_DOCKER_UID` and `V2A_DOCKER_GID` in `.env`
to `id -u` and `id -g`; the inference process then runs as that host user.
Set `V2A_GPU_DEVICE` to the physical GPU index assigned to the inference
container. Only that GPU is exposed, and all CUDA models use it as `cuda:0`.
Model files remain in the `v2a_server_data` volume across container restarts.
This file starts only the UI and inference server; the Audyssey tools server is
not included, so use the demo compose below for Generate/Separate.

## Demo deployment

`compose.demo.yaml` runs `inference`, `tools` and `ui` on one GPU (Hunyuan off,
NVENC off, weights read-only from `HF_CACHE`):

```bash
cp .env.demo.example .env.demo   # fill V2A_GPU_DEVICE, GEMINI_API_KEY, ELEVENLABS_API_KEY, AUDYSSEY_TOOL_TOKEN
docker compose -f compose.demo.yaml --env-file .env.demo up -d
uv run python scripts/demo_preflight.py --gpu <N> --warmup <video> [<video> ...]
```

`--warmup` loads the tools models before the first real call. See
[docs/demo-runbook.md](docs/demo-runbook.md) and
[docs/demo-measurements.md](docs/demo-measurements.md).

## Packages

- `src/v2a_inspect/`: main CLI, agent pipeline, editor API, and inference clients.
- `web/`: React timeline editor.
- `server/`: GPU inference API for SAM3, optional Hunyuan V2A, and Kokoro TTS.
- `tests/` and `server/tests/`: lightweight regression tests.
- `scripts/run.sh`: batch wrapper around `v2a run`.

Runtime media, generated outputs, virtual environments, and downloaded model
weights are intentionally ignored by Git. Copy `.env.example` to `.env`; never
commit real credentials.
