# 시연 고도화 3차: 생성·분리 방식 선택과 에이전트 라우팅 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 분석이 끝난 뒤 사용자가 생성(기본)·분리를 고르고, 생성은 에이전트 라우팅대로 v2a→SelVA w/ SelSync, t2a→ElevenLabs SFX, tts→ElevenLabs TTS로 만들며, 분리한 트랙은 그 트랙의 에이전트 라우팅대로 생성으로 바꿀 수 있게 한다.

**Architecture:** 트랙에 에이전트가 고른 모델(`agent_model`)을 보관하고, 자산에 방식(`audio_mode`)을 둔다. 한 함수 `route_tracks`가 방식·원음 유무·t2a 규칙으로 트랙의 실제 방식(`generation_model`)을 정한다. 기존 트랙 단위(분리·SelSync)와 이벤트 단위(ElevenLabs SFX·TTS) 경로를 그대로 쓰고, 트랙 재생성 API가 이벤트 단위 트랙도 처리하게 넓힌다. 프런트는 상단 바에 방식 선택을 더하고 패널 버튼 규칙만 바꾼다.

**Tech Stack:** Python 3.13, FastAPI, Pydantic v2, elevenlabs SDK 2.51(설치됨), unittest; React 19 + TS, `node --test`.

**Spec:** `docs/superpowers/specs/2026-11-18-demo-upgrade-design.md` §10 (이 계획의 기준), §5.2·§5.4(기존 구조).

## Global Constraints

- 방식 값: `audio_mode: Literal["generate", "separate"]`, 기본 `"generate"`. 원음이 없으면 `"separate"`는 422 `"No original audio to separate."`.
- 라우팅 대응(생성): v2a → `selsync`(트랙 단위), t2a → `t2a`(ElevenLabs SFX, 이벤트 단위), tts → `tts`(이벤트 단위). 분리: 모든 트랙 `separate`(배경음 포함).
- t2a 규칙(생성): `track_type == "ambience"` → t2a; 비대사 트랙의 원천 `source_type in {"scene_global", "offscreen_unknown"}` → t2a; 대사(`speech`)는 항상 tts.
- 에이전트 판단 보관: `SoundTrack.agent_model: Literal["t2a", "v2a", "tts"] | None`. 처음 라우팅할 때 에이전트가 쓴 `generation_model`을 복사하고, 이후 방식 전환에 덮어쓰지 않는다. 예전 자산의 `agent_model`이 없으면 그때의 `generation_model`(t2a/v2a/tts일 때)에서 채운다; 그 외(`separate`/`selsync`)면 트랙 종류로 추정(speech→tts, ambience→t2a, 나머지→v2a).
- TTS 공급자 설정 `V2A_INSPECT_TTS_PROVIDER: "elevenlabs" | "kokoro"`, 기본 `"elevenlabs"`. ElevenLabs TTS 설정: `V2A_INSPECT_ELEVENLABS_TTS_VOICE_ID`(기본 `21m00Tcm4TlvDq8ikWAM`), `V2A_INSPECT_ELEVENLABS_TTS_MODEL`(기본 `eleven_multilingual_v2`), 출력 `wav_48000`. 키가 없으면 오류(조용한 대체 금지, 1차 P0 규칙).
- 나머지 소리 스위치 기본 꺼짐 유지.
- UI 문구(영어): 방식 선택 `Generate` / `Separate`, 주 버튼 `Generate audio` / `Separate audio`, 비활성 이유 `No original audio to separate.`, 방식 배지 `SelVA w/ SelSync` / `ElevenLabs` / `ElevenLabs TTS` / `Separated` / `Kokoro TTS`(공급자가 kokoro일 때).
- 테스트: `uv run python -m unittest discover tests`, `cd web && npx -y node@22 --test tests/*.test.mjs`, `npx tsc --noEmit`.
- 유료 호출(Gemini, ElevenLabs)과 GPU 실측은 Task 7에서만, 사용자 승인 뒤.

## Review Focus

- 원음 영상에서 Separate → 한 트랙 Generate instead → Generate로 전체 전환 → 다시 Separate: 각 트랙의 `agent_model`이 처음 값 그대로이고 실제 방식만 바뀐다. (Task 2)
- 무음 영상에서 Separate 요청 → 422, 상태 변화 없음; UI는 Separate 비활성과 이유 문구. (Task 4, Task 6)
- 이벤트 단위(t2a/tts) 트랙의 트랙 재생성 중 한 이벤트가 실패 → 그 트랙 failed + 이전 오디오 유지, 다른 트랙 영향 없음. (Task 5)
- ElevenLabs 키 없음 + tts 트랙 생성 → 읽을 수 있는 오류, 더미 소리 없음. (Task 3)
- 에이전트가 앰비언스를 v2a로 골라도 생성 방식에서는 t2a로 간다(코드 규칙). (Task 2)

---

### Task 1: 모델 필드

**Files:** Modify `src/v2a_inspect/models/sound_timeline.py`, `src/v2a_inspect/models/video.py`, `web/src/types.ts`. Test `tests/test_models_demo.py`.

**Interfaces:**
- Produces: `SoundTrack.agent_model: Literal["t2a", "v2a", "tts"] | None = None`; `VideoAsset.audio_mode: Literal["generate", "separate"] = "generate"`; TS 같은 필드.

- [ ] **Step 1: 실패하는 테스트** — 기본값(`agent_model is None`, `audio_mode == "generate"`); 예전 샘플 JSON이 그대로 읽힘; 새 필드 왕복.
- [ ] **Step 2: 실패 확인** — `uv run python -m unittest tests.test_models_demo -v`
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 + 전체 + tsc**
- [ ] **Step 5: Commit** — `feat(models): keep the agent's model choice and the audio mode`

### Task 2: `route_tracks` — 방식·t2a 규칙 라우팅

**Files:** Modify `src/v2a_inspect/audio_generation/routing.py` (`apply_default_routing` 제거·대체), `src/v2a_inspect/ui/pipeline.py`(에이전트 직후 호출부 2곳: `_apply_track_routing`). Test `tests/test_track_routing.py`(교체).

**Interfaces:**
- Produces: `route_tracks(timeline: SoundTimeline, *, mode: Literal["generate","separate"], has_audio: bool) -> SoundTimeline` — 새 객체. 각 트랙: `agent_model`이 없으면 Global Constraints 규칙으로 채움; `mode == "separate"`면 `has_audio`가 아닐 때 `ValueError("No original audio to separate.")`, 아니면 모든 트랙 `separate`; `mode == "generate"`면 speech → `tts`, t2a 규칙 해당 → `t2a`, 그 외 `agent_model`: v2a → `selsync`, t2a → `t2a`, tts → `tts`.
- 에이전트 직후 파이프라인은 `route_tracks(timeline, mode=asset.audio_mode, has_audio=asset.has_audio)`(기본 generate).

- [ ] **Step 1: 실패하는 테스트**
  - 생성: 에이전트 v2a sfx(visual_object 원천) → `selsync`, `agent_model == "v2a"`; v2a ambience → `t2a`; v2a sfx with `scene_global` 원천 → `t2a`; v2a sfx with `offscreen_unknown` 원천 → `t2a`; t2a → `t2a`; speech → `tts`.
  - 분리: 원음 있음 → 전부 `separate`, `agent_model` 보존; 원음 없음 → ValueError.
  - 왕복(Review Focus 1): generate → separate → generate 결과가 처음 generate와 같고 `agent_model` 불변.
  - 예전 자산(`generation_model == "selsync"`, `agent_model is None`) → 트랙 종류로 `agent_model` 추정.
- [ ] **Step 2: 실패 확인** — `uv run python -m unittest tests.test_track_routing -v`
- [ ] **Step 3: 구현** — 원천은 `timeline.sound_sources`에서 `sound_source_id`로 찾는다.
- [ ] **Step 4: 통과 + 전체**
- [ ] **Step 5: Commit** — `feat(routing): route tracks by audio mode and the agent's choice`

### Task 3: ElevenLabs TTS

**Files:** Modify `src/v2a_inspect/audio_generation/client.py`(`generate_audio_for_item`의 tts 분기), `src/v2a_inspect/config/settings.py`, `.env.example`. Test `tests/test_audio_routing.py`.

**Interfaces:**
- Produces: `generate_speech_elevenlabs(text: str, out_path: str, duration: float | None = None) -> str` — `ElevenLabs(api_key).text_to_speech.convert(voice_id, text=text, model_id=..., output_format="wav_48000")`의 바이트를 파일로. 키 없으면 `RuntimeError("ELEVENLABS_API_KEY is not set. Add it to .env or set V2A_INSPECT_TTS_PROVIDER=kokoro.")`. `duration`은 쓰지 않는다(길이 조정 없음; 믹스가 이벤트 시작에 놓는다).
- 설정 3개(Global Constraints). `generate_audio_for_item`의 tts 분기는 `tts_provider`에 따라 ElevenLabs 또는 기존 Kokoro.

- [ ] **Step 1: 실패하는 테스트** — `ElevenLabs` patch: 호출 인자(voice_id, model_id, text, output_format)와 파일 내용; 키 없음 → RuntimeError, 파일 없음(Review Focus 4); provider=kokoro → 기존 Kokoro 함수 호출; speech kind는 provider 기본(elevenlabs)으로 감.
- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 + 전체**
- [ ] **Step 5: Commit** — `feat(audio): ElevenLabs text-to-speech for speech tracks`

### Task 4: 방식 API

**Files:** Modify `src/v2a_inspect/ui/routes.py`, `src/v2a_inspect/ui/pipeline.py`, `web/src/api.ts`. Test `tests/test_track_routes.py`.

**Interfaces:**
- Produces: `POST /api/audio/mode` form `mode` → 실행 중 409, 자산 없음 404, 원음 없이 separate 422(`detail="No original audio to separate."`), 성공 시 `audio_mode` 저장 + `route_tracks` 적용 + `publish_asset_mutation` 후 `{"status": "ok"}`(백그라운드 작업 아님, 생성은 하지 않음).
- `run_audio_generation_pipeline`은 시작할 때 `route_tracks(timeline, mode=asset.audio_mode, has_audio=asset.has_audio)`를 한 번 더 적용한다(사용자가 트랙을 개별 교체한 뒤 전체 생성을 누르면 방식대로 다시 맞춤).
- `api.ts`: `setAudioMode(mode: "generate" | "separate"): Promise<void>`.

- [ ] **Step 1: 실패하는 테스트** — 409/404/422(Review Focus 2: 422 뒤 `audio_mode`·트랙 불변); 성공 시 트랙 방식이 바뀜; 전체 생성이 방식대로 다시 라우팅함.
- [ ] **Step 2: 실패 확인** — `uv run python -m unittest tests.test_track_routes -v`
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 + 전체 + tsc**
- [ ] **Step 5: Commit** — `feat(api): choose generate or separate after analysis`

### Task 5: 트랙 재생성을 에이전트 라우팅으로

**Files:** Modify `src/v2a_inspect/ui/routes.py`(트랙 재생성 라우트), `src/v2a_inspect/ui/pipeline.py`(`run_track_regeneration_pipeline`), `web/src/api.ts`. Test `tests/test_track_routes.py`, `tests/test_track_pipeline.py`.

**Interfaces:**
- Produces: `POST /api/audio/tracks/{id}/regenerate` form `method: Literal["separate", "generate"]`(이전 `"selsync"`는 `"generate"`와 같게 받는다). `generate`는 그 트랙의 생성 라우팅(Task 2 규칙을 그 트랙 하나에 적용한 값)을 쓴다: `selsync` → 기존 트랙 단위 경로; `t2a`/`tts` → 그 트랙의 모든 이벤트를 이벤트 경로로 다시 만들고 트랙 stem 재믹스. 대사+selsync 422 규칙은 사라진다(대사는 tts로 감). 분리 on 무음 422 유지.
- 이벤트 단위 재생성 중 이벤트 하나라도 실패 → 그 트랙 `failed`(오류 문구 1차 규칙대로), 이전 이벤트 WAV·stem 유지(Review Focus 3).
- 성공 시 트랙 `generation_model`을 그 라우팅 값으로 바꾼다; `agent_model`은 그대로.
- `api.ts`: `regenerateTrackAudio(trackId, method: "separate" | "generate", description?, seed?)`.

- [ ] **Step 1: 실패하는 테스트** — 분리 트랙 + generate, agent v2a → selsync 경로 호출(`generate_track`); agent t2a → 이벤트마다 `generate_audio_for_item(generation_model="t2a")`; speech → tts; 이벤트 하나 실패 → failed + 이전 오디오 유지; `"selsync"` 별칭 동작.
- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: 구현** — 이벤트 경로는 기존 `_generate_event_audio_artifact`와 `_rebuild_audio_outputs`를 재사용한다.
- [ ] **Step 4: 통과 + 전체 + tsc**
- [ ] **Step 5: Commit** — `feat(pipeline): regenerate a track with the agent's model`

### Task 6: 프런트 — 방식 선택과 버튼 규칙

**Files:** Modify `web/src/components/TopBar.tsx`, `web/src/components/VideoEditor.tsx`, `web/src/App.tsx`, `web/src/lib/selection.ts`, `web/src/components/SelectedTrackPanel.tsx`, `web/src/styles.css`. Test `web/tests/topbar.test.mjs`, `web/tests/selection.test.mjs`, `web/tests/selected-panel.test.mjs`.

**Interfaces:**
- Consumes: Task 4 `setAudioMode`, Task 5 `regenerateTrackAudio(..., "generate")`.
- Produces:
  - `TopBar` props 추가: `audioMode: "generate" | "separate"`, `onSetMode(mode)`. 주 버튼 왼쪽에 2칸 세그먼트(`role="radiogroup"`, 각 `role="radio"` + `aria-checked`), Separate는 `hasAudio === false`면 `disabled` + `title="No original audio to separate."`; 실행 중 둘 다 비활성. 주 버튼 문구 `Generate audio` / `Separate audio`(실행 중 `Generating…`). 주(금색) 버튼은 여전히 하나. 스타일은 시안 토큰(보조 버튼 높이·모서리 6px·0.8125rem)으로.
  - `trackActions(model, hasAudio, trackType)` 새 규칙: `separate` → `["re-separate","generate-instead"]`; `selsync`/`t2a`/`tts` → `hasAudio ? ["regenerate","re-separate"] : ["regenerate"]`. 버튼 매핑: `re-separate` → `"separate"`, `generate-instead`·`regenerate` → `"generate"`.
  - `methodLabel`: `selsync` → `SelVA w/ SelSync`, `t2a` → `ElevenLabs`, `tts` → `ElevenLabs TTS`(공급자 정보가 없으면 그대로), `separate` → `Separated`, `v2a` → `Hunyuan`(예전 자산).
  - 패널: 분리 트랙의 [Generate instead] 옆에 작은 흐린 글씨로 생성 때 쓸 모델(`Generates with SelVA w/ SelSync` 등, `agent_model`에서). 이벤트 단위 트랙은 트랙 [Regenerate] + 기존 이벤트별 목록 유지.
- [ ] **Step 1: 실패하는 테스트** — TopBar: 기본 Generate 선택, 원음 없음 → Separate disabled + 이유, separate 모드 → 주 버튼 `Separate audio`, 금색 버튼 1개; selection: 새 `trackActions`·`methodLabel` 표; 패널: 분리 트랙에 `Generates with …` 문구, t2a 트랙에 `Regenerate`.
- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 + tsc + 빌드**
- [ ] **Step 5: Commit** — `feat(web): choose generate or separate and regenerate with the agent's model`

### Task 7: 프롬프트와 실측 (유료·GPU, 사용자 승인 후)

**Files:** Modify `src/v2a_inspect/prompts/files/system/sound_timeline_agent.txt`. Create/append `docs/demo-measurements.md`.

- [ ] **Step 1: 프롬프트** — §10의 v2a·t2a 규칙으로 "Default sfx and ambience to t2a / Use v2a only when …" 두 줄을 바꾼다(짧게, 기존 규칙과 충돌 없게). 백엔드 테스트 통과 확인. Commit `feat(prompts): narrow v2a to on-screen object contacts`.
- [ ] **Step 2: 승인 받기** — Gemini 1회(`gemini-4-hydrogen`), ElevenLabs SFX·TTS 몇 건, GPU 7 Audyssey docker(`audyssey-tools:demo-1117`) 사용 범위를 알리고 승인.
- [ ] **Step 3: 라우팅 비율** — 샘플 에이전트 1회: 에이전트 원래 판단(`agent_model`)과 규칙 적용 후(`generation_model`)를 트랙 종류별로 표로.
- [ ] **Step 4: 생성 확인** — 무음 사본으로 UI에서 Generate: selsync·t2a(·tts가 있으면) 트랙 생성, 걸린 시간, 오류. 원음 영상으로 Separate → 한 트랙 Generate instead.
- [ ] **Step 5: 기록·Commit** — `docs: record routing ratio and generate/separate run`
