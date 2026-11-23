# SoundPilot

**SoundPilot is a tool-calling agent system for multitrack sound design** (team
sogang V2A). It turns a video into an editable multitrack sound timeline, produces
each track with a suitable audio model (or separates it from the original audio),
and mixes the result back into the video.

The product name is SoundPilot (the UI title and the Docker image). The Python
package is still `v2a_inspect` and the CLI is still `v2a`.

## How it works

1. PySceneDetect splits the video into scenes.
2. Gemini analyzes each scene.
3. SAM3 tracks the objects and produces visual events.
4. A Gemini sound-timeline agent plans the sound as source / track / event.
5. Routing assigns each track to an audio model (Generate) or to separation (Separate).
6. Per-track audio is produced and mixed into the video.

## Features

- **Generate mode** (default), per-track routing:
  - Visible, picture-timed SFX: SelVA w/ SelSync (Audyssey tools server, one stem per track).
  - Ambience and off-screen sources: ElevenLabs SFX.
  - Speech: Kokoro TTS (English only).
  - A SelSync stem that is silent inside its events falls back to ElevenLabs on a full
    run. On a per-track regenerate it is flagged instead.
- **Separate mode** (needs original audio): Gemini listens to the original audio and
  plans the sources (Audyssey planning-evidence-v3, with per-source audio validation).
  SAM Audio separates each source, using its spans as positive anchors; 8 candidates are
  reranked by CLAP and SAM Audio Judge. Sources are linked to SAM3 objects (by name,
  then by box IoU). The residual (original minus stems) can be toggled.
- **Editing**: "Edit spans" moves and resizes event boxes (the input of generation and
  separation); "Edit audio" trims and moves each track's clip without changing the
  stem; "Add track" (several spans, model SelSync / ElevenLabs / Kokoro / SAM Audio);
  "Delete track"; "Regenerate with new spans" regenerates only the edited track.
- **Analysis panel** and **project history** (Recent projects) in the UI.
- **Sharing**: Save bundle (zip, about 30-40 MB per 10 s clip) and Download video (MP4).
  Open a bundle with "Load a saved project".

Known limits: running separation replaces the generation audio (run Generate first if
you need both); Kokoro is English only; ambience may be split per scene.
UI walkthrough: [docs/usage.md](docs/usage.md) (Korean).

## Run it

### RunPod single image (main path)

One image runs the UI, the inference server and the Audyssey tools server in one pod.

- Image: `ghcr.io/gwpark0741/soundpilot:<tag>` (see [Image tags](#image-tags)).
- GPU: 80 GB, CUDA 12.8 or newer. Container disk 50 GB, network volume 100 GB or more
  at `/workspace`. Expose HTTP port `8501`.
- Environment: `V2A_INSPECT_UI_PASSWORD` and `GEMINI_API_KEY` (required),
  `HF_TOKEN` (first boot only, gated weights), `ELEVENLABS_API_KEY` (ambience SFX).

Settings, weight list and first-boot steps: [docs/runpod.md](docs/runpod.md).
Records live in `/workspace/data`; without a network volume they are lost when the pod
is terminated, so save a bundle before stopping.

### Server with Docker (sv3)

`compose.demo.yaml` runs `inference`, `tools` and `ui` as three containers on one GPU
(Hunyuan off, NVENC off, weights read-only from `HF_CACHE`):

```bash
cp .env.demo.example .env.demo   # fill V2A_GPU_DEVICE, GEMINI_API_KEY, ELEVENLABS_API_KEY, AUDYSSEY_TOOL_TOKEN
docker compose -f compose.demo.yaml --env-file .env.demo up -d
uv run python scripts/demo_preflight.py --gpu <N> --warmup <video> [<video> ...]
```

`--warmup` loads the tools models before the first real call. See
[docs/demo-runbook.md](docs/demo-runbook.md) and
[docs/demo-measurements.md](docs/demo-measurements.md).

### Local development

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
and `server/.venv` for GPU inference. Press `Ctrl+C` to stop both. It does not start
the Audyssey tools server; Generate (SelSync) and Separate need one, with
`V2A_INSPECT_AUDYSSEY_TOOLS_URL` and `V2A_INSPECT_AUDYSSEY_TOOL_TOKEN` set.

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

CLI entrypoints (the package and command names are unchanged):

- `uv run v2a run VIDEO`: analyze a video and export a `VideoAsset` JSON.
- `uv run v2a ui`: open the upload, timeline-editing, generation, and preview UI.
- `uv run v2a synthesize`: generate audio from an exported asset or timeline.
- `uv run --project server v2a-inspect-server serve`: run the inference API.

These are one-shot CLI/UI entrypoints. The sound-timeline inference stage uses
LangGraph internally; it is not an interactive CLI loop.

Plain `docker compose up --build` (`docker-compose.yaml`) starts only the UI and the
inference server, with the UI on `http://127.0.0.1:8501`. Set `V2A_DOCKER_UID`,
`V2A_DOCKER_GID` (`id -u`, `id -g`) and `V2A_GPU_DEVICE` in `.env`. It has no tools
server, so use `compose.demo.yaml` for Generate/Separate.

## Image tags

| Tag | Built | Status |
|---|---|---|
| `demo-20261123` | on sv3 | Verified end to end on a GPU. The presentation version; the safe choice until a release note says otherwise. |
| `latest` | automatically by GitHub Actions on every merge to main | Moves with main. The first auto-built image (`000a20c`) passed the same GPU end-to-end check on 2026-11-24; later merges are only build-checked, not GPU-checked. |
| `<commit sha>` | automatically, same build as `latest` for that commit | Pin one change when you want to try it on RunPod. |

The tools part of every image is the pinned `ghcr.io/gwpark0741/soundpilot-tools:demo-1117`.

## Development workflow

`main` is protected (ruleset "main protection"): changes go in through a pull request,
the four checks below must pass, force pushes and deletion are blocked, and no
approval is required.

1. Branch from `main` (`git switch -c feat/<topic>`).
2. Commit and push the branch, then open a PR to `main`.
3. `ci-lint` runs on the PR:
   - `ruff check`, `ruff format --check` and `ty check` on `src/` and `server/src/`
   - backend `pytest` (with ffmpeg)
   - web `npm test` and `npm run build` (Node 22)
   - a build check of the UI and server images (no push)
4. When the checks pass, merge the PR.
5. A merge that changes code or Docker files (`src/`, `server/`, `web/`,
   `scripts/runpod/`, `pyproject.toml`, `uv.lock`, `Dockerfile`, `Dockerfile.runpod`,
   `.dockerignore`, the publish workflow) triggers Publish Image. It builds the UI and
   server images from the repo, combines them with the tools image through
   `Dockerfile.runpod`, and pushes `soundpilot:latest` and `soundpilot:<sha>`
   (about 25 minutes). Docs-only merges build nothing.

To change the tools version, verify the new Audyssey tools image end to end on a GPU,
then change `TOOLS_IMAGE` in `.github/workflows/publish-image.yaml`.

Step-by-step guide for teammates (Korean): [docs/contributing.md](docs/contributing.md).

## Tests

```bash
uv run --extra ui --with pytest --with pytest-asyncio python -m pytest -q tests   # 208 passed
(cd web && npm test && npm run build)                                             # 21 passed, Node 22
uv run --project server python -m unittest discover server/tests
```

Tests must not depend on `.env`: CI has no API keys.

## Prerequisites

- Linux with an NVIDIA GPU for the inference server (SAM3) and the tools server.
- A Gemini API key (`GEMINI_API_KEY`) for the analysis and timeline agent.
- The Audyssey tools server and `ELEVENLABS_API_KEY` for ambience SFX.
- SAM3 is installed from `facebookresearch/sam3`; its checkpoint may need a
  Hugging Face account with access (set `HF_TOKEN`).
- Optional: Hunyuan V2A (`V2A_SERVER_HUNYUAN_ENABLED`, default true in the inference
  server; about 8 GB of weights on first use). The demo images turn it off.
- Kokoro TTS downloads its model on first use.

`V2A_INSPECT_TTS_PROVIDER=elevenlabs` selects ElevenLabs TTS instead of Kokoro; the
current ElevenLabs plan rejects it with 402 `paid_plan_required`.

## Sample data

`samples/` holds a 7-second clip and its analyzed `VideoAsset` (scenes, object
tracks, and an agent-generated sound timeline). The UI dialog takes only a bundle
zip, so load the sample through the API (`POST /api/asset/import`):

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

## Packages / layout

- `src/v2a_inspect/`: main CLI, agent pipeline, editor API, and inference clients.
- `web/`: React timeline editor.
- `server/`: GPU inference API for SAM3, Kokoro TTS, and optional Hunyuan V2A.
- `tests/` and `server/tests/`: regression tests.
- `Dockerfile`, `server/Dockerfile`: UI and inference server images.
- `Dockerfile.runpod`: combines the UI, server and tools images into the single RunPod image.
- `scripts/runpod/`: `start.sh` (pod entrypoint) and `fetch_weights.py` (weight download).
- `scripts/start-local.sh`, `scripts/demo_preflight.py`, `scripts/run.sh`: local start,
  demo preflight, and a batch wrapper around `v2a run`.
- `compose.demo.yaml`, `docker-compose.yaml`: Docker deployments.
- `.github/workflows/`: `ci-lint.yaml` (PR checks) and `publish-image.yaml` (image build on merge).
- `docs/`: [usage](docs/usage.md), [runpod](docs/runpod.md),
  [demo-runbook](docs/demo-runbook.md), [contributing](docs/contributing.md).

Runtime media, generated outputs, virtual environments, and downloaded model
weights are intentionally ignored by Git. Copy `.env.example` to `.env`; never
commit real credentials.
