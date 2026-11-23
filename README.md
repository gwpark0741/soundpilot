# SoundPilot

영상을 넣으면 장면 속 객체와 움직임을 분석해 **편집 가능한 멀티트랙 사운드**를 만들어 주는 에이전트 시스템입니다. (팀 sogang V2A)

- 소리를 트랙 단위로 나눠 만들기 때문에, 원하는 소리만 골라 고치고 다시 만들 수 있습니다.
- 소리 종류에 따라 생성 모델을 고릅니다. 동작 효과음은 영상 기반 생성(V2A), 배경음은 텍스트 기반 생성(T2A), 대사는 TTS를 씁니다.
- 원본 오디오가 있는 영상은 생성 대신 원본에서 소리를 분리할 수도 있습니다.

> 코드 안의 Python 패키지 이름은 `v2a_inspect`, CLI 이름은 `v2a`로 그대로입니다.

## 바로 실행하기

팀원은 **RunPod pod 하나**로 실행하는 방법을 쓰면 됩니다. 브라우저만 있으면 됩니다.

| 항목 | 값 |
|---|---|
| 이미지 | `ghcr.io/gwpark0741/soundpilot:demo-20261123` |
| GPU | 80 GB 1장 (A100 80GB 또는 H100), CUDA 12.8 이상 |
| 디스크 | Container disk 50 GB 이상, Network volume 100 GB 이상을 `/workspace`에 연결 |
| 포트 | HTTP `8501` |
| 환경변수 | `V2A_INSPECT_UI_PASSWORD`(접속 비밀번호), `GEMINI_API_KEY`, `HF_TOKEN`(첫 부팅에 가중치 다운로드), `ELEVENLABS_API_KEY` |

처음 켜면 모델 가중치 약 41 GB를 받습니다. 로그에 `all services are up`이 나오면 8501 포트로 접속합니다. 자세한 설정과 주의 사항은 [docs/runpod.md](docs/runpod.md)에 있습니다.

다른 실행 방법:

- **GPU 서버에서 Docker로 실행** (sv3): `compose.demo.yaml`로 서비스 3개를 띄웁니다. → [docs/demo-runbook.md](docs/demo-runbook.md)
- **로컬 개발 환경**: 아래 [로컬 개발](#로컬-개발) 절을 봅니다.

## 사용 흐름

1. **Open video**로 영상을 올리면 장면 분석, 객체 추적, 사운드 계획이 자동으로 진행됩니다.
2. 상단에서 **Generate**(생성) 또는 **Separate**(분리)를 고르고 실행합니다.
3. 타임라인에서 트랙별로 듣고 고칩니다.
   - **Edit spans**: 소리가 날 구간(이벤트 박스)을 옮기거나 늘이고 줄입니다.
   - **Edit audio**: 만들어진 소리를 자르거나 옮깁니다. 원본은 남아 있어 언제든 되돌릴 수 있습니다.
   - **Add track / Delete track**: 트랙을 추가하거나 지웁니다. 추가할 때는 타임라인에서 구간을 직접 그립니다.
   - 오른쪽 패널에서 설명을 고쳐 그 트랙만 다시 만듭니다. 구간을 고친 트랙은 **Regenerate with new spans**로 다시 만듭니다.
4. 결과는 **Save bundle**(프로젝트 zip)이나 **Download video (MP4)**로 받습니다. zip은 **Load a saved project**로 다시 엽니다.

화면별 자세한 사용법은 [docs/usage.md](docs/usage.md)에 있습니다.

## 동작 방식

```
영상 → 장면 분할(PySceneDetect) → 장면 분석(Gemini) → 객체 추적(SAM3)
     → 사운드 계획 에이전트(Gemini: 소리 원천 / 트랙 / 이벤트)
     → 트랙별 생성 또는 분리 → 영상에 mix
```

| 모드 | 소리 종류 | 사용 모델 |
|---|---|---|
| Generate | 화면 속 동작 효과음 | SelVA w/ SelSync (Audyssey tools) |
| Generate | 배경음, 화면 밖 소리 | ElevenLabs SFX |
| Generate | 대사 | Kokoro TTS (영어) |
| Separate | 원본 속 각 소리 | Gemini가 원본을 듣고 분리 계획 → SAM Audio로 분리 (후보 8개 중 최고 점수 선택) |

- SelSync 결과가 이벤트 구간에서 무음이면, 전체 생성 때는 ElevenLabs로 자동 대체합니다. 트랙 하나만 다시 만들 때는 대체하지 않고 실패로 표시합니다.
- 분리된 소리는 이름과 위치를 기준으로 SAM3 객체와 연결되어, 화면에 해당 객체가 표시됩니다.

## 알려진 한계

- 분리를 실행하면 생성 결과를 덮어씁니다. 시연할 때는 생성을 먼저, 분리를 나중에 보여 줍니다.
- 음성(Kokoro)은 영어만 지원합니다.
- 생성 타임라인에서 배경음이 장면마다 나뉘어 나올 수 있습니다.
- 효과음 분리는 배경음악이 강한 영상에서 약합니다.

## 이미지 태그

| 태그 | 만드는 곳 | 용도 |
|---|---|---|
| `demo-20261123` | sv3에서 직접 빌드, GPU 검증 완료 | 발표·시연용. 기본으로 이 태그를 씁니다 |
| `latest` | main에 merge될 때마다 GitHub가 자동 빌드 | 최신 코드 확인용 |
| `<커밋 해시>` | 위와 같음 | 특정 변경을 RunPod에서 확인할 때 |

자동 빌드는 이미지가 만들어지는지만 확인하고, GPU에서 실행해 보지는 않습니다. 첫 자동 빌드(`000a20c`)는 2026-11-24 sv3 GPU에서 전체 흐름 검증을 통과했습니다.

## 코드를 고칠 때

main은 보호되어 있어 직접 push할 수 없습니다. 아래 순서로 반영합니다.

1. main에서 브랜치를 만듭니다. (`git switch -c feat/<주제>`)
2. 수정 후 push하고 main으로 PR을 엽니다.
3. PR에서 검사 4개가 자동으로 돕니다. 모두 통과해야 merge할 수 있습니다.
   - `lint-and-format-check`: ruff, ty
   - `tests`: 백엔드 pytest
   - `web`: 웹 테스트와 빌드
   - `docker-build`: UI·서버 이미지 빌드 확인
4. merge하면 코드나 Docker 파일이 바뀐 경우에만 RunPod 이미지가 자동으로 빌드되어 `soundpilot:latest`, `soundpilot:<커밋 해시>`로 올라갑니다. 약 25분 걸립니다.

팀원용 단계별 안내는 [docs/contributing.md](docs/contributing.md)에 있습니다.

Audyssey tools 부분은 이 레포에 코드가 없어서, 검증한 이미지 `ghcr.io/gwpark0741/soundpilot-tools:demo-1117`을 고정해서 씁니다. tools를 바꾸려면 새 버전을 GPU에서 전체 흐름으로 검증한 뒤 `.github/workflows/publish-image.yaml`의 `TOOLS_IMAGE`를 바꿉니다.

## 로컬 개발

```bash
cp .env.example .env          # GEMINI_API_KEY 등 입력
uv sync --extra ui
uv sync --project server
(cd web && npm ci && npm run build)
./scripts/start-local.sh      # inference 서버 + UI, Ctrl+C로 종료
```

`http://127.0.0.1:8501`로 접속합니다. 생성과 분리에는 Audyssey tools 서버가 따로 필요하므로(`V2A_INSPECT_AUDYSSEY_TOOLS_URL`, `V2A_INSPECT_AUDYSSEY_TOOL_TOKEN`), 전체 기능은 RunPod 이미지나 `compose.demo.yaml`로 확인하는 편이 쉽습니다.

웹 화면만 고칠 때는 두 터미널에서 실행합니다.

```bash
uv run v2a ui                          # 백엔드, 8501
cd web && npm ci && npm run dev        # Vite 개발 서버, /api와 /events를 8501로 전달
```

## 테스트

```bash
uv run --extra ui --with pytest --with pytest-asyncio python -m pytest -q tests
uv run --project server python -m unittest discover server/tests
(cd web && npm test && npm run build)  # Node 22
```

테스트는 `.env`의 API 키 없이 통과해야 합니다. CI에는 키가 없습니다.

## 디렉터리 구성

| 경로 | 내용 |
|---|---|
| `src/v2a_inspect/` | 분석 파이프라인, 사운드 계획 에이전트, 생성·분리 연동, UI 백엔드 |
| `web/` | React 타임라인 편집 화면 |
| `server/` | GPU inference 서버 (SAM3, Kokoro) |
| `Dockerfile`, `server/Dockerfile` | UI 이미지, 서버 이미지 |
| `Dockerfile.runpod`, `scripts/runpod/` | RunPod 단일 이미지와 시작·가중치 다운로드 스크립트 |
| `docker-compose.yaml`, `compose.demo.yaml` | 서버에서 여러 컨테이너로 실행할 때 (전체 기능은 `compose.demo.yaml`) |
| `.github/workflows/` | PR 검사(`ci-lint.yaml`), 이미지 자동 빌드(`publish-image.yaml`) |
| `docs/` | 사용법, RunPod, 시연 절차, 기여 안내 |
| `tests/`, `server/tests/` | 테스트 |

`.env`에는 실제 키가 들어가므로 커밋하지 않습니다. 실행 결과물, 가상환경, 모델 가중치도 Git에 올리지 않습니다.
