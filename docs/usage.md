# 사용 가이드

V2A Inspect는 영상에서 SAM3 tracking과 Gemini agent로 sound timeline을 만들고, track마다 오디오를 생성하거나 원본 오디오에서 분리한 뒤 영상에 mix한다. 시연 당일 절차는 [demo-runbook.md](demo-runbook.md)를 따른다.

## 1. 실행 방법

필요한 서비스는 세 가지이다.

| 서비스 | 역할 |
|---|---|
| UI (`v2a ui`, port 8501) | 웹 화면과 API |
| inference server (port 8080) | SAM3 tracking (Hunyuan, Kokoro는 선택) |
| Audyssey tools server (port 8700, image `audyssey-tools:demo-1117`) | 분리(SAM Audio)와 SelVA w/ SelSync 생성 |

로컬 실행:

```bash
cp .env.example .env     # GEMINI_API_KEY, ELEVENLABS_API_KEY, V2A_INSPECT_AUDYSSEY_TOOLS_URL 등 설정
uv sync --extra ui && uv sync --project server
(cd web && npm ci && npm run build)    # Node 20.19+ 또는 22.12+
./scripts/start-local.sh               # UI와 inference server 실행
```

`start-local.sh`는 tools server를 띄우지 않는다. tools server는 별도로 실행하고 `V2A_INSPECT_AUDYSSEY_TOOLS_URL`, `V2A_INSPECT_AUDYSSEY_TOOL_TOKEN`을 맞춘다.

Compose 실행은 세 container를 GPU 1장에 올리는 `compose.demo.yaml`을 사용한다.

```bash
cp .env.demo.example .env.demo
docker compose -f compose.demo.yaml --env-file .env.demo up -d
uv run python scripts/demo_preflight.py --gpu <N> --warmup <video>
```

접속 주소는 `http://127.0.0.1:8501`이다. `V2A_INSPECT_UI_PASSWORD`가 설정되어 있으면 로그인 화면이 나타난다.

## 2. 화면 구성

- 상단 바: "Open video", "Save bundle", 상태 표시, 오디오 방식 선택 "Generate" / "Separate", 실행 버튼 ("Generate audio" / "Separate audio"). 원본 오디오 유무는 "Original audio" / "Silent video" 배지로 표시된다.
- Stage: 영상 재생과 선택한 track의 object mask 표시.
- Timeline: lane 하나가 track 하나이며 track 목록을 겸한다. lane마다 method 배지(`Separated`, `SelVA w/ SelSync`, `ElevenLabs`, `ElevenLabs TTS`), 재생 버튼, "Solo" 버튼이 있다.
- "Selected track" 패널: 선택한 track의 상태, method, seed, Description, 재생성 버튼, "Include rest of original audio" 스위치.

## 3. 작업 순서

1. **영상 열기.** "Open video"에서 "Analyze a new video"로 영상을 올린다. SAM3 tracking과 agent 분석이 실행된다.
2. **방식 선택.** 기본은 "Generate"이다. "Separate"는 원본 오디오가 있을 때만 켜지며, 없으면 "No original audio to separate."가 표시된다. 방식 전환은 저장된 mode만 바꾸고, track 라우팅은 다음 실행에서 바뀐다.
3. **실행.** "Generate audio" 또는 "Separate audio"를 누른다. 진행 상태는 상단 바에 표시된다.
4. **듣기.** lane의 재생 버튼으로 track 하나를, "Solo"로 해당 track만 재생한다. 재생 중 Solo를 바꾸면 현재 frame부터 다시 재생된다. 전체 미리보기는 상단 영상 재생으로 확인한다. Mute 버튼은 없다.
5. **track 재생성.** lane을 선택하고 "Selected track" 패널에서 Description을 고친 뒤 버튼을 누른다.
   - Generate 계열 track: "Regenerate" (원본 오디오가 있으면 "Re-separate"도 표시).
   - Separate track: "Re-separate" 또는 "Generate instead".
   - 재생성할 때마다 seed가 새로 정해지고 패널에 `Seed N`이 표시된다 (분리는 `Attempt N`). API는 `seed`를 직접 받는다 (`POST /api/audio/tracks/{id}/regenerate`).
   - ElevenLabs 계열(이벤트 단위) track은 이벤트별 Description, Spoken text와 "Regenerate"를 쓴다.
   - 실패하면 "Previous audio kept."가 표시되고 이전 오디오가 유지된다.
   - 저장하지 않은 timeline 편집이 있으면 재생성이 막히며, "Discard edits"로 편집을 버릴 수 있다.
6. **residual.** Separate에서 residual은 원본 오디오에서 분리된 stem을 뺀 나머지 소리이다. "Include rest of original audio" 스위치로 mix에 포함하거나 뺀다 (원본 오디오가 있을 때만 표시).
7. **다운로드.** "Open video" > "Advanced"의 "Download video (MP4)"로 mix된 영상을, "Download edited JSON"으로 편집된 asset JSON을 받는다.
8. **bundle 저장과 불러오기.** "Save bundle"은 project 전체를 zip으로 내려받는다. 다음에는 "Open video" > "Load a saved project"에서 zip을 올린다. 분석 없이 상태와 오디오가 복원된다.

## 4. 라우팅 규칙

Generate 모드에서 track별 모델은 다음 순서로 정해진다 (`audio_generation/routing.py`).

| 조건 | 모델 |
|---|---|
| speech track | ElevenLabs TTS (`V2A_INSPECT_TTS_PROVIDER=kokoro`로 Kokoro 선택 가능) |
| ambience track, 또는 source가 `scene_global` / `offscreen_unknown` | ElevenLabs SFX (t2a) |
| 그 외, agent가 영상 동기 SFX(v2a)를 고른 track | SelVA w/ SelSync (track당 stem 1개) |
| 그 외 | agent가 고른 모델 |

agent는 화면에 보이는 물체가 내고 타이밍이 동작에 맞아야 하는 소리(타격, 발소리, 휘두르기, 옷·갑옷 움직임 등)를 v2a로 고르고, ambience, 화면 밖 소리, 숨소리·기합 같은 비언어 음성만 t2a로 고른다 (`prompts/files/system/sound_timeline_agent.txt`).

Separate 모드에서는 모든 track이 분리(SAM Audio) 대상이다. ElevenLabs TTS는 현재 plan에서 기본 voice가 402(`paid_plan_required`)로 막혀 보류 중이다.

## 5. 주요 환경 변수

| 변수 | 용도 |
|---|---|
| `GEMINI_API_KEY` | 분석과 timeline agent (필수) |
| `V2A_INSPECT_LLM_LARGE_MODEL` | sound timeline agent 모델 (기본 `gemini-4-hydrogen`, 불가 시 `gemini-3.5-flash`) |
| `ELEVENLABS_API_KEY` | t2a와 TTS |
| `V2A_INSPECT_TTS_PROVIDER` | `elevenlabs`(기본) 또는 `kokoro` |
| `V2A_INSPECT_AUDYSSEY_TOOLS_URL` / `V2A_INSPECT_AUDYSSEY_TOOL_TOKEN` | tools server 주소와 Bearer token |
| `V2A_INSPECT_SELSYNC_SEED` | 첫 SelVA w/ SelSync 생성의 seed (기본 42) |
| `V2A_INSPECT_UI_PASSWORD` | UI 비밀번호 (비우면 인증 없음) |
| `V2A_SERVER_HUNYUAN_ENABLED` | inference server의 Hunyuan 적재 여부 (기본 true, demo compose는 false) |
| `V2A_SERVER_API_TOKEN` / `V2A_INSPECT_CLIENT_SERVER_TOKEN` | inference server Bearer token (서버/클라이언트 쌍) |
| `V2A_GPU_DEVICE` | compose에서 사용할 GPU index |

## 6. 자주 겪는 오류

| 증상 | 대처 |
|---|---|
| Generate/Separate가 실패하고 tools server 관련 짧은 오류가 표시됨 | tools server 실행 여부, URL, token을 확인한다. |
| "No original audio to separate." | 원본 오디오가 없는 영상이다. Generate를 사용한다. |
| 첫 Generate/Separate가 매우 느림 | tools 모델이 첫 호출에서 적재된다. `--warmup` preflight를 먼저 실행한다. |
| 재생성 버튼이 비활성화됨 | 패널에 표시된 이유를 확인한다. 저장하지 않은 편집은 "Discard edits"로 버린다. |
| speech track 생성이 402로 실패 | ElevenLabs plan 제한이다. Kokoro로 바꾸거나 해당 track을 제외한다. |
| Gemini model unavailable | `V2A_INSPECT_LLM_LARGE_MODEL=gemini-3.5-flash`로 바꾼다. |
| UI 화면이 비어 있음 | `web/dist`가 없다. `npm run build`를 실행한다. |
| 전체 stack 장애 | UI만 띄우고 "Load a saved project"로 저장한 bundle을 연다. |
