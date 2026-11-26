# RunPod one-pod demo

UI, inference server, Audyssey tools server를 하나의 image(`Dockerfile.runpod`)로 묶어 RunPod GPU pod 1개에서 실행한다. 세 프로세스는 모두 `0.0.0.0`에서 listen하며, 두 GPU server는 하나의 token으로 보호한다. `V2A_INSPECT_UI_PASSWORD`를 비워 두면 UI 없이 GPU server만 실행한다 ([UI는 로컬에서, GPU 서버는 pod에서](#8-ui는-로컬에서-gpu-서버는-pod에서) 참고).

## 1. 구성

| 프로세스 | 포트 | 런타임 | 사용하는 가중치 |
|---|---|---|---|
| inference server | 0.0.0.0:8080 | Python 3.12, `/app/server/.venv` | SAM 3.1, Kokoro |
| tools server | 0.0.0.0:8700 | Python 3.12 (standalone), `/app/gpu/.venv` | SAM Audio + Judge + CLAP (분리), SelVA base + internal model (생성) |
| UI (선택) | 0.0.0.0:8501 | Python 3.13, `/opt/venv` | 없음 (Gemini API 호출) |

기존 세 image(`v2a-inspect-server`, `v2a-inspect`, `audyssey-tools`)를 build arg로 받아 한 image에 복사한다. 각 venv가 절대 경로를 쓰므로 원래 경로를 그대로 유지한다. 가중치는 image에 넣지 않고 `/workspace/hf`에 받는다. `scripts/runpod/start.sh`가 entrypoint이며 어느 한 프로세스라도 종료되면 pod 전체가 non-zero로 종료된다.

## 2. Pod 설정

| 항목 | 값 |
|---|---|
| GPU | 80 GB GPU 1장 (A100 80GB, H100). 단일 image에서 분석, 생성, 분리(후보 8개 rerank)를 차례로 실행했을 때 최대 GPU 메모리가 68.5 GB였으므로 48 GB GPU에는 들어가지 않는다. |
| CUDA | driver 570 이상 (CUDA 12.8 이상). 두 torch가 모두 `cu128` build이다. RunPod template의 CUDA 필터를 12.8 이상으로 둔다. |
| Container disk | 50 GB 이상 (triton cache, 임시 영상) |
| Network volume | 100 GB 이상, mount path `/workspace` (가중치 약 41 GB + 작업 데이터) |
| Image | `ghcr.io/gwpark0741/soundpilot:<tag>`. `demo-20261123`은 sv3에서 build하고 GPU에서 end to end로 검증한 시연용 image이다. `latest`와 `<commit sha>`는 main에 merge될 때마다 GitHub Actions가 자동으로 build한다. 첫 자동 build(`000a20c`)는 2026-11-24 GPU end to end 검증을 통과했다. 이후 merge로 바뀐 `latest`는 build만 확인된 상태이므로, 시연에는 GPU로 검증한 tag를 쓴다. |
| Container start command | 비워 둔다 (image의 `CMD`가 `start.sh`를 실행) |
| Expose HTTP port | `8501` (UI를 실행할 때). GPU server만 쓰는 구성은 8절 참고 |

환경변수 (RunPod Secrets 사용을 권장):

| 변수 | 필수 | 설명 |
|---|---|---|
| `V2A_INSPECT_UI_PASSWORD` | UI를 쓸 때 | UI 로그인 password. 비어 있으면 UI를 시작하지 않고 GPU server만 실행한다. proxy URL이 공개되므로 충분히 긴 값을 쓴다. |
| `GEMINI_API_KEY` | UI를 쓸 때 | planner LLM. GPU server만 실행할 때는 필요 없다. |
| `ELEVENLABS_API_KEY` | 선택 | ElevenLabs를 쓸 때만. 기본 TTS는 Kokoro (`V2A_INSPECT_TTS_PROVIDER=kokoro`) |
| `HF_TOKEN` | 첫 부팅 | 가중치 다운로드용 read token. 아래 접근 권한이 필요하다. 다운로드가 끝나면 세 서버의 환경에서 제거된다. |
| `SOUNDPILOT_TOKEN` | 선택 | 두 GPU server가 공통으로 쓰는 Bearer token. 비우면 `AUDYSSEY_TOOL_TOKEN`을, 그것도 없으면 부팅 때마다 무작위로 생성해 로그에 출력한다. |
| `AUDYSSEY_TOOL_TOKEN` | 선택 | `SOUNDPILOT_TOKEN`이 없을 때만 쓰는 이전 이름 |

`HF_TOKEN`에 필요한 접근 권한: `facebook/sam3.1`, `facebook/sam-audio-large-tv`, `facebook/sam-audio-judge` (모두 gated, SAM License 승인), `sogang-audyssey` 조직 멤버 (`sogang-audyssey/internal-model`은 private). 접근이 없는 repo는 다운로드 로그에 `no access to: ...`로 출력되고 pod가 종료된다.

## 3. 첫 부팅과 확인

1. 첫 부팅에서 `fetch_weights.py`가 `/workspace/hf`에 약 41 GB를 받는다. 이후 부팅에서는 이미 있는 파일을 건너뛰고, `HF_HUB_OFFLINE=1`로 실행한다.
2. 모델 적재(약 3분)가 끝나면 로그에 `[start] all services are up: inference :8080, tools :8700, UI :8501`이 나온다. Logs 탭에서 `[inference]`, `[tools]`, `[ui]` prefix로 서비스별 출력을 구분한다.
3. Pod의 Connect -> HTTP Service `8501`을 열거나 `https://<pod-id>-8501.proxy.runpod.net`에 접속해 `V2A_INSPECT_UI_PASSWORD`로 로그인한다.
4. 영상을 열고 Generate와 Separate를 각각 한 번 실행한다. 첫 호출은 모델 적재 때문에 느릴 수 있다.

가중치 상태만 확인하려면 pod 터미널에서 다음을 실행한다 (오프라인, 다운로드 없음).

```bash
/app/server/.venv/bin/python /app/runpod/fetch_weights.py --check
```

## 4. 중지

RunPod 콘솔에서 pod를 Stop(또는 Terminate)한다. `/workspace` network volume은 유지되므로 다음 pod에서 가중치를 다시 받지 않는다. 사용하지 않을 때는 pod와 volume 모두 비용이 발생하므로 volume도 불필요해지면 삭제한다.

## 5. 가중치 목록

`scripts/runpod/fetch_weights.py`의 `WEIGHTS`가 유일한 목록이다. 각 revision은 데모를 검증한 `/home/common/huggingface`의 snapshot과 같다. 필요한 파일만 받는다.

| 용도 | Repo | Revision | 크기 |
|---|---|---|---|
| inference | `facebook/sam3.1` | `daa63191` | 3.5 GB |
| inference | `hexgrad/Kokoro-82M` | `f3ff3571` | 0.33 GB |
| 분리 | `facebook/sam-audio-large-tv` | `b9fc9468` | 14.9 GB |
| 분리 | `t5-base` | `a9723ea7` | 0.9 GB |
| 분리 (후보 8개 rerank) | `facebook/sam-audio-judge` | weights `cd52eb97` (`sam_audio` branch), config/tokenizer `d461b0b9` (`main`) | 6.1 GB |
| 분리 (rerank), 생성 (scoring) | `lukewys/laion_clap` | `b3708341` | 1.9 GB |
| CLAP text tower | `roberta-base` (weights 포함), `bert-base-uncased`, `facebook/bart-base` | `e2da8e2f`, `86b5e093`, `aadd2ab0` | 0.5 GB (나머지 둘은 tokenizer만, 10 MB 미만) |
| 생성 | `jnwnlee/SelVA` | `857f32db` | 2.7 GB |
| 생성 | `sogang-audyssey/internal-model` | `2b5bb25e` | 4.2 GB |
| 생성 | `apple/DFN5B-CLIP-ViT-H-14-384` | `01b771ed` | 3.95 GB |
| 생성 | `google/flan-t5-base` | `7bcac572` | 1.0 GB |
| 생성 | `nvidia/bigvgan_v2_44khz_128band_512x` | `95a9d1dc` | 0.49 GB |

합계 약 40 GB. SAM 3.1은 inference server가 항상 적재하며, tools server의 visual prompting은 기본값(off)이라 사용하지 않는다.

## 6. Image build와 push (owner)

main에 merge되면 GitHub Actions(`.github/workflows/publish-image.yaml`, Publish Image)가 단일 image를 자동으로 빌드해 `ghcr.io/gwpark0741/soundpilot:latest`와 `:<commit sha>`로 올린다. 약 25분이 걸린다.

- 실행 조건: `src/`, `server/`, `web/`, `scripts/runpod/`, `pyproject.toml`, `uv.lock`, `Dockerfile`, `Dockerfile.runpod`, `.dockerignore`, workflow 파일이 바뀐 merge. 문서, 테스트만 바뀐 merge는 image를 만들지 않는다. Actions 탭에서 수동으로(`workflow_dispatch`) 실행할 수도 있다.
- 구성: UI와 server image는 레포 코드로 빌드하고, Audyssey tools는 검증한 image `ghcr.io/gwpark0741/soundpilot-tools:demo-1117`을 고정해서 `Dockerfile.runpod`로 합친다.
- tools를 바꾸려면 새 tools image를 GPU에서 end to end로 검증한 뒤 workflow의 `TOOLS_IMAGE`를 바꾼다.
- PR에서는 `ci-lint`가 UI image와 server image의 build만 확인하고 push하지 않는다.

기여 절차는 [contributing.md](contributing.md)를 따른다. 아래는 sv3에서 직접 빌드할 때의 절차이다.

ghcr.io에 `write:packages` 권한이 있는 personal access token이 필요하다.

```bash
echo "$GHCR_TOKEN" | docker login ghcr.io -u gwpark0741 --password-stdin

# sv3의 기본 builder는 RUN 단계에서 실패하므로 docker-container builder를 지정한다.
# docker-bake.hcl이 UI, server image를 stage로 함께 빌드하므로 docker save나 build context가 필요 없다.
# CACHE를 주면 CI가 올린 layer cache를 받아 바뀐 layer만 빌드한다.
TAG=demo-$(date +%Y%m%d)
BUILDX_BUILDER=audyssey-builder CACHE=ghcr.io/gwpark0741/soundpilot:buildcache \
  IMAGE=ghcr.io/gwpark0741/soundpilot TAG=$TAG \
  docker buildx bake -f docker-bake.hcl --set runpod.output=type=registry runpod
```

로컬에서 실행해 볼 image만 필요하면 `--load`로 받는다: `BUILDX_BUILDER=audyssey-builder docker buildx bake -f docker-bake.hcl --load runpod` (tag `soundpilot:dev`).

layer cache는 `ghcr.io/gwpark0741/soundpilot:buildcache-{ui,server,runpod}`에 있다. main에 merge되면 Publish Image workflow가 같은 cache로 빌드하고 갱신한다. `uv.lock`이나 `server/uv.lock`이 바뀌면 해당 venv layer만 다시 빌드된다.

ghcr.io package는 기본이 private이므로, RunPod에서 받으려면 package를 public으로 바꾸거나 RunPod의 Container Registry Auth에 ghcr 계정과 `read:packages` token을 등록한다. image에는 가중치와 secret이 들어 있지 않다.

## 7. 알려진 제약

- Image는 root로 실행한다. RunPod volume이 root 소유라 별도 사용자를 두면 `/workspace`에 쓸 수 없기 때문이다.
- 세 runtime(Python 3.12 두 개, 3.13 한 개)과 torch 두 벌이 들어 있어 image가 크다. 하나로 합치지 않은 이유는 각 서비스의 검증된 의존성 조합을 바꾸지 않기 위해서다.
- sv3-a100에서 같은 image를 container 1개로 띄워 분석, 생성, Kokoro, 프로젝트 기록, 분리까지 실행해 확인했다 (2026-11-23). RunPod pod에서는 아직 실행하지 않았다.
- server image가 설정하는 `TRANSFORMERS_CACHE`는 `start.sh`가 지운다. 남아 있으면 tools server가 `HF_HOME` 밖에서 `flan-t5-base`를 찾다가 생성과 분리가 모두 실패한다.

## 8. UI는 로컬에서, GPU 서버는 pod에서

UI를 로컬에서 실행하고, GPU가 필요한 inference server(8080)와 Audyssey tools server(8700)만 pod에서 실행할 수 있다. 하나의 token이 두 server를 보호한다.

Pod 설정:

- Expose HTTP port에 `8080`과 `8700`을 추가한다. pod에서 UI도 실행할 때만 `8501`을 추가한다.
- `V2A_INSPECT_UI_PASSWORD`를 비워 두면 `start.sh`가 UI 없이 GPU server만 실행하고 `GEMINI_API_KEY`도 요구하지 않는다.
- `SOUNDPILOT_TOKEN`은 선택이다. 설정하지 않으면 부팅 때마다 무작위 token을 생성한다.

두 server가 준비되면 pod 로그(Logs 탭)에 다음 내용이 출력된다. 생성한 token은 `Token:` 줄에 그대로 나오고, 직접 설정한 `SOUNDPILOT_TOKEN`은 출력하지 않는다.

```text
[start] Connect a local UI with: Open video > Advanced > GPU servers
[start]   Inference server URL: https://<pod-id>-8080.proxy.runpod.net
[start]   Audyssey tools URL:   https://<pod-id>-8700.proxy.runpod.net
[start]   Token: <token>
```

RunPod가 아닌 GPU host에서는 URL이 `http://<host>:8080`, `http://<host>:8700` 형태로 출력된다.

로컬에서 UI image를 build하고 실행한다.

```bash
docker buildx bake -f docker-bake.hcl --load --set ui.tags=soundpilot-ui:dev ui
docker run --rm -p 127.0.0.1:8501:8501 -e GEMINI_API_KEY=... \
  -e V2A_INSPECT_VIDEO_ENCODE_USE_NVENC=false \
  -e V2A_INSPECT_UI_WORK_DIR=/workspace/data/work \
  -v <project folder>:/workspace/data/work soundpilot-ui:dev
```

`V2A_INSPECT_VIDEO_ENCODE_USE_NVENC=false`는 GPU가 없는 host에서 영상 정규화(ffmpeg h264_nvenc)가 실패하지 않도록 필요하다. `http://127.0.0.1:8501`을 열고 Open video > Advanced > GPU servers에 두 URL과 token을 입력한다. 환경변수로 미리 지정하려면 `V2A_INSPECT_UI_SERVER_URL`, `V2A_INSPECT_AUDYSSEY_TOOLS_URL`, `V2A_INSPECT_CLIENT_SERVER_TOKEN`, `V2A_INSPECT_AUDYSSEY_TOOL_TOKEN`을 `docker run`에 `-e`로 넘긴다. 입력 칸의 값은 UI 프로세스가 켜져 있는 동안만 유지되며 pipeline이 실행 중일 때는 바꿀 수 없다.

- 저장된 project는 만들 때와 같은 경로에 mount해야 열린다. RunPod에서는 `/workspace/data/work`이다.
- 확인되지 않음: RunPod HTTP proxy는 약 100초를 넘는 요청을 끊을 수 있다. 분리나 생성이 timeout으로 실패하면 8080과 8700을 HTTP가 아닌 TCP 포트로 노출한다.
