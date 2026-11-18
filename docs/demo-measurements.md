# 데모 백엔드 GPU 실측 (Task 11)

측정일 2026-11-18. v2a_inspect 브랜치 `feat/demo-upgrade` (측정 시점 HEAD `fbb3225`) 대 실제 Audyssey 도구 서버.

## 환경

| 항목 | 값 |
|---|---|
| GPU | 물리 GPU 7 한 장 (A100-SXM4-80GB, 드라이버 570.124.06), 시작 전 비어 있음 |
| 가중치 | `/home/common/huggingface` (`HF_HUB_OFFLINE=1`), SAM Audio `facebook/sam-audio-large-tv`, judge `facebook/sam-audio-judge`, 후보 8개 |
| 분리 1차 측정 (venv) | `gpu/.venv`에서 `uv run --no-sync --project gpu audyssey tool-server --tool separation --tool generation --impl real` |
| 생성 포함 측정 (docker) | 이미지 `audyssey-tools:demo-1117` (id `cc603e8ffc46`) 한 컨테이너에 separation + generation, `--gpus device=7` |
| UI 백엔드 | `uv run v2a ui`, `V2A_INSPECT_TRACK_DESCRIPTION_DEFAULT=label` (A/B의 `first_event` 실행만 다름) |
| 입력 | `samples/sample.mp4` 7.1초 (오디오 있음), 같은 영상 `-an` (무음), 같은 JSON에 `apply_default_routing` 적용. 트랙 4개: Player Damage Impact, Player Footsteps, Background Wind Ambience (sfx/ambience), Player Hurt Grunt (sfx). 대사 트랙 없음 |
| 제외 | Gemini 분석(`POST /api/runs`)과 ElevenLabs는 호출하지 않음 (이미 분석된 샘플을 import) |

`work/`는 `.gitignore`에 없습니다. 이 문서만 커밋하고 `work/`와 오디오는 커밋하지 않았습니다.

## 실행 구분

| 실행 | 환경 | 이유 |
|---|---|---|
| 오디오 시나리오 분리 (아래 표 1) | venv | 처음 시도. 이 venv에는 `selva`가 없어 생성 호출은 모두 500 |
| 나머지 전부 | docker `audyssey-tools:demo-1117` | venv로는 생성 불가 (아래 "발생한 문제") |

## 오디오 시나리오 (분리)

| 항목 | venv | docker |
|---|---|---|
| Generate 1회차 (모델 로드 포함) | 160.5 s | 157.6 s |
| Generate 2회차 (warm) | 72.2 s | 72.6 s (`first_event` 실행의 오디오 시나리오) |
| 한 트랙 재분리 (`method=separate`) | 19.1 s, Footsteps `attempt` 1 -> 2 | 측정 안 함 |
| 잔여 소리 켜기 (`/api/audio/residual`) | 2.0 s, `residual.wav` 생성 | 측정 안 함 |
| 최대 GPU 7 메모리 (1초 샘플) | 33,837 MiB | 31,561 MiB (label 단계), 36,685 MiB (first_event 단계) |

트랙별 결과 (4개 모두 동일): `track_status=done`, 스템 `origin=separated`, `attempt=1`, `seed=null`.
`preview.mp4` 생성 확인.

## 오디오 시나리오에서 selsync로 전환 (docker)

| 항목 | 값 |
|---|---|
| Player Damage Impact -> selsync, 첫 생성 호출 (모델 로드 포함) | 34.2 s, `done`, `origin=generated`, seed 151971019 (시드 지정 없음 -> 무작위) |
| 같은 트랙 selsync 다시 (warm) | 7.0 s, seed 722401208 |

모델 로드 비용은 약 27초입니다 (34.2 s - 7.0 s). venv에서는 같은 호출이 `ModuleNotFoundError: No module named 'selva'`로 500.

## 무음 시나리오 (docker, selsync)

| 항목 | 값 |
|---|---|
| Generate 4트랙 | 24.1 s (label), 21.1 s (first_event). 모델은 이미 로드된 상태 |
| 트랙별 결과 | 4개 모두 `done`, `origin=generated`, `seed=42`, `attempt=null` |
| 한 트랙(Player Footsteps) 시드 없이 재생성 | 7.0 s, 새 seed 48728754 |
| 한 트랙 시드 42로 2회 재생성 | 7.04 s, 7.07 s |
| 시드 재현성 (두 파형 `np.corrcoef`) | 1.0 (통과 기준 0.99), 최대 절대 차이 0.0 (비트 동일), 길이 340800 샘플 |
| `preview.mp4` | 생성됨 |

## GPU 메모리 (docker, 한 컨테이너에 separation + generation)

| 구간 | 최대 (MiB) |
|---|---|
| label 단계 (분리 -> selsync 전환 -> 무음 생성 -> 재생성) | 31,561 |
| first_event 단계 (분리 + 무음 생성) | 36,685 |
| 전체 최대 | 36,685 (약 35.8 GiB) |

분리 첫 실행이 끝날 때 31,549 MiB였고, 생성 모델 첫 호출 뒤에는 31,561 MiB로 +12 MiB만 늘었습니다. 단계마다 최대치가 5 GB 가까이 다른 것은 분리 쪽 PyTorch 캐시 할당 차이로 보이며, 이유는 확인하지 않았습니다. 1초 간격 샘플이라 순간 최대는 더 높을 수 있습니다. 컨테이너 시작 후 대기 상태에서는 1 MiB. 종료 후 GPU 7은 1 MiB, 프로세스 없음.

## 확인 항목

| 항목 | 결과 |
|---|---|
| 반환 WAV 샘플레이트 | 48000 Hz, mono. 분리(venv, docker)와 생성(docker), `residual.wav`, A/B 파일 16개 모두 |
| `spans: []` 수락 | 실행하지 않음 (이 샘플은 모든 트랙에 2프레임 이상 이벤트가 있음. `source_plan`은 길이 0 구간을 걸러냄) |
| `has_audio` | import 직후 `/api/asset`에서 오디오 True, 무음 False 확인 |

## 설명 기본값 A/B (듣고 고르는 용도, 선택하지 않음)

파일: `work/task11/ab/{audio,silent}/<트랙>__{label,first_event}.wav`. audio는 분리 결과, silent는 selsync (seed 42).

| 트랙 | label | first_event |
|---|---|---|
| Player Damage Impact | Player Damage Impact | Sharp slash hit impact on player character |
| Player Footsteps | Player Footsteps | Sustained rustling and scuffing footstep sounds of player running |
| Background Wind Ambience | Background Wind Ambience | Continuous background wind ambience |
| Player Hurt Grunt | Player Hurt Grunt | Player vocal grunt of pain |

## 발생한 문제

1. `uv run --project gpu --locked ...`가 시작 전에 실패: `antlr4-python3-runtime` 빌드 중 uv 캐시(`/home/common/uv`) 읽기 권한 오류. `--no-sync`로 기존 venv를 그대로 써서 우회했습니다 (venv는 수정하지 않음).
2. `gpu/.venv`에 `selva`(git 의존성)가 없어 `/generate`가 500 (`real.py:75`, `import_module("selva.utils.eval_utils")`). 이 때문에 selsync 전환이 `failed` (`Audyssey generate failed (500)`)로 끝남. 코드와 venv를 고치지 않고, 이미지 `audyssey-tools:demo-1117`로 바꿔 재측정했습니다. 이미지에서는 생성 오류 없음.
3. 이 문서 외 코드 변경 없음. 측정용 스크립트와 로그는 `work/task11/`에 있습니다 (`tools.log`는 venv, `tools-docker.log`는 docker).
