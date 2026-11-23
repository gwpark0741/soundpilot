# Demo runbook

대상: `compose.demo.yaml` (inference, tools, ui 3 containers, GPU 1장). 측정값은 `docs/demo-measurements.md`의 "Compose (3 containers, GPU 7)" 절을 따른다.

## 1. Prerequisites

| 항목 | 값 |
|---|---|
| 이미지 | `v2a-inspect:demo` (`06d50a8ad286`, 2026-11-23 파형 dB 표시 반영), `v2a-inspect-server:demo` (`7cccf9e9eac9`), `audyssey-tools:demo-1117` (`cc603e8ffc46`) |
| 가중치 | `/home/common/huggingface` (read-only 마운트, `HF_HUB_OFFLINE=1`) |
| GPU | 다른 사용자의 process가 없는 GPU 1장 |

`.env.demo` 설정:

```bash
cp .env.demo.example .env.demo
openssl rand -hex 24   # AUDYSSEY_TOOL_TOKEN 값
nvidia-smi             # 비어 있는 GPU 확인 후 V2A_GPU_DEVICE에 지정
```

값을 채울 key: `V2A_GPU_DEVICE`, `GEMINI_API_KEY`, `ELEVENLABS_API_KEY`, `AUDYSSEY_TOOL_TOKEN`. `V2A_SERVER_API_TOKEN`과 `V2A_INSPECT_CLIENT_SERVER_TOKEN`은 RunPod 구성에서만 필요하다(비우면 인증 없음).

## 2. Rebuild (필요한 경우에만)

이미 빌드된 이미지를 사용한다. 재빌드가 필요하면 sv3의 기본 docker builder는 buildkit cachemounts 디렉터리가 없어 실패하므로 다음과 같이 builder를 지정한다.

```bash
BUILDX_BUILDER=audyssey-builder docker compose -f compose.demo.yaml --env-file .env.demo build
```

transformers의 GitHub fetch가 일시적으로 실패할 수 있으며, Dockerfile의 retry가 처리한다.

## 3. Demo-day sequence

```bash
docker compose -f compose.demo.yaml --env-file .env.demo up -d
docker compose -f compose.demo.yaml --env-file .env.demo ps   # 3개 모두 healthy (약 32 s)
python scripts/demo_preflight.py --gpu <V2A_GPU_DEVICE> --warmup <demo video> [<demo video> ...]
```

IMPORTANT: tools `/health`는 모델 적재 전에 통과한다. 청중 앞에서 첫 호출이 로딩 시간을 부담하지 않도록 반드시 `--warmup`을 붙여 preflight를 실행한다. Cold 시간(영상별 1회): analysis 약 100 s, Generate 15-48 s, Separate 65 s.

이후 노트북에서 SSH forward를 열고 접속한다.

```bash
ssh -L 8501:127.0.0.1:8501 sv3-a100
# http://127.0.0.1:8501
```

Warmup 후 데모 영상마다 "Save bundle"로 bundle을 하나씩 저장한다. Bundle에는 work root 전체가 들어간다(이전 실행의 업로드 포함).

## 4. GPU memory

Peak 약 49.6 GB (inference 약 28.7 GB, tools 약 20.9 GB, ui 0). 한도 70 GB 이내.

## 5. Failure table

| 증상 | 조치 |
|---|---|
| 첫 Generate/Separate가 매우 느림 | tools가 아직 모델을 적재 중이다. `--warmup` preflight를 다시 실행한다. |
| Gemini model unavailable | `.env.demo`에 `V2A_INSPECT_LLM_LARGE_MODEL=gemini-3.5-flash` 설정 후 `docker compose -f compose.demo.yaml --env-file .env.demo up -d ui` |
| GPU를 다른 사용자가 점유 | `V2A_GPU_DEVICE`를 비어 있는 GPU로 변경하고 `up -d` |
| 영상 정규화 실패 | A100에는 NVENC가 없다. `V2A_INSPECT_VIDEO_ENCODE_USE_NVENC`는 compose에서 `false`로 고정되어 있으므로 바꾸지 않는다. |
| preflight에서 ElevenLabs 401 `missing_permissions` | key에 `user_read`가 없는 제한 key이며, preflight는 이를 인증 성공으로 처리한다. 조치 불필요. |
| 전체 stack 장애 | UI만 띄우거나 기존 UI에서 Open video -> Load a saved project로 저장된 bundle을 연다. |

## 6. RunPod backup

세 서비스를 하나의 image(`Dockerfile.runpod`)로 묶어 pod 1개에서 실행한다. 설정, 가중치 목록, build와 push 명령은 [docs/runpod.md](runpod.md)를 따른다. 아직 RunPod GPU에서 실행으로 검증하지 않았다. 유료이며 사용 전 승인이 필요하다.

## 7. TTS

데모 compose는 speech track을 Kokoro(inference server, 영어 voice `af_heart`)로 생성한다. Kokoro의 영어 G2P가 쓰는 spaCy `en_core_web_sm`은 server 이미지에 포함되어 있다.
