# SoundPilot

**SoundPilot은 multitrack 사운드 디자인을 위한 tool-calling agent 시스템이다** (team
sogang V2A). 영상을 편집 가능한 multitrack sound timeline으로 바꾸고, track마다 알맞은
오디오 모델로 소리를 생성하거나 원본 오디오에서 분리한 뒤, 결과를 영상에 mix한다.

제품 이름은 SoundPilot이다 (UI 제목과 Docker image). Python package 이름은 그대로
`v2a_inspect`이고 CLI 이름은 그대로 `v2a`이다.

## 동작 방식

1. PySceneDetect가 영상을 scene으로 나눈다.
2. Gemini가 scene마다 분석한다.
3. SAM3가 object를 tracking하고 visual event를 만든다.
4. Gemini sound-timeline agent가 소리를 source / track / event로 계획한다.
5. 라우팅이 track마다 오디오 모델(Generate) 또는 분리(Separate)를 지정한다.
6. track별 오디오를 만들어 영상에 mix한다.

## 기능

- **Generate mode** (기본), track별 라우팅:
  - 화면에 보이고 화면과 타이밍을 맞추는 SFX: SelVA w/ SelSync (Audyssey tools server, track당 stem 하나).
  - 앰비언스와 화면 밖 source: ElevenLabs SFX.
  - 음성: Kokoro TTS (영어만 지원).
  - event 구간 안에서 무음인 SelSync stem은 전체 실행에서 ElevenLabs로 대체된다.
    track 단위 재생성에서는 대체하지 않고 표시만 한다.
- **Separate mode** (원본 오디오 필요): Gemini가 원본 오디오를 듣고 source를 계획한다
  (Audyssey planning-evidence-v3, source별 오디오 검증 포함).
  SAM Audio가 각 source를 분리하며, 구간(span)을 positive anchor로 사용한다. 후보 8개를
  CLAP과 SAM Audio Judge로 rerank한다. source는 SAM3 object와 연결된다 (이름으로 먼저,
  다음으로 box IoU). residual(원본에서 stem을 뺀 나머지)은 켜고 끌 수 있다.
- **편집**: "Edit spans"는 event 박스를 이동하고 크기를 조절한다 (생성과 분리의 입력이 된다).
  "Edit audio"는 stem을 바꾸지 않고 각 track의 clip을 자르고 이동한다.
  "Add track"은 span을 여러 개 받고 모델은 SelSync / ElevenLabs / Kokoro / SAM Audio 중에서 고른다.
  "Delete track"은 track을 지운다. "Regenerate with new spans"는 편집한 track만 다시 생성한다.
- UI의 **Analysis panel**과 **project history** (Recent projects).
- **공유**: Save bundle (zip, 10초 clip당 약 30-40 MB)과 Download video (MP4).
  bundle은 "Load a saved project"로 연다.

알려진 제한: 분리를 실행하면 생성된 오디오가 교체된다 (둘 다 필요하면 Generate를 먼저
실행한다). Kokoro는 영어만 지원한다. 앰비언스는 scene마다 나뉠 수 있다.
UI 사용 안내: [docs/usage.md](docs/usage.md).

## 실행 방법

### RunPod 단일 image (주 경로)

image 하나가 pod 안에서 UI, inference server, Audyssey tools server를 함께 실행한다.

- Image: `ghcr.io/gwpark0741/soundpilot:<tag>` ([Image tag](#image-tag) 참고).
- GPU: 80 GB, CUDA 12.8 이상. Container disk 50 GB, network volume 100 GB 이상을
  `/workspace`에 연결한다. HTTP port `8501`을 노출한다.
- 환경 변수: `V2A_INSPECT_UI_PASSWORD`와 `GEMINI_API_KEY` (필수),
  `HF_TOKEN` (첫 부팅에만 필요, gated weight), `ELEVENLABS_API_KEY` (앰비언스 SFX).

설정, weight 목록, 첫 부팅 절차: [docs/runpod.md](docs/runpod.md).
기록은 `/workspace/data`에 저장된다. network volume이 없으면 pod를 terminate할 때
기록이 사라지므로 중지하기 전에 bundle을 저장한다.

### Docker 서버 실행 (sv3)

`compose.demo.yaml`은 `inference`, `tools`, `ui`를 container 세 개로 GPU 1장에 올린다
(Hunyuan off, NVENC off, weight는 `HF_CACHE`에서 읽기 전용):

```bash
cp .env.demo.example .env.demo   # fill V2A_GPU_DEVICE, GEMINI_API_KEY, ELEVENLABS_API_KEY, AUDYSSEY_TOOL_TOKEN
docker compose -f compose.demo.yaml --env-file .env.demo up -d
uv run python scripts/demo_preflight.py --gpu <N> --warmup <video> [<video> ...]
```

`--warmup`은 첫 실제 호출 전에 tools model을 로드한다.
[docs/demo-runbook.md](docs/demo-runbook.md)와
[docs/demo-measurements.md](docs/demo-measurements.md)를 참고한다.

### 로컬 개발

```bash
cp .env.example .env
uv sync --extra ui
uv sync --project server
(cd web && npm ci && npm run build)
```

UI는 `web/dist`의 build된 frontend를 서비스하므로 `v2a ui`로 화면을 보려면 build 단계가
필요하다. Node는 20.19+ 또는 22.12+를 사용한다 (Vite 7).

`.env`에 Gemini API key를 추가한 뒤, 저장소가 관리하는 가상 환경에서 inference server와
UI를 한 번에 시작한다.

```bash
./scripts/start-local.sh
```

이 스크립트는 실행한 host 사용자 권한으로 두 process를 실행하며, UI는 `.venv`를,
GPU inference는 `server/.venv`를 사용한다. `Ctrl+C`를 누르면 둘 다 종료된다.
Audyssey tools server는 시작하지 않는다. Generate (SelSync)와 Separate에는 tools server가
필요하므로 `V2A_INSPECT_AUDYSSEY_TOOLS_URL`과 `V2A_INSPECT_AUDYSSEY_TOOL_TOKEN`을 설정한다.

`http://127.0.0.1:8501`을 연다 (`V2A_INSPECT_UI_PASSWORD`가 설정되어 있으면 password 로그인).
UI 흐름은 다음과 같다.

1. **Open video**: "Analyze a new video" (SAM3 + agent 실행) 또는 "Load a saved project"
   (bundle zip).
2. 상단 바에서 **Generate** 또는 **Separate**를 고른 뒤 **Generate audio** /
   **Separate audio**를 누른다.
3. Timeline의 lane이 곧 track 목록이다. lane을 선택하면 selected-track 패널
   (Description, Regenerate / Re-separate / Generate instead, residual switch)이 열린다.
   lane마다 Play와 Solo를 쓰고 event를 편집한다.
4. mix된 영상을 미리 본다. Open video > Advanced에 **Download video (MP4)**가 있다.
   **Save bundle**은 project 전체를 zip으로 내보내며, **Load a saved project**로 다시 열 수 있다.

mode를 전환하면 저장된 mode만 바뀌며, track은 다음 main 버튼 실행에서 다시 라우팅된다.
Kokoro를 선택하면 첫 사용 때 model을 다운로드한다.

CLI entrypoint (package와 command 이름은 그대로이다):

- `uv run v2a run VIDEO`: 영상을 분석하고 `VideoAsset` JSON을 내보낸다.
- `uv run v2a ui`: 업로드, timeline 편집, 생성, 미리보기 UI를 연다.
- `uv run v2a synthesize`: 내보낸 asset 또는 timeline에서 오디오를 생성한다.
- `uv run --project server v2a-inspect-server serve`: inference API를 실행한다.

이들은 한 번 실행하고 끝나는 CLI/UI entrypoint이다. sound-timeline inference 단계는
내부적으로 LangGraph를 사용하며, 대화형 CLI loop는 아니다.

`docker compose up --build` (`docker-compose.yaml`)는 UI와 inference server만 시작하며
UI는 `http://127.0.0.1:8501`에서 열린다. `.env`에 `V2A_DOCKER_UID`, `V2A_DOCKER_GID`
(`id -u`, `id -g`)와 `V2A_GPU_DEVICE`를 설정한다. tools server가 없으므로
Generate/Separate에는 `compose.demo.yaml`을 사용한다.

## Image tag

| Tag | Build 방식 | 상태 |
|---|---|---|
| `demo-20261123` | sv3에서 build | GPU에서 end-to-end 검증 완료. 발표용 버전이며, release note가 다르게 안내하기 전까지는 이 tag가 안전하다. |
| `latest` | main에 merge될 때마다 GitHub Actions가 자동 build | main을 따라 움직인다. 처음 자동 build된 image(`000a20c`)는 2026-11-24에 같은 GPU end-to-end 검사를 통과했다. 이후 merge는 build만 확인했고 GPU에서는 확인하지 않았다. |
| `<commit sha>` | 자동 build, 해당 commit의 `latest`와 같은 build | RunPod에서 변경 하나만 시험하려고 고정할 때 사용한다. |

모든 image의 tools 부분은 고정된 `ghcr.io/gwpark0741/soundpilot-tools:demo-1117`이다.

## 개발 workflow

`main`은 ruleset("main protection")으로 보호된다. 변경은 pull request를 거쳐야 하고,
아래 네 가지 검사가 통과해야 하며, force push와 삭제는 막혀 있고, approval은 필요하지 않다.

1. `main`에서 branch를 만든다 (`git switch -c feat/<topic>`).
2. commit하고 branch를 push한 뒤 `main`을 대상으로 PR을 연다.
3. PR에서 `ci-lint`가 실행된다.
   - `src/`와 `server/src/`에 대한 `ruff check`, `ruff format --check`, `ty check`
   - backend `pytest` (ffmpeg 포함)
   - web `npm test`와 `npm run build` (Node 22)
   - UI image와 server image의 build 검사 (push 없음)
4. 검사가 통과하면 PR을 merge한다.
5. 코드나 Docker 파일(`src/`, `server/`, `web/`, `scripts/runpod/`, `pyproject.toml`,
   `uv.lock`, `Dockerfile`, `Dockerfile.runpod`, `.dockerignore`, publish workflow)을
   바꾼 merge는 Publish Image를 실행한다. 이 workflow는 저장소에서 UI image와 server image를
   build하고, `Dockerfile.runpod`로 tools image와 합친 뒤 `soundpilot:latest`와
   `soundpilot:<sha>`를 push한다 (약 25분). 문서만 바꾼 merge는 아무것도 build하지 않는다.

tools 버전을 바꾸려면 새 Audyssey tools image를 GPU에서 end-to-end로 검증한 뒤
`.github/workflows/publish-image.yaml`의 `TOOLS_IMAGE`를 바꾼다.

팀원용 단계별 안내: [docs/contributing.md](docs/contributing.md).

## 테스트

```bash
uv run --extra ui --with pytest --with pytest-asyncio python -m pytest -q tests   # 208 passed
(cd web && npm test && npm run build)                                             # 21 passed, Node 22
uv run --project server python -m unittest discover server/tests
```

CI에는 API key가 없으므로 테스트는 `.env`에 의존하면 안 된다.

## 사전 요건

- inference server(SAM3)와 tools server를 위한 NVIDIA GPU가 있는 Linux.
- 분석과 timeline agent를 위한 Gemini API key (`GEMINI_API_KEY`).
- 앰비언스 SFX를 위한 Audyssey tools server와 `ELEVENLABS_API_KEY`.
- SAM3는 `facebookresearch/sam3`에서 설치한다. checkpoint를 받으려면 접근 권한이 있는
  Hugging Face 계정이 필요할 수 있다 (`HF_TOKEN` 설정).
- 선택: Hunyuan V2A (`V2A_SERVER_HUNYUAN_ENABLED`, inference server 기본값은 true이며
  첫 사용 때 weight 약 8 GB). demo image는 이 기능을 끈다.
- Kokoro TTS는 첫 사용 때 model을 다운로드한다.

`V2A_INSPECT_TTS_PROVIDER=elevenlabs`는 Kokoro 대신 ElevenLabs TTS를 선택한다. 현재
ElevenLabs plan은 이를 402 `paid_plan_required`로 거부한다.

## 샘플 데이터

`samples/`에는 7초 clip과 분석된 `VideoAsset`(scene, object track, agent가 생성한 sound
timeline)이 있다. UI 대화상자는 bundle zip만 받으므로 샘플은 API(`POST /api/asset/import`)로
불러온다.

```bash
curl -F video=@samples/sample.mp4 -F asset=@samples/sample.video-asset.json \
  http://127.0.0.1:8501/api/asset/import
```

`V2A_INSPECT_UI_PASSWORD`가 설정되어 있으면 `-H "Authorization: Bearer $V2A_INSPECT_UI_PASSWORD"`를 추가한다.
keyframe 이미지와 SAM3 overlay는 포함되어 있지 않으므로, 분석을 직접 실행하기 전까지
해당 미리보기는 비어 있다. Generate/Separate에는 tools server가 필요하고, 분석에는
inference server가 필요하다.

## Frontend 개발

UI backend와 Vite dev server를 따로 실행한다.

```bash
uv run v2a ui                  # terminal 1, port 8501
cd web && npm ci && npm run dev   # terminal 2, proxies /api and /events to 8501
```

## Package / 디렉터리 구성

- `src/v2a_inspect/`: 메인 CLI, agent pipeline, editor API, inference client.
- `web/`: React timeline editor.
- `server/`: SAM3, Kokoro TTS, 선택 기능인 Hunyuan V2A를 위한 GPU inference API.
- `tests/`와 `server/tests/`: regression 테스트.
- `Dockerfile`, `server/Dockerfile`: UI image와 inference server image.
- `Dockerfile.runpod`: UI, server, tools image를 RunPod 단일 image로 합친다.
- `scripts/runpod/`: `start.sh` (pod entrypoint)와 `fetch_weights.py` (weight 다운로드).
- `scripts/start-local.sh`, `scripts/demo_preflight.py`, `scripts/run.sh`: 로컬 시작,
  demo preflight, `v2a run`을 감싼 batch wrapper.
- `compose.demo.yaml`, `docker-compose.yaml`: Docker 배포.
- `.github/workflows/`: `ci-lint.yaml` (PR 검사)과 `publish-image.yaml` (merge 시 image build).
- `docs/`: [usage](docs/usage.md), [runpod](docs/runpod.md),
  [demo-runbook](docs/demo-runbook.md), [contributing](docs/contributing.md).

runtime media, 생성된 출력, 가상 환경, 다운로드한 model weight는 의도적으로 Git에서
제외한다. `.env.example`을 `.env`로 복사해 사용하고, 실제 credential은 commit하지 않는다.
