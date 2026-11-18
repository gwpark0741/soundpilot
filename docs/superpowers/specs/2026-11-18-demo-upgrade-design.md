# 시연 고도화 설계: 분리·SelSync 생성·트랙 단위 재생성·패키징 (2026-11-18)

## 1. 목적

다음 주 대회 시연에서 v2a_inspect로 최고 품질을 보여준다. 대회에는 생성 기반으로 제출했다.

시연 흐름은 두 가지다. 둘 다 보여준다.

1. **무음 영상:** SAM3 물체 추적 → 멀티트랙 효과음 생성 → 트랙별로 듣기 → 마음에 안 드는 트랙만 다시 생성
2. **원음 있는 영상:** SAM3 물체 추적 → 원음에서 트랙별 분리 → 트랙별로 듣기 → 나쁜 트랙만 다시 분리하거나 생성으로 교체

분리를 넣는 이유: 무음 영상의 생성 품질이 아직 충분하지 않다. 원음이 있으면 분리로 깨끗한 stem을 얻고, 분리가 나쁘거나 원음이 없을 때 생성으로 채운다. 생성이 빠지는 것이 아니라 분리가 앞에 붙는 구조로 설명한다.

Audyssey를 대신 시연하지 않는 이유: SAM3로 물체를 추적하는 장면이 v2a_inspect에만 있고, 이 장면이 시연의 중심이다.

## 2. 목표, 비목표, 반목표

**목표**
- 트랙 단위 분리(SAM Audio)와 트랙 단위 생성(SelVA w/ SelSync)
- 트랙별 듣기와 트랙별 다시 분리 / 생성으로 바꾸기 / 다시 생성
- 기존 이벤트 재생성의 결함 보완(P0)
- LLM을 상위 모델로 교체
- 시연용 UI 개선(재생성 조작을 눈에 띄는 곳으로, 트랙 ↔ 물체 연결 표시)
- 재현성과 패키징: sv3-a100 주 시연, RunPod 예비

**비목표**
- 에이전트 기능 확장(자연어 편집, 분리↔생성 자동 루프)
- Audyssey의 Judge 기반 재시도, 플래너 교체
- 새 모델 학습이나 체크포인트 탐색. Audyssey에서 검증된 조합만 쓴다
- Hunyuan 코드 삭제(기본 라우팅에서만 뺀다)

**반목표 (돌아가도 실패로 보는 것)**
- 시연 중 같은 버튼을 눌렀는데 결과를 다시 만들 수 없다(시드·버전 미기록)
- 생성 실패가 더미 소리나 무음으로 조용히 넘어간다
- 네트워크나 GPU 문제 하나로 시연 전체가 멈춘다(비상용 묶음 없음)
- UI가 개발용 화면처럼 보이고, 재생성하려면 스크롤을 한참 내려야 한다

## 3. 제약

- 기간: 1주. 시연 영상은 아직 정하지 않았다(원음 1개, 무음 1개를 고를 예정). **둘 다 15초 이하로 고른다**(사용자 결정, 2026-11-18). SelSync가 한 번에 생성하는 길이 안이다.
- 주 시연: sv3-a100(A100 80GB)에 원격으로 붙는다. 예비: RunPod.
- GPU 사용 규칙: 다른 사용자 프로세스가 있는 GPU는 쓰지 않는다. 우리 GPU 총수 상한은 사용자가 정한다. 시연 주간에는 GPU 1장을 시연용으로 잡는다.
- 가중치는 `/home/common/huggingface` 공용 캐시를 경로로 참조한다. 복사하지 않는다.
- 유료 API(Gemini, ElevenLabs) 비교 실험은 실행 전에 사용자 승인을 받는다.
- Audyssey는 다른 팀 저장소다(`capstone-audissey-fall-2026`). v2a_inspect에서 Audyssey 코드를 고치지 않고, 도구 서버 이미지와 HTTP API만 쓴다.

## 4. 접근 방식

### 4.1 SelSync를 어떤 단위로 호출할지 (선택: A)

| 안 | 내용 | 장점 | 단점 |
|---|---|---|---|
| **A. 트랙 단위, 영상 전체** | 트랙마다 영상 전체 + 트랙 설명으로 1회 생성 → 영상 길이 stem | Audyssey에서 검증한 방식 그대로. 여러 물체 중 대상만 고르는 SelSync 강점이 드러남. 같은 소리의 음색이 일관됨 | 소리 시점은 모델이 정함. 이벤트 구간은 참고용 |
| B. 이벤트 단위, 구간 자르기 | 지금 Hunyuan처럼 구간마다 생성 | 기존 이벤트 재생성 UI 재사용 | 8초 학습 분포 밖의 짧은 구간, 같은 소리도 이벤트마다 음색이 달라짐 |
| C. 트랙 생성 후 이벤트 구간만 교체 | A + 구간 이어 붙이기 | 이벤트 단위 조작 유지 | 이음새 처리가 복잡하고 시연 이득이 작음 |

사용자가 A를 골랐다.

### 4.2 분리·생성 모델을 어떻게 붙일지 (선택: Audyssey 도구 서버를 HTTP로)

| 안 | 장점 | 단점 |
|---|---|---|
| **Audyssey tools 이미지를 그대로 띄우고 `/separate`, `/generate`를 HTTP로 호출** | 검증된 코드·이미지 재사용, v2a_inspect 쪽 코드는 클라이언트뿐 | 서비스가 하나 늘어남, Bearer 토큰 관리 |
| SelVA·SAM Audio 코드를 v2a_inspect 서버로 이식 | 서비스 수 그대로 | 의존성 충돌(torch, numpy), 이식 비용, 검증 다시 |
| Audyssey 자체를 시연 | 구현 거의 없음 | SAM3 추적 장면이 없음. 사용자가 기각 |

첫 번째 안으로 간다(제안, 사용자 이의 없음).

## 5. 설계

### 5.1 P0: 기존 기능 보완

1. **실패 원인 표시.** `generate_audio_for_item`이 예외를 삼키고 `None`을 돌려줘서 UI에는 "No audio file was generated."만 뜬다. 예외 메시지를 `store.set_error`까지 올려 UI에 그대로 보인다.
2. **ElevenLabs 키가 없으면 오류.** 지금은 더미 소리를 만들고 조용히 넘어간다(`generate_sfx_elevenlabs`). 키가 없으면 오류를 낸다.
3. **재생성 시 편집 손실 방지.** 저장하지 않은 타임라인 편집이 있는 상태에서 이벤트 재생성을 누르면, 서버에는 설명만 가고 화면을 서버 상태로 맞추면서 다른 편집이 사라진다. 재생성 전에 편집을 먼저 서버에 반영하거나, 편집이 남아 있으면 재생성 버튼을 막고 이유를 표시한다(빌더 판단, 더 짧은 쪽).
4. Hunyuan 시드 42 고정은 고치지 않는다. Hunyuan은 기본 라우팅에서 빠지므로 시연과 무관하다. 시드는 5.2의 SelSync 경로에서 다룬다.

### 5.2 P1: 분리와 SelSync 생성

**원본 보존.** 지금 작업용 영상(`source_path`)은 오디오를 지운 1280×720 레터박스 영상이다(`preprocessing/video.py` `normalize_video`). 원본 업로드는 `work/uploads/`에 있지만 `VideoAsset`이 가리키지 않는다. 그래서 `VideoAsset`에 두 필드를 추가한다.
- `raw_video_path`: 원본 업로드 영상. SelSync `/generate`에는 이것을 보낸다(Audyssey가 원본 영상으로 검증했고, 레터박스는 모델이 보는 화면을 바꾼다).
- `original_audio_path`: 업로드 시 뽑은 원본 오디오(모노 WAV, Audyssey `/separate` 입력 형식). 무음이면 `None`.
- 비상용 묶음에는 두 파일을 함께 넣는다. 묶음에서 불러온 뒤에도 다시 분리할 수 있어야 한다.

**원음 판단.** 업로드 시 `VideoAsset`에 `has_audio: bool`을 기록한다. 오디오 스트림이 없거나 최대 진폭이 −60dBFS 미만이면 무음(Audyssey와 같은 기준).

**트랙 라우팅.** `SoundTrack.generation_model`에 `"separate"`와 `"selsync"`를 추가한다. 사운드 타임라인 에이전트가 끝난 뒤 결정적으로 덮어쓴다(프롬프트는 바꾸지 않는다). 에이전트가 쓰는 스키마(`tools/sound_timeline/schemas.py`)의 선택지는 `t2a|v2a|tts` 그대로 둔다. `SoundTrack.migrate_legacy_values`가 speech를 무조건 `tts`로 바꾸는 부분은 `separate`를 허용하도록 고친다.

| 입력 | sfx·ambience 트랙 | speech 트랙 |
|---|---|---|
| 원음 있음 | `separate` | `separate` (원래 목소리 유지) |
| 무음 | `selsync` | `tts` (기존 Kokoro, 이벤트 단위) |

사용자는 UI에서 트랙별로 바꿀 수 있다. 선택지는 `separate`(원음 있을 때만), `selsync`, `t2a`(ElevenLabs), speech는 `tts`.

**트랙 단위 처리 (`separate`, `selsync`).**
- `separate`: 원본 오디오를 모노 WAV로 뽑아(형식은 Audyssey `/separate` 입력과 맞춤) Audyssey `/separate`에 `mono`, `plan`, `attempt`를 보낸다. 시각 힌트(video)는 보내지 않는다(Audyssey 기본값과 같음).
- `selsync`: 원본 영상과 `plan`, `seed`를 Audyssey `/generate`에 보낸다.
- `plan`은 Audyssey `SourcePlan` JSON이다. 대응: `name`=트랙 label, `kind`=트랙 종류(`speech`→`dialogue`), `description`=트랙 설명(아래), `spans`=그 트랙 이벤트 구간(초). `/generate`는 spans를 쓰지 않는다.
- 트랙 설명: 새 필드 `SoundTrack.description: str | None`. 사용자가 고친 설명은 여기에 저장한다. 기본값 후보는 `track.label`(짧은 명사구, SAM Audio 입력 형태에 가까움)과 첫 이벤트 description(이벤트 생성용 문장) 두 가지다. P1-a에서 샘플로 둘 다 분리·생성해 사용자가 듣고 **`label`로 정했다**(2026-11-18). `plan.py`가 붙이는 `"{source}, [{label}] "` 접두어는 트랙 경로에 쓰지 않는다.
- 결과는 영상 길이 stem 1개다. 이 트랙의 이벤트별 WAV는 만들지 않는다.
- 이벤트 단위 경로(`tts`, `t2a`, `v2a`)는 지금 그대로 이벤트 WAV를 만들고 트랙 stem으로 믹스한다.
- **한 번의 "Generate Audio"가 두 경로를 모두 돈다.** 트랙마다 라우팅에 따라 트랙 경로 또는 이벤트 경로로 간다. 지금 파이프라인은 이벤트 WAV가 하나도 없으면 실패하거나 일찍 끝나므로(`ui/pipeline.py` `_rebuild_audio_outputs`, `run_audio_generation_pipeline`) stem만 있는 영상도 되도록 고친다.
- **실패 처리.** 트랙 하나가 실패해도(도구 서버 다운, 401, 5xx, 시간 초과) 나머지 트랙은 끝까지 간다. 실패한 트랙은 이전 stem을 유지하고, 트랙별 상태·오류(`status`, `error`)를 기록해 UI에서 그 트랙 옆에 보인다. 전역 오류는 전체가 실패했을 때만. 도구 서버 호출 시간 제한은 600초(첫 모델 로딩 2~3분, 도구 서버는 요청을 하나씩 처리).
- **영상 길이.** SelSync는 15초까지 한 번에 생성하고, 넘으면 8초 창을 1초씩 겹쳐 이어 붙인다(Audyssey `generation/real.py`). "같은 소리의 음색이 일관됨"은 15초 이하에서만 성립한다.

**시드와 시도 기록.** `SoundTrackAudioArtifact`에 `origin: "mixed" | "separated" | "generated"`, `seed: int | None`, `attempt: int | None`, `description: str | None`를 추가한다.
- `selsync` 첫 생성 시드는 설정값(기본 42), 다시 생성할 때마다 새 무작위 시드. 시드는 UI에 보이고, 사용자가 시드를 직접 넣어 같은 결과를 다시 만들 수 있다.
- `separate` 다시 분리는 `attempt`를 1 올린다(Audyssey는 attempt를 시드로 쓴다).

**트랙 API.** `POST /api/audio/tracks/{sound_track_id}/regenerate` (form: `method`, `description?`, `seed?`). 처리 뒤 트랙 stem 교체 → 미리보기 영상 다시 믹스. 실행 중이면 409. 기존 이벤트 재생성 API는 이벤트 단위 트랙용으로 남긴다.

**나머지 소리(residual).** 원음 있는 영상에서만, 원본 − 분리 stem 합. 트랙을 생성으로 바꿔도 그 트랙의 마지막 분리 stem은 계속 뺀다(Audyssey: keep과 무관하게 분리 stem을 뺌). 그래야 교체한 소리가 residual로 되살아나지 않는다. 그래서 트랙을 생성으로 바꿔도 분리 stem 파일은 지우지 않고 보관한다. 트랙 목록에 한 줄로 두고 기본은 꺼짐. 켜면 최종 믹스에 더한다.

**최종 믹스.** 켜진 트랙 stem(+ 켜진 경우 residual)을 합쳐 영상에 넣는다. 원본 오디오는 쓰지 않는다(`keep_original_audio=False`, 지금과 같음). 모든 stem과 믹스는 48kHz 모노다. 원본이 스테레오여도 미리보기는 모노로 둔다.

**메모리.** v2a_inspect 추론 서버는 시작할 때 Hunyuan XL을 올린다(`server/.../app.py` lifespan). 라우팅에서 빼는 것만으로는 메모리가 비지 않으므로, Hunyuan을 올리지 않는 설정(`V2A_SERVER_HUNYUAN_ENABLED=false`)을 추가하고 시연에서는 끈다.

**표기.** UI 배지와 문서에서 생성 방식은 "SelVA w/ SelSync"로 쓴다. 실제 가중치는 `sogang-audyssey/internal-model`(생성기 `S_O_s43` 1K + 인코더 `S_O`)이다.

**설정.** `V2A_INSPECT_AUDYSSEY_TOOLS_URL`, `V2A_INSPECT_AUDYSSEY_TOOL_TOKEN`, `V2A_INSPECT_SELSYNC_SEED`(기본 42). 도구 서버는 `--tool separation --tool generation --impl real`로 띄운다(filtering, grounding은 띄우지 않음). 이미지는 태그가 아닌 digest로 고정한다.

### 5.3 P1: LLM 상향

- 지금: 장면 분석과 사운드 타임라인 에이전트 모두 `gemini-3.5-flash`, 씨앗 추출은 `gemini-3.1-flash-lite`.
- 후보(2026-11-18, 이 키로 조회): `gemini-3.1-pro-preview`, `gemini-3.8-flash`, `gemini-4-*` 중 1개(문서 설명 없는 코드명).
- 방법: 샘플 영상과 시연 후보 영상 1개로 후보별 분석을 돌려, 트랙·이벤트 구성 품질(사용자가 읽고 판단), 걸린 시간, 오류를 비교한다. **유료 호출이라 실행 전 승인.**
- 고른 모델은 `-latest` 별칭이 아닌 고정 ID로 `.env`에 적는다. 코드명 모델은 비교 결과가 확실히 좋을 때만 쓴다.

### 5.4 P2: UI (기능 통합 후)

사용자 지적: 시연 중 재생성 조작이 페이지 아래로 한참 내려가야 보인다. 추가한 기능들이 개발용 화면처럼 약해 보인다. 기준 리뷰([2026-11-18-ui-review.md](2026-11-18-ui-review.md), 판정 Block)가 두 지적을 모두 확인했다.

**화면 구성 — 확정 시안 [docs/mockups/2026-11-18-demo-ui.html](../../mockups/2026-11-18-demo-ui.html) (A안, 2026-11-18 사용자 승인).** 시안이 이 절보다 우선한다. 스크린샷은 `docs/mockups/shots/`.

```
┌ 상단 바: V2A Inspect [Original audio|Silent video] | [Open video] | 상태(role=status, 진행 막대) | [Generate audio ★] ┐
├ 영상 + SAM3 마스크 + "Highlighting <물체>" 칩 ─────────┬ Selected track (선택한 트랙 하나) ─────┤
│  재생 / 슬라이더 / 시간                                 │ 이름 · 방식 배지 · 상태 · Seed/Attempt │
│                                                         │ Description [..............]           │
│                                                         │ [Re-separate] [Generate instead] 또는  │
│                                                         │ [Regenerate]                           │
│                                                         │ Include rest of original audio (원음만) │
├ 타임라인: 레인 머리(이름·배지·Play·Solo·Done이 아닐 때 상태) + 파형 ──────────────────────────┤
```

- 타임라인 레인 머리가 트랙 목록이다. 별도 트랙 카드 목록은 두지 않는다(중복 제거, 사용자 지적). 레인 아무 곳이나 누르면 선택되고, 선택하면 레인 막대·영상 마스크·오른쪽 패널 테두리가 그 물체 색으로 함께 바뀐다(`SoundTrack.sound_source_id` → `SoundSource.visual_object_id`).
- 오른쪽 패널은 선택한 트랙 하나의 상세와 버튼만. 분리 트랙은 [Re-separate][Generate instead], 생성 트랙은 [Regenerate]. 시드는 읽기 전용 표시만 하고 입력칸은 없다. Mute는 없고 Solo만 둔다.
- 지금 화면의 Generated Audio 목록, 이벤트 편집 창의 재생성, 장면·추적 그룹, At Frame 인스펙터, Load bundle 버튼은 시연 화면에서 뺀다. 이벤트 단위 트랙(TTS, ElevenLabs)의 이벤트 재생성은 선택 패널에서 이벤트를 고르는 방식으로 남긴다(구현 계획에서 정함).
- [Open video]는 업로드 + 묶음 불러오기를 한 창(네이티브 `<dialog>`)에서 처리하고, Work directory·서버 URL·임계값은 그 창의 "Advanced" 접기 안에 둔다. 화면당 주 버튼은 Generate audio 하나.
- 글자 위계: 트랙 이름 0.9375rem/600, 상태·주요 버튼·설명 0.8125rem, 배지·Play/Solo·라벨·Seed 0.75rem 흐리게. 모서리 컨트롤 6px·패널 12px. 누름 0.96/150ms, hover는 `(hover: hover)`에서만, 모션 줄이기 존중.
- 1280×720·1440×900에서 페이지와 패널 모두 스크롤 없음. 타임라인 최대 38vh(트랙이 많으면 안쪽 스크롤, 눈금 고정). ≤900px에서는 영상 → 선택 패널 → 타임라인으로 쌓는다.

**리뷰 반영 범위.** 시연은 노트북 데스크톱 폭에서 하므로 시연 흐름에 걸리는 것을 먼저 고친다.
- 반영: F1, F9(구성 변경으로 해결), F6, F15(오류·상태를 컨트롤 옆에), F4(네이티브 `<dialog>`), F11, F12, F13(시각 정돈), F7, F8, F10(값싼 접근성), F3 일부(막대를 버튼으로, Enter로 편집), F2·F5(CSS 몇 줄), LOW 묶음 문구.
- 범위 밖: 타임라인 막대의 키보드 이동·크기 조절(F3 나머지).

**확인 방법.** 시안은 better-interface 점검을 세 번 거쳐 Approve(HIGH 없음, 글자 대비 최소 6.3:1). 구현 후 1440×900, 1280×720에서 스크린샷으로 스크롤 없이 트랙 듣기·재생성이 되는지, 320폭에서 잘리는 컨트롤이 없는지 확인한다.

### 5.5 재현성과 패키징

**버전 고정**
- 시연용 git 태그(`demo-<날짜>`)와 같은 태그의 이미지: `v2a-inspect`(UI), `v2a-inspect-server`(SAM3, Kokoro), Audyssey `audyssey-tools`.
- **Audyssey tools 이미지는 `audyssey-tools:demo-1117`(image ID `sha256:cc603e8ffc46c26da15aac051996271db8c06bdffe1134c1bdf7b6291d08694a`, 2026-11-13 빌드)로 고정한다**(사용자 결정, 2026-11-18). 이 이미지로 분리·SelSync 생성을 끝까지 실측했다([demo-measurements.md](../../demo-measurements.md)). 이 서버의 Audyssey `gpu/.venv`에는 `selva`가 없어 생성이 안 되므로 시연에 쓰지 않는다. RunPod 예비용으로는 같은 이미지를 레지스트리에 올리거나 내보내야 한다(로컬 빌드라 레지스트리 digest가 없다).
- 가중치는 HF 스냅샷 해시로 고정하고 `HF_HUB_OFFLINE=1`로 실행.
- LLM 모델 ID, thinking level, 시드 기본값을 `.env.demo` 한 파일에 모은다.

**주 시연: sv3-a100**
- `docker compose`(시연용 compose 파일) 한 번으로 UI, 추론 서버, Audyssey tools를 GPU 1장에 띄운다. 공용 HF 캐시를 읽기 전용으로 마운트한다.
- UI는 `127.0.0.1`에만 열고, 현장 노트북에서는 SSH 포트 포워딩으로 붙는다.
- GPU 메모리 예상: SAM3 + SAM Audio(약 30GB) + SelSync(약 6.7GB). 70GB 안쪽으로 예상하지만 실측으로 확인한다.

**예비: RunPod**
- Pod 안에서는 Docker Compose를 못 쓴다. Audyssey tools Pod(기존 템플릿)와 v2a_inspect 추론 서버 Pod를 따로 띄우고, 노트북에서 UI 컨테이너만 띄워 두 Pod 주소를 가리킨다.
- v2a_inspect 추론 서버에는 지금 인증이 없다. RunPod 공개 프록시에 열려면 Bearer 토큰 검사를 추가한다.
- 시연 전에 RunPod에서 한 번 끝까지 돌려 본다(유료, 승인 필요).

**시연 직전 점검·워밍업 스크립트**
- 서비스 health, GPU 메모리, API 키 유효성, 가중치 존재를 확인한다.
- 원음 영상과 무음 영상을 한 번씩 끝까지 돌려 모델을 올려 두고, 걸린 시간을 출력한다.

**같은 시드 재현의 기준.** 비트 단위 일치는 보장하지 않는다. Audyssey에서 torch 버전만 바꿔도 같은 시드의 파형 상관이 0.34~0.79로 떨어졌다(`audyssey/docs/status.md`). 기준은 "고정한 이미지, 같은 GPU 종류에서 같은 시드로 두 번 생성 → 파형 상관 0.99 이상"으로 두고 실측한다. 못 미치면 재현은 "시드를 기록하고, 결과 stem을 묶음에 보관한다"로 정의를 낮추고 문서에 적는다.

**비상용 묶음**
- 시연 영상마다 원본 영상, 원본 오디오, 분석 결과, 트랙 stem(분리 stem 보관본 포함), 미리보기 영상을 zip 하나로 묶는다.
- 지금 Import asset은 영상과 추적 경로만 다시 잡고, JSON 안의 오디오 산출물 절대경로는 깨진다. 그래서 묶음 내보내기/불러오기를 추가한다. 불러온 뒤 듣기·다시 분리·다시 생성이 모두 되어야 한다.

## 6. 우선순위와 순서

| 순서 | 내용 | 끝났다는 기준 |
|---|---|---|
| P0 | 5.1 | 생성 실패 시 실제 원인이 UI에 보임. 키 없을 때 더미 없음. 편집 손실 재현 불가 |
| P1-a | 원본 보존, 원음 판단, 라우팅, Audyssey 클라이언트, 트랙 API, 트랙별 실패 처리, residual, Hunyuan 끄기 | 샘플(원음)과 무음 영상으로 분리·생성·다시 하기가 API로 동작. 같은 시드 2회 상관 실측. 트랙 설명 기본값 결정 |
| P1-b | LLM 비교·상향(승인 후) | 고른 모델 ID가 `.env.demo`에 고정 |
| P2 | UI | 1440×900 한 화면에서 트랙 듣기·재생성까지 스크롤 없이. 트랙 선택 시 물체 강조 |
| P3 | 패키징, 점검·워밍업, 비상용 묶음 | 깨끗한 클론에서 태그 체크아웃 → compose up → 점검 스크립트 통과 |
| P4 | 리허설 | sv3에서 두 시연 영상 끝까지 2회. RunPod 1회 |

## 7. 빌더에게 맡기는 것

- 5.1-3의 두 방식 중 더 짧은 쪽
- 모노 WAV 추출 형식(Audyssey `/separate` 입력과 맞추기만 하면 됨), ffmpeg 호출 방식
- 시연용 compose 파일 이름과 위치, 스크립트 이름
- 테스트: 핵심 로직(라우팅 덮어쓰기, SourcePlan 변환, residual 계산)마다 짧은 테스트 하나

## 8. 1차 구현(P0·P1-a)에서 다음 계획으로 넘긴 항목

브랜치 전체 리뷰(2026-11-18)에서 분류한 것. 자세한 경위는 git 기록에 있다.

- **P2(UI)에서:**
  - `track_status`를 트랙 옆에 표시한다. 모든 트랙 호출이 실패해도 이전 stem이 있으면 실행은 complete로 끝나므로, 실패는 트랙 상태로만 보인다. 이벤트 단위 트랙도 이제 `track_status`가 생긴다.
  - 트랙별 진행 단계(i/n) 표시. 트랙이 "done"이 된 뒤 미리보기 다시 믹스가 실패하는 경우의 표시.
  - 트랙 단위로 바꾼 트랙은 이벤트 WAV가 지워진다. 이벤트 단위로 되돌리는 UI가 생기면 다시 생성해야 한다.
  - 이벤트 재생성 창: 이유 문구의 `aria-describedby`, 편집+실행 중 우선순위 테스트(F4 네이티브 `<dialog>`로 바꿀 때 함께).
  - 저장 안 한 편집이 있는 상태에서 `/api/audio/generate`에 자산을 올리면 서버의 `separated_stem_paths` 등이 클라이언트 초안으로 덮인다. 트랙 재생성을 UI에 노출할 때 막는다.
- **P3(패키징)에서:**
  - `work/`를 `.gitignore`에 넣는다.
  - 비상용 묶음에 분리 stem 보관본을 포함하고, 파일이 없을 때 residual이 원본 복사본이 되지 않게 한다. 쓰지 않는 이전 stem 파일 정리.
  - `.env.demo`에 `V2A_SERVER_HUNYUAN_ENABLED=false`. 추론 서버 테스트 전체(`server/tests`)를 서버 이미지 안에서 한 번 돌린다(이 호스트에는 서버 venv가 없다).
- **실측 때 확인 못 한 것:** Audyssey가 `spans: []`를 받는지(샘플에 이벤트 없는 트랙이 없었다). 시연 영상이 정해지면 확인한다.

## 9. 2차 구현(P2 UI)에서 남긴 항목 (2026-11-18)

- **물체 강조 데이터 문제(사용자 결정 필요):** 샘플에서는 씨앗 추출이 플레이어와 적을 같은 프롬프트("black humanoid character", 라벨 "player character")로 묶었다. 그래서 SAM3가 네 물체를 같은 라벨로 추적했고, 사운드 소스의 `visual_object_id`도 비어 있다. 라벨 대체 경로가 플레이어 트랙을 고르면 적까지 함께 강조된다. UI로는 구분할 수 없다.
- 이벤트 단위 트랙(TTS 등)을 고르면 1280×720에서 선택 패널이 안쪽에서 스크롤될 수 있다(시안의 `overflow-y:auto` 대체). 시연 트랙은 분리/생성이라 해당 없음.
- 추적 창 응답에 모든 추적의 마스크가 들어가 크기가 약 5배(샘플 112KB→573KB). 느린 링크에서 문제가 되면 선택 트랙 마스크만 받도록 바꾼다.
- 브라우저에서 확인 못 한 것: 15초 근처 긴 영상에서 추적 창 경계 재요청(단위 테스트만), 생성 중 상태 실제 화면.
- 작은 정리 항목: 패널 오류가 선택이 바뀐 뒤 도착하면 새 트랙에 보일 수 있음, Reset + rerun 뒤 열기 창이 열린 채 남음, MP4 다운로드 파일명, 쓰지 않는 백엔드 라우트(`/api/rows/current-frame`, `/api/frames/tracking-overlay`, `DELETE /api/audio/events`).
