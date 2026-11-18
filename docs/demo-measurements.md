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

## LLM 비교: 사운드 타임라인 에이전트 (large 모델, 2026-11-18)

샘플(7.1초) 1개, 모델당 1회, thinking level low. 추적·장면 분석은 샘플에 있는 결과를 그대로 썼다. 물체: #2 플레이어, #3 싸우는 적, #1·#4 배경 생물(bbox 움직임과 정지 화면으로 확인).

| 모델 | 시간 | 토큰(입력/출력) | 원천/트랙/이벤트 | 물체 연결 |
|---|---|---|---|---|
| gemini-3.5-flash (이전 기본값) | 26초 | 67k / 1.1k | 3 / 3 / 3 | #2, #3 맞음 |
| gemini-3.1-pro-preview | 56초 | 50k / 0.8k | 1 / 1 / 1 | 없음 |
| gemini-3.8-flash | 244초 | 1,089k / 4.2k | 3 / 6 / 12 | #2, #3 맞음 |
| **gemini-4-hydrogen (선택)** | 48초 | 67k / 2.7k | 3 / 4 / 6 | #2, #3 맞음 |

- 3.5-flash는 트랙마다 전 구간 이벤트 1개라 타격·휘두르기를 잡지 못했다. 3.8-flash가 가장 풍부하지만 약 9배 느리다. 4-hydrogen은 비용이 3.5-flash와 비슷하면서 타격·휘두르기·발소리를 나눠 잡는다(시점은 대략적).
- 선택: `gemini-4-hydrogen`. 문서 없는 코드명 모델이라 시연 당일 쓸 수 없으면 `gemini-3.5-flash`로 바꾼다.
- 1회씩만 돌렸다. 시연 영상이 정해지면 선택 모델로 2~3회 더 돌려 안정성을 확인한다.

## 라우팅 비율과 Generate/Separate 실측 (2026-11-18, Task 7)

샘플(7.1초) 1회, `gemini-4-hydrogen`. GPU 6 한 장, 이미지 `audyssey-tools:demo-1117` 한 컨테이너(분리+생성). 이 표의 "에이전트"는 `agent_model`(에이전트 원래 판단), "라우팅"은 `route_tracks(mode="generate", has_audio=False)` 결과이다. 에이전트 실행은 39초.

| 트랙 | 종류 | 원천 | 에이전트 | 라우팅 |
|---|---|---|---|---|
| Enemy Attack Whoosh | sfx | visual_object (#3) | t2a | t2a |
| Player Footsteps | sfx | visual_object (#2) | v2a | selsync |
| Player Body Impact | sfx | visual_object (#2) | v2a | selsync |
| Arena Wind Ambience | ambience | scene_global | t2a | t2a |

- 원천 3개 중 2개가 물체와 연결됨(#2 플레이어, #3 적). 에이전트가 v2a로 고른 트랙은 2개(둘 다 플레이어 접촉음)이고 모두 selsync로 갔다. 규칙(앰비언스·scene_global·offscreen → t2a)이 바꾼 트랙은 이번엔 없다. 대사 트랙이 없어 tts 경로는 타지 않았다.

**Generate (무음 사본)**: `has_audio=false`, `audio_mode=generate` 확인, 전체 61초, 오류 없음, GPU 6 최대 약 5.9 GB.

| 트랙 | 방식 | 상태 | 출처 | 완료 시점(시작 후) |
|---|---|---|---|---|
| Player Footsteps | selsync | 성공 | generated | 약 47초 (첫 호출, 모델 로딩 포함) |
| Player Body Impact | selsync | 성공 | generated | 약 51초 |
| Enemy Attack Whoosh | t2a (이벤트 3개) | 성공 | mixed | 약 55~57초 |
| Arena Wind Ambience | t2a (이벤트 1개) | 성공 | mixed | 약 54~58초 |

ElevenLabs 결과 이벤트 WAV 4개는 모두 RIFF/PCM 16bit 44.1 kHz 스테레오, 무음 아님(최대 -0.1 ~ -14.6 dBFS). 원본 응답 바이트는 가로채지 않았다(추가 유료 호출 방지).

**Separate (원음 영상)**: 같은 자산을 가져와 `POST /api/audio/mode separate` → 전체 생성. 전체 164초, GPU 6 최대 약 36.6 GB(SAM Audio). 4개 트랙 모두 `generation_model=separate`, `agent_model` 불변(t2a/v2a/v2a/t2a), 출처 `separated`. 모드 전환 직후에는 트랙 방식이 그대로이고 다음 생성에서 바뀌는 것도 확인했다.

**Generate instead (분리 상태에서 한 트랙)**:
- Enemy Attack Whoosh (t2a 라우팅): 이벤트 단위 ElevenLabs로 3개 이벤트 재생성, 10초 이내, `generation_model` t2a, 출처 mixed, 이벤트 3개 생김. 다른 3개 트랙은 `separated` 그대로(파일도 그대로).
- Player Footsteps (selsync 라우팅): 트랙 단위 selsync, 10초 이내, `generation_model` selsync, 출처 generated, 새 seed. 나머지 트랙 유지.
- 두 경우 모두 `agent_model`은 처음 값 그대로.

**TTS 직접 호출 (`generate_speech_elevenlabs("Get back here!")`) — 실패**: 402 `paid_plan_required` ("Free users cannot use library voices via the API"). 기본 voice id `21m00Tcm4TlvDq8ikWAM`(Rachel)는 현재 ElevenLabs 계정(무료 플랜)에서 API로 못 쓴다. RIFF 확인은 못 했다(승인된 호출 1회를 이 실패로 사용). 대사(tts) 트랙이 샘플에 없어 Generate 실측엔 영향이 없지만, 시연에서 대사가 나오면 막힌다. 유료 플랜이거나 `V2A_INSPECT_ELEVENLABS_TTS_VOICE_ID`를 계정이 쓸 수 있는 voice로 바꿔야 한다.

**화면(1280x720)**: `docs/mockups/shots/impl-mode-1280-silent.png`, `impl-mode-1280-audio.png`. 상단 바는 한 줄(Open video / 상태 / Generate·Separate / Generate audio), 가로 스크롤 없음. 무음에서는 Separate가 비활성이고 컨트롤 아래에 "No original audio to separate." 문구가 작게 붙어 바 높이가 55px(원음 49px)이다.

**발생한 문제**
1. TTS 402 (위). 실패 시 0바이트 `tts.wav`가 남는다(`open(out_path, "wb")`가 API 호출 오류보다 먼저 실행됨). 소스는 고치지 않았다.
2. `model_dump_json`으로 저장한 자산은 계산 필드(`start_frame_index` 등)가 들어 있어 `/api/asset/import`(extra 금지)가 400을 냈다. 측정용으로 해당 키만 제거해 가져왔다.
3. 설치된 Playwright가 요구하는 chromium(1255)이 캐시에 없어(1248만 있음) `executable_path`로 1248을 썼다.
