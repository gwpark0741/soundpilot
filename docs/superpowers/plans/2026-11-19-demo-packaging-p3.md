# Demo Packaging (P3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On sv3-a100, a clean clone at tag `demo-20261119` runs the full demo (UI + v2a inference server + Audyssey tools) on one GPU via one compose command, passes a preflight script, and can save and reload each demo video as a self-contained bundle. The RunPod backup gets the code it needs (server token, image export).

**Architecture:** Three containers. The two v2a images are built from the existing `Dockerfile` and `server/Dockerfile`. The Audyssey tools container reuses the local image `audyssey-tools:demo-1117` unchanged. A new `compose.demo.yaml` puts all three on one GPU (`V2A_GPU_DEVICE`) and mounts `/home/common/huggingface` read-only as `HF_HOME` with `HF_HUB_OFFLINE=1`. The preflight script talks only to HTTP endpoints, `docker` and `nvidia-smi`. The bundle is a zip of the asset's `v2a-inspect-ui` work root plus the asset JSON; on import the old root prefix in the JSON is replaced with the new one.

**Tech Stack:** Docker Compose v2 (NVIDIA runtime), FastAPI, httpx, React 19, stdlib `zipfile`.

**Spec:** `docs/superpowers/specs/2026-11-18-demo-upgrade-design.md` §5.5, §6 (P3 row).

## Global Constraints

- Audyssey tools image: `audyssey-tools:demo-1117`, image ID `sha256:cc603e8ffc46c26da15aac051996271db8c06bdffe1134c1bdf7b6291d08694a`. Do not rebuild it or modify `~/audyssey`.
- Tools command: `audyssey tool-server --tool separation --tool generation --impl real --host 0.0.0.0 --port 8700`, env `HF_HOME=/hf`, `HF_HUB_OFFLINE=1`, `AUDYSSEY_TOOL_TOKEN` required.
- Weights come only from `/home/common/huggingface` (mounted read-only). Never copy them. Pinned snapshots: `facebook/sam3.1` `daa63191845a41281374e725f4c9e51c7a824460`, `sogang-audyssey/internal-model` `2b5bb25e4212cd1bb9eef46ff2c344cdfa8ef1b8`, `hexgrad/Kokoro-82M` `f3ff3571791e39611d31c381e3a41a3af07b4987`.
- Inference server: `V2A_SERVER_HUNYUAN_ENABLED=false` for the demo.
- LLM: `V2A_INSPECT_LLM_LARGE_MODEL=gemini-4-hydrogen` (fallback `gemini-3.5-flash`), `V2A_INSPECT_SELSYNC_SEED=42`, `V2A_INSPECT_TRACK_DESCRIPTION_DEFAULT=label`.
- UI port binds `127.0.0.1` only. Tools and inference ports are not published on the host.
- GPU: one GPU with no other users' processes, ≤70 GB used. Running anything on a GPU, or calling Gemini/ElevenLabs, needs the user's approval first.
- Never print values from `.env` / `.env.demo`.
- Commit on `feat/demo-upgrade`; push only when the user asks.

## Review Focus

1. Tools container not ready yet when the UI's first separate/generate call arrives (cold load 157 s) → the UI shows the existing "failed. Previous audio kept." copy, and preflight reports tools as not ready instead of passing.
2. `.env.demo` missing a required key (`GEMINI_API_KEY`, `AUDYSSEY_TOOL_TOKEN`) → `docker compose config` fails with a message naming the key, not a container that starts and fails later.
3. Bundle imported into a different work dir on a different machine → every stem, residual and preview path resolves under the new root and plays; nothing points at the old root.
4. Bundle zip containing `..` or absolute member names → import rejects it with 400 (trust boundary).
5. Inference server token set but the UI not sending it → the UI shows an auth failure, not a hang.

---

### Task 1: Demo compose file and env template

**Files:**
- Create: `compose.demo.yaml`
- Create: `.env.demo.example`
- Modify: `.gitignore` (add `.env.demo`)

**Interfaces:**
- Produces: services `ui` (image `v2a-inspect:demo`), `inference` (image `v2a-inspect-server:demo`), `tools` (image `audyssey-tools:demo-1117`). UI reaches `http://inference:8080` and `http://tools:8700`. Run: `docker compose -f compose.demo.yaml --env-file .env.demo up -d`.

- [ ] **Step 1:** Write `compose.demo.yaml` from `docker-compose.yaml`, with these changes:
  - `inference`: image `v2a-inspect-server:demo`; `V2A_SERVER_HUNYUAN_ENABLED: "false"`; `HF_HOME: /hf`, `HF_HUB_OFFLINE: "1"`; volume `${HF_CACHE:-/home/common/huggingface}:/hf:ro` plus a named volume for `/data/uploads`; no host port.
  - `tools`: image `audyssey-tools:demo-1117`, the command from Global Constraints, `HF_HOME`/`HF_HUB_OFFLINE` as above, `AUDYSSEY_TOOL_TOKEN: ${AUDYSSEY_TOOL_TOKEN:?set AUDYSSEY_TOOL_TOKEN in .env.demo}`, same HF mount, healthcheck on `/health` (unauthenticated; use `python -c urllib...` as the existing compose does) with `start_period: 5m`, no host port.
  - `ui`: image `v2a-inspect:demo`; `V2A_INSPECT_AUDYSSEY_TOOLS_URL: http://tools:8700`; `V2A_INSPECT_AUDYSSEY_TOOL_TOKEN: ${AUDYSSEY_TOOL_TOKEN}`; `GEMINI_API_KEY: ${GEMINI_API_KEY:?set GEMINI_API_KEY in .env.demo}`; port `127.0.0.1:${V2A_INSPECT_UI_PORT:-8501}:8501`; `depends_on` inference + tools `service_started`.
  - All GPU services: `gpus: [{driver: nvidia, device_ids: ["${V2A_GPU_DEVICE:?set V2A_GPU_DEVICE}"]}]`.
  - `env_file: .env.demo` on all three.
- [ ] **Step 2:** Write `.env.demo.example`: every key from Global Constraints with its value, `V2A_GPU_DEVICE=`, `GEMINI_API_KEY=replace-me`, `ELEVENLABS_API_KEY=replace-me`, `AUDYSSEY_TOOL_TOKEN=` (comment: `openssl rand -hex 24`), `V2A_DOCKER_UID`/`GID`, `V2A_INSPECT_UI_PASSWORD=`.
- [ ] **Step 3: Verify.** `cp .env.demo.example /tmp/e && sed -i 's/^V2A_GPU_DEVICE=$/V2A_GPU_DEVICE=7/; s/^AUDYSSEY_TOOL_TOKEN=$/AUDYSSEY_TOOL_TOKEN=x/' /tmp/e && docker compose -f compose.demo.yaml --env-file /tmp/e config -q` → exit 0. Removing `AUDYSSEY_TOOL_TOKEN` from the copy → error naming the key (Review Focus 2).
- [ ] **Step 4: Commit** `chore: add demo compose file and env template`.

### Task 2: Inference server Bearer token (RunPod)

**Files:**
- Modify: `server/src/v2a_inspect_server/settings.py`, `server/src/v2a_inspect_server/app.py`
- Modify: `src/v2a_inspect/client/config/settings.py`, `src/v2a_inspect/client/endpoints/base.py`
- Test: `server/tests/test_auth.py`, `tests/test_client_auth.py`

**Interfaces:**
- Produces: server setting `api_token: str = ""` (env `V2A_SERVER_API_TOKEN`); client setting `server_token: str = ""` (env `V2A_INSPECT_CLIENT_SERVER_TOKEN`). Blank = no auth, as today.

- [ ] **Step 1: Failing tests.** Server (TestClient, token `"t"` set): `GET /healthz` → 200 without header; `POST /videos/upload` without header → 401; with `Authorization: Bearer t` → not 401. Token blank → no 401. Client: with `server_token="t"` the built `httpx.AsyncClient` has header `Authorization: Bearer t`; blank → no header.
- [ ] **Step 2:** Run both test files → FAIL.
- [ ] **Step 3:** Server: one FastAPI dependency or middleware that skips `/healthz` and compares with `secrets.compare_digest`. Client: pass `headers` to `httpx.AsyncClient` in `base.py`.
- [ ] **Step 4:** Run → PASS. Run the full backend and server test suites → no new failures.
- [ ] **Step 5:** Add both keys (blank) to `.env.demo.example` and `.env.example`. Commit `feat: optional bearer token for the inference server`.

### Task 3: Emergency bundle export/import

**Files:**
- Create: `src/v2a_inspect/ui/bundle.py`
- Modify: `src/v2a_inspect/ui/routes.py` (two routes), `web/src/components/OpenVideoDialog.tsx`, the component that holds the top-bar secondary actions (add "Save bundle")
- Test: `tests/test_bundle.py`

**Interfaces:**
- Consumes: `_asset_work_root(asset) -> Path | None` and `_root_dir(work_dir)` in `routes.py`; `store.set_complete_asset(asset, stage=...)`.
- Produces: `write_bundle(asset: VideoAsset, root: Path, out: BinaryIO) -> None` and `read_bundle(data: BinaryIO, dest_root: Path) -> VideoAsset`. Routes: `GET /api/bundle` → `application/zip`, filename `<video stem>-bundle.zip`; `POST /api/bundle/import` (multipart `bundle`, optional `work_dir`) → `{"status": "imported"}`.

- [ ] **Step 1: Failing tests** in `tests/test_bundle.py`:
  - `test_roundtrip_rewrites_paths`: build a root `a/v2a-inspect-ui` with a source video, one track stem, one separated stem, residual; asset JSON points there. `write_bundle` → `read_bundle` into `b/v2a-inspect-ui` → every `Path` in the dumped JSON starts with the new root and exists; `str(old_root)` does not appear in the dumped JSON.
  - `test_rejects_unsafe_members`: zip with member `../x` → `ValueError`; member `/abs` → `ValueError`.
  - `test_missing_asset_json`: zip without `asset.json` → `ValueError`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement. Zip layout: `asset.json` (dumped with `exclude_computed_fields=True`, root recorded as `"bundle_root"` in a sibling `bundle.json`) + `files/<path relative to root>` for every file under the root. Import: validate members, extract under `dest_root`, replace the old root string with `dest_root` in the JSON text, `VideoAsset.model_validate_json`. Routes map `ValueError` → 400; export returns 404 when no asset or the asset has no work root; import returns 409 while running.
  `# ponytail: zips the whole work root (may include other uploads); filter to referenced paths if bundles get large.`
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5: UI.** In the dialog's "Load a saved project" section, replace the Video + JSON inputs with one `Bundle (.zip)` file input posting to `/api/bundle/import`. Add a secondary "Save bundle" link (`<a href="/api/bundle" download>`), shown only when an asset is loaded. Keep `/api/asset/import` route as is. Run `npm test` and `npx tsc --noEmit` → pass.
- [ ] **Step 6: Commit** `feat: bundle export and import for offline demo recovery`.

### Task 4: Preflight and warmup script

**Files:**
- Create: `scripts/demo_preflight.py`
- Test: `tests/test_demo_preflight.py`

**Interfaces:**
- Consumes: Task 1 service names and URLs; Task 3 nothing.
- Produces: CLI `uv run python scripts/demo_preflight.py [--ui http://127.0.0.1:8501] [--gpu N] [--warmup VIDEO ...]`; exit 0 only if every check passes. Pure helpers `check_snapshots(hf_root: Path, pins: dict[str, str]) -> list[str]` and `parse_gpu_used_mib(nvidia_smi_csv: str, gpu: int) -> int`.

- [ ] **Step 1: Failing tests:** `check_snapshots` returns `[]` when `hub/models--a--b/snapshots/<hash>` exists and one error naming the repo otherwise; `parse_gpu_used_mib("0, 1 MiB\n7, 36712 MiB\n", 7) == 36712`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement. Checks, one line each, `OK`/`FAIL <reason>`:
  - `docker image inspect audyssey-tools:demo-1117 --format {{.Id}}` equals the pinned ID.
  - Weights: `check_snapshots` with the three pins.
  - Containers: `docker compose -f compose.demo.yaml ps` shows `ui`, `inference`, `tools` running; `docker compose exec` `/healthz` on inference and `/health` on tools (they have no host port). Tools not healthy → FAIL "tools still loading" (Review Focus 1).
  - UI `/healthz` 200.
  - Keys: Gemini `GET https://generativelanguage.googleapis.com/v1beta/models/gemini-4-hydrogen` with the key header → 200; ElevenLabs `GET https://api.elevenlabs.io/v1/user` → 200. Read keys from the environment; never print them.
  - GPU: other users' processes on `--gpu` → FAIL; used memory printed.
  - `--warmup VIDEO`: upload through the UI API, run analysis + Generate (and Separate if the video has audio), poll the SSE/status until complete, print seconds per stage and peak GPU memory.
- [ ] **Step 4:** Run tests → PASS. Run the script without warmup against nothing running → exits non-zero with FAIL lines, no traceback.
- [ ] **Step 5: Commit** `feat: demo preflight and warmup script`.

### Task 5: Build images and live run on sv3 (needs GPU + Gemini approval)

**Files:**
- Modify: `docs/demo-measurements.md` (add a "Compose (3 containers)" section)

- [ ] **Step 1:** `docker compose -f compose.demo.yaml --env-file .env.demo build` → both v2a images build. Record image IDs.
- [ ] **Step 2:** Ask the user for approval and a GPU. Then `up -d`, wait for tools healthy, run `scripts/demo_preflight.py --gpu N --warmup samples/sample.mp4 work/task11/sample_silent.mp4`.
- [ ] **Step 3:** Expected: preflight all OK; raw upload → SAM3 tracking → analysis → Generate completes for both; Separate completes for the audio video; peak GPU memory ≤70 GB. Over 70 GB → stop and report; the fallback is to move `tools` to a second GPU (`V2A_TOOLS_GPU_DEVICE`).
- [ ] **Step 4:** In the UI (SSH port forward), regenerate one track, Save bundle, `down`, `up`, Load bundle → stems play, regenerate works.
- [ ] **Step 5:** Write measurements (cold/warm per stage, peak memory, image IDs). Commit `docs: compose demo measurements`.

### Task 6: Runbook, image export, tag

**Files:**
- Create: `docs/demo-runbook.md`

- [ ] **Step 1:** Runbook sections: prerequisites; `.env.demo` setup; up / preflight / warmup; SSH forward `ssh -L 8501:127.0.0.1:8501 sv3-a100`; failure table (tools loading, Gemini down → `gemini-3.5-flash`, GPU taken → change `V2A_GPU_DEVICE`, everything down → Load bundle); RunPod backup (tools pod from `audyssey-tools:demo-1117`, inference pod with `V2A_SERVER_API_TOKEN`, UI container on the laptop pointing at both); image export `docker save audyssey-tools:demo-1117 v2a-inspect:demo v2a-inspect-server:demo | zstd > <path>` or push to the user's registry (user decides where).
- [ ] **Step 2:** Clean-clone check: `git clone` the fork into scratch, checkout the branch, `docker compose -f compose.demo.yaml --env-file <copied .env.demo> config -q` → exit 0.
- [ ] **Step 3:** Commit `docs: demo runbook`. Tag `demo-20261119` locally (push only when asked); tag images `:demo-20261119` as well.

**Not in this plan:** the RunPod live run (paid; separate approval), Kokoro TTS (user trial 2026-11-20), demo video choice and rehearsal (P4).
