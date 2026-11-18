# 시연 고도화 1차: P0 보완 + P1-a 분리·SelSync 백엔드 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 기존 생성 경로의 결함을 고치고, 원음 영상은 트랙 단위 분리(SAM Audio), 무음 영상은 트랙 단위 생성(SelVA w/ SelSync)을 Audyssey 도구 서버로 돌려 트랙별 다시 분리·다시 생성까지 API로 동작하게 한다.

**Architecture:** 업로드 때 원본 영상·원본 오디오를 보존하고 원음 유무를 기록한다. 에이전트가 만든 타임라인의 트랙 라우팅을 원음 유무로 덮어쓴다. `separate`·`selsync` 트랙은 Audyssey `/separate`·`/generate`를 HTTP로 불러 영상 길이 stem 하나를 받고, 나머지 트랙은 기존 이벤트 경로를 그대로 탄다. 미리보기 믹스는 두 종류 stem과 선택적 residual을 합친다.

**Tech Stack:** Python 3.13, FastAPI, Pydantic v2, httpx, numpy/scipy, ffmpeg, unittest. Audyssey tools 이미지(별도 저장소, 수정 금지).

**Spec:** `docs/superpowers/specs/2026-11-18-demo-upgrade-design.md` (5.1, 5.2, 6절 P0·P1-a). 이 계획 다음에 P1-b(LLM 비교), P2(UI), P3(패키징·비상용 묶음) 계획을 따로 쓴다.

## Global Constraints

- Audyssey 저장소(`/home/gwpark0741/audyssey`)는 읽기만 한다. 도구 서버 HTTP API만 쓴다: `POST /separate` (multipart `mono` 파일, form `plan` JSON, form `attempt` int) → `audio/wav`; `POST /generate` (multipart `video` 파일, form `plan` JSON, form `seed` int) → `audio/wav`. `/health` 외에는 `Authorization: Bearer <token>`.
- `SourcePlan` JSON: `{"name": str(min 1), "kind": "dialogue"|"sfx"|"music"|"ambience", "description": str, "spans": [{"start": float>=0, "end": float>start}]}`. `visual_hint`는 보내지 않는다.
- 원음 판단: 오디오 스트림 없음, 또는 최대 진폭 < −60 dBFS(`10 ** (-60/20)`) → 무음.
- 원본 오디오 형식: 48 kHz, 모노, `pcm_f32le` WAV (Audyssey `extract_audio(..., mono=True)`와 같은 ffmpeg 인자).
- 에이전트 스키마 `src/v2a_inspect/tools/sound_timeline/schemas.py`의 `SoundGenerationModel = Literal["t2a", "v2a", "tts"]`는 바꾸지 않는다.
- UI·문서 표기는 "SelVA w/ SelSync". 코드 값은 `"selsync"`.
- SelSync 첫 생성 시드 기본 42(`V2A_INSPECT_SELSYNC_SEED`). 다시 생성은 새 무작위 시드(`secrets.randbelow(2**31)`), 사용자가 시드를 주면 그 값.
- 도구 서버 호출 시간 제한 600초.
- 모든 stem·residual·믹스는 48 kHz 모노. 길이가 다르면 겹치는 길이만 더한다.
- 테스트는 기존 방식(`unittest`, `uv run python -m unittest discover tests`, 서버는 `uv run --project server python -m unittest discover server/tests`).
- GPU 실측(Task 10)은 다른 사용자 프로세스가 없는 GPU 1장, 사용자 승인 후. 가중치는 `/home/common/huggingface` 공용 캐시를 경로로 참조, `HF_HUB_OFFLINE=1`.

## Review Focus

- 이벤트가 하나도 없는 트랙(사용자가 막 추가한 트랙) → `spans=[]`인 plan으로 분리·생성이 그대로 된다. (Task 6)
- 원본 오디오와 분리 stem의 길이가 코덱 패딩으로 몇 샘플 다름 → residual·믹스가 실패하지 않고 겹치는 길이로 계산한다. (Task 8)
- 도구 서버가 꺼져 있거나 401 → 그 트랙만 `failed`와 읽을 수 있는 오류, 다른 트랙은 완료, 이전 stem 유지. (Task 7)
- 파이프라인 실행 중 트랙 재생성 요청 → 409, 상태 변화 없음. (Task 9)
- 원본 경로가 없는 예전 JSON을 불러온 자산에 `separate` 요청 → "원본 오디오가 없어 분리할 수 없습니다. 생성으로 바꾸세요." 오류. (Task 7)

---

### Task 1: P0 — 생성 실패 원인을 그대로 올리고 ElevenLabs 더미 제거

**Files:**
- Modify: `src/v2a_inspect/audio_generation/client.py` (`generate_audio_for_item` 146-196, `generate_sfx_elevenlabs` 58-)
- Modify: `src/v2a_inspect/ui/pipeline.py` (`run_audio_generation_pipeline` 307-353, `_generate_event_audio_artifact` 497-)
- Test: `tests/test_audio_routing.py`, `tests/test_ui_audio_events.py`

**Interfaces:**
- Produces: `generate_audio_for_item(...) -> str` — 실패 시 예외를 그대로 올린다(더 이상 `None`을 돌려주지 않는다). `generate_sfx_elevenlabs`는 키가 없으면 `RuntimeError("ELEVENLABS_API_KEY is not set. Add it to .env or switch this track to SelVA w/ SelSync.")`.

- [ ] **Step 1: 실패하는 테스트 작성**
  - `test_audio_routing.py::test_generation_error_propagates`: `generate_sfx_elevenlabs`를 `side_effect=RuntimeError("quota")`로 patch → `generate_audio_for_item(kind="sfx", ..., generation_model="t2a")`가 `RuntimeError`를 올리고 메시지에 `"quota"`.
  - `test_audio_routing.py::test_missing_elevenlabs_key_raises`: `get_settings().elevenlabs_api_key`가 `None`이 되도록 patch → `generate_sfx_elevenlabs("x", out, 1.0)`가 `RuntimeError`, 메시지에 `"ELEVENLABS_API_KEY"`, 출력 파일이 생기지 않음.
  - `test_ui_audio_events.py::test_regenerate_reports_generation_error`: `generate_audio_for_item`을 `side_effect=RuntimeError("server 500: boom")`로 patch하고 `run_event_audio_regeneration_pipeline` 실행 → `(await store.snapshot()).error`에 `"server 500: boom"`.
- [ ] **Step 2: 실행해 실패 확인** — `uv run python -m unittest tests.test_audio_routing tests.test_ui_audio_events -v`. 새 테스트 3개 FAIL.
- [ ] **Step 3: 구현** — `generate_audio_for_item`의 `try/except Exception: return None` 제거. `generate_sfx_elevenlabs`의 더미 분기를 위 `RuntimeError`로. `run_audio_generation_pipeline` 루프에서는 항목별로 예외를 잡아 `f"{track.label}: {exc}"`를 모으고 다음 항목으로 진행한다. 하나도 생성되지 않으면 `ValueError("No audio files were generated. " + "; ".join(errors))`. `_generate_event_audio_artifact`는 예외를 그대로 올린다.
- [ ] **Step 4: 전체 테스트 통과 확인** — `uv run python -m unittest discover tests`. 기존 테스트가 `None` 반환을 기대하면 예외 기대로 고친다.
- [ ] **Step 5: Commit** — `fix(audio): surface generation errors instead of silent fallbacks`

### Task 2: P0 — 저장 안 한 편집이 있으면 이벤트 재생성 막기

**Files:**
- Modify: `web/src/components/VideoEditor.tsx` (`regenerateSoundEventAudio` 355-383, Regenerate 버튼 1211-1223)
- Test: `web/tests/regenerate-guard.test.mjs` (새 파일, 기존 `web/tests/*.test.mjs` 방식)

**Interfaces:**
- Produces: `export function regenerateBlockedReason(hasTimelineEdits: boolean, status: string): string | null` (`VideoEditor.tsx` 또는 기존 순수 함수가 모인 파일). 편집이 있으면 `"Save Changes first. Regenerating now would discard your other unsaved edits."`, 실행 중이면 `"Wait for the current run to finish."`, 아니면 `null`.

- [ ] **Step 1: 실패하는 테스트** — 세 경우의 반환값을 `assert.equal`로 확인.
- [ ] **Step 2: 실패 확인** — `cd web && npm test` (Node 20.19+ 필요. 호스트가 18이면 `npx -y node@22 --test tests/regenerate-guard.test.mjs`).
- [ ] **Step 3: 구현** — Regenerate 버튼 `disabled={reason !== null}`, 버튼 아래 `<p role="status">{reason}</p>`. `regenerateSoundEventAudio`의 `catch { return; }`는 오류를 `setExportStatus(error.message)`로 보여준다.
- [ ] **Step 4: 통과 확인** — 같은 명령 PASS, `npx tsc --noEmit` 오류 0.
- [ ] **Step 5: Commit** — `fix(ui): block event regenerate while edits are unsaved`

### Task 3: 데이터 모델 확장

**Files:**
- Modify: `src/v2a_inspect/models/video.py`, `src/v2a_inspect/models/sound_timeline.py:12-85`, `src/v2a_inspect/models/audio_artifacts.py`, `src/v2a_inspect/models/__init__.py`
- Test: `tests/test_models_demo.py` (새 파일)

**Interfaces:**
- Produces:
  - `SoundGenerationModel = Literal["t2a", "v2a", "tts", "separate", "selsync"]` (models 쪽만)
  - `SoundTrack.description: str | None = None`
  - `SoundTrack.migrate_legacy_values`: speech는 `generation_model`이 `"separate"`면 유지, 그 외에는 `"tts"`. `check_generation_model`: speech는 `tts|separate`만, 비speech는 `tts` 금지(기존).
  - `TrackStatus(SchemaModel)`: `state: Literal["running", "done", "failed"]`, `error: str | None = None`
  - `SoundTrackAudioArtifact` 추가 필드: `origin: Literal["mixed", "separated", "generated"] = "mixed"`, `seed: int | None = None`, `attempt: int | None = None`, `description: str | None = None`
  - `VideoAsset` 추가 필드: `raw_video_path: Path | None = None`, `original_audio_path: Path | None = None`, `has_audio: bool = False`, `separated_stem_paths: dict[UUID, Path] = {}`, `track_status: dict[UUID, TrackStatus] = {}`, `residual_path: Path | None = None`, `residual_enabled: bool = False`

- [ ] **Step 1: 실패하는 테스트**
  - `test_speech_track_keeps_separate`: `SoundTrack(track_type="speech", label="V", generation_model="separate").generation_model == "separate"`.
  - `test_speech_track_defaults_to_tts`: `generation_model="t2a"`인 speech → `"tts"`.
  - `test_sfx_rejects_tts`: `ValidationError`.
  - `test_old_asset_json_loads`: `samples/sample.video-asset.json`을 `VideoAsset.model_validate_json` → `has_audio is False`, `raw_video_path is None`, `track_status == {}`.
  - `test_asset_roundtrip_new_fields`: 새 필드를 채운 자산을 `model_dump_json` → 다시 읽어 같음.
- [ ] **Step 2: 실패 확인** — `uv run python -m unittest tests.test_models_demo -v`.
- [ ] **Step 3: 구현** — 위 Interfaces 그대로. `schemas.py`는 건드리지 않는다.
- [ ] **Step 4: 통과 확인** — 같은 명령 + `discover tests` 전체 PASS.
- [ ] **Step 5: Commit** — `feat(models): add separation and SelSync fields`

### Task 4: 원본 보존과 원음 판단

**Files:**
- Modify: `src/v2a_inspect/preprocessing/video.py` (`prepare_video` 132-155)
- Modify: `src/v2a_inspect/ui/routes.py` (`import_asset` 288-343: 업로드 원본으로 같은 필드 채움)
- Test: `tests/test_original_audio.py` (새 파일)

**Interfaces:**
- Produces:
  - `extract_original_audio(raw_video_path: Path, out_path: Path) -> Path | None` — 오디오 스트림이 없으면(ffprobe `-select_streams a`) `None`. 있으면 48 kHz 모노 `pcm_f32le` WAV를 쓰고, 최대 진폭 < `10 ** (-60/20)`이면 파일을 지우고 `None`, 아니면 경로.
  - `prepare_video(raw_video_path, work_dir) -> VideoAsset`가 `raw_video_path=raw_video_path`, `original_audio_path=extract_original_audio(raw_video_path, work_dir / f"{raw_video_path.stem}.original.wav")`, `has_audio=original_audio_path is not None`를 채운다.

- [ ] **Step 1: 실패하는 테스트** — ffmpeg `lavfi`로 임시 2초 영상 세 개 생성: `testsrc`+`sine=frequency=440`, `testsrc`만, `testsrc`+`anullsrc`.
  - `test_audio_video_has_audio`: 경로 반환, `scipy.io.wavfile.read` 샘플레이트 48000, 1채널.
  - `test_no_stream_is_silent`: `None`.
  - `test_silent_stream_is_silent`: `None`, 출력 파일 없음.
- [ ] **Step 2: 실패 확인** — `uv run python -m unittest tests.test_original_audio -v`.
- [ ] **Step 3: 구현** — `subprocess.run` + ffmpeg/ffprobe(이 모듈의 기존 ffmpeg 호출 방식). import 경로는 `_write_upload`가 만든 `upload_path`를 원본으로 같은 세 필드를 채운다.
- [ ] **Step 4: 통과 확인** — 같은 명령 + 전체 PASS.
- [ ] **Step 5: Commit** — `feat(preprocess): keep raw video and original audio`

### Task 5: 원음 유무로 트랙 라우팅 덮어쓰기

**Files:**
- Create: `src/v2a_inspect/audio_generation/routing.py`
- Modify: `src/v2a_inspect/ui/pipeline.py` (`run_uploaded_video_pipeline` 104-113, `run_sound_timeline_pipeline` 128-136: 에이전트 직후 적용하고 `publish_asset_mutation`)
- Test: `tests/test_track_routing.py` (새 파일)

**Interfaces:**
- Produces: `apply_default_routing(timeline: SoundTimeline, *, has_audio: bool) -> SoundTimeline` — 새 객체를 돌려준다. 원음 있음: 모든 트랙 `separate`. 무음: sfx·ambience `selsync`, speech `tts`.

- [ ] **Step 1: 실패하는 테스트** — 표의 네 칸(원음×{sfx, ambience, speech}, 무음×같은 셋)을 `assertEqual`로. 입력 타임라인이 바뀌지 않았는지도 확인.
- [ ] **Step 2: 실패 확인** — `uv run python -m unittest tests.test_track_routing -v`.
- [ ] **Step 3: 구현** — `model_copy(update=...)`. 파이프라인에서는 `video_asset.sound_timeline`이 있을 때만 적용.
- [ ] **Step 4: 통과 확인**.
- [ ] **Step 5: Commit** — `feat(routing): route tracks by original audio presence`

### Task 6: Audyssey 도구 서버 클라이언트

**Files:**
- Create: `src/v2a_inspect/audio_generation/audyssey.py`
- Modify: `src/v2a_inspect/config/settings.py` (설정 4개), `.env.example`
- Test: `tests/test_audyssey_client.py` (새 파일)

**Interfaces:**
- Consumes: Task 3 모델.
- Produces:
  - 설정: `audyssey_tools_url: str = "http://127.0.0.1:8700"` (`V2A_INSPECT_AUDYSSEY_TOOLS_URL`), `audyssey_tool_token: SecretStr | None` (`V2A_INSPECT_AUDYSSEY_TOOL_TOKEN`), `selsync_seed: int = 42` (`V2A_INSPECT_SELSYNC_SEED`), `track_description_default: Literal["label", "first_event"] = "label"` (`V2A_INSPECT_TRACK_DESCRIPTION_DEFAULT`).
  - `track_description(track: SoundTrack, events: list[SoundEvent]) -> str` — `track.description`가 있으면 그것. 없으면 설정에 따라 `track.label` 또는 시작 프레임이 가장 이른 이벤트의 `description`(이벤트가 없으면 label). `plan.py`의 접두어는 붙이지 않는다.
  - `source_plan(track: SoundTrack, events: list[SoundEvent], *, fps: float, description: str) -> dict` — `name=track.label`, `kind={"speech": "dialogue"}.get(track_type, track_type)`, `description`, `spans=[{"start": round(e.start_frame_index / fps, 3), "end": round(e.end_frame_index / fps, 3)}]`(시작순).
  - `separate_track(mono_path: Path, plan: dict, attempt: int, out_path: Path) -> Path`
  - `generate_track(video_path: Path, plan: dict, seed: int, out_path: Path) -> Path`
  - 둘 다 `httpx.post(..., timeout=600)`, Bearer 헤더(토큰 없으면 헤더 생략). 2xx가 아니면 `RuntimeError(f"Audyssey {endpoint} failed ({status}): {detail}")`(`detail`은 JSON의 `detail` 또는 본문 앞 200자). 연결 실패는 `RuntimeError(f"Audyssey tools server unreachable at {url}: {exc}")`.

- [ ] **Step 1: 실패하는 테스트** (`httpx.post`를 patch)
  - `test_source_plan_maps_speech_and_spans`: speech 트랙, 이벤트 (30–60), (0–15), fps 30 → `kind == "dialogue"`, `spans == [{"start": 0.0, "end": 0.5}, {"start": 1.0, "end": 2.0}]`.
  - `test_source_plan_without_events`: `spans == []`. (Review Focus 1)
  - `test_track_description_prefers_override_then_label`.
  - `test_generate_sends_seed_and_writes_wav`: 응답 200 + 바이트 → 파일에 그 바이트, 호출 `data["seed"] == "7"` 또는 `7`, `headers["Authorization"] == "Bearer t"`.
  - `test_separate_error_message`: 응답 401 `{"detail": "unauthorized"}` → `RuntimeError`, 메시지에 `"401"`과 `"unauthorized"`.
  - `test_unreachable`: `side_effect=httpx.ConnectError("x")` → 메시지에 `"unreachable"`.
- [ ] **Step 2: 실패 확인** — `uv run python -m unittest tests.test_audyssey_client -v`.
- [ ] **Step 3: 구현** — `plan`은 `json.dumps`로 form 필드에. 파일은 `with path.open("rb")`.
- [ ] **Step 4: 통과 확인**.
- [ ] **Step 5: Commit** — `feat(audio): add Audyssey separation and SelSync client`

### Task 7: 트랙 단위 생성을 전체 생성과 미리보기 믹스에 통합

**Files:**
- Modify: `src/v2a_inspect/ui/pipeline.py` (`run_audio_generation_pipeline` 258-366, `_rebuild_audio_outputs` 545-655)
- Test: `tests/test_track_pipeline.py` (새 파일)

**Interfaces:**
- Consumes: Task 3 필드, Task 6 `track_description`, `source_plan`, `separate_track`, `generate_track`.
- Produces:
  - `async def _generate_track_stem(video_asset: VideoAsset, track: SoundTrack, *, method: Literal["separate", "selsync"], description: str, seed: int | None, attempt: int | None, out_dir: Path) -> SoundTrackAudioArtifact` — `separate`: `original_audio_path`가 없으면 `ValueError("원본 오디오가 없어 분리할 수 없습니다. 생성으로 바꾸세요.")`. `selsync`: `raw_video_path`(없으면 `source_path`)를 보낸다. 출력은 `out_dir / f"{track_id 앞 8자}_{method}_{seed 또는 attempt}.wav"`. `waveform_peaks`는 기존 `_waveform_peaks`, `event_count`는 그 트랙 이벤트 수, `origin`은 `separated`/`generated`.
  - `_rebuild_audio_outputs(video_asset, audio_plan, event_artifacts, store, track_artifacts: list[SoundTrackAudioArtifact] = [])` — 이벤트 WAV에서 믹스한 트랙 stem(`origin="mixed"`)과 넘겨받은 트랙 단위 stem을 합쳐 미리보기를 만든다. 이벤트 WAV가 하나도 없어도 트랙 단위 stem이 있으면 진행한다. `residual_enabled`이고 `residual_path`가 있으면 미리보기에 함께 넣는다.
  - `run_audio_generation_pipeline`: 트랙별로 `generation_model`이 `separate`/`selsync`면 `_generate_track_stem`(selsync 시드 = `settings.selsync_seed`, separate attempt = 1), 아니면 기존 이벤트 경로. 트랙마다 `track_status[track_id]`를 `running` → `done`/`failed(error=str(exc))`로 기록하고 실패해도 다음 트랙으로. `separate` 성공 시 `separated_stem_paths[track_id]` 갱신. 모든 트랙이 실패하면 `set_error`로 트랙별 오류를 합쳐 보여준다.

- [ ] **Step 1: 실패하는 테스트** (`separate_track`/`generate_track`를 patch해 1초 48 kHz 사인파 WAV를 쓰게 하고, `mix_audio_into_video`를 patch해 출력 경로만 만들기)
  - `test_silent_asset_generates_selsync_stems`: 무음 자산, sfx 트랙 2개 → 트랙 stem 2개 `origin == "generated"`, `seed == 42`, `synthesized_video_path` 설정.
  - `test_audio_asset_separates_and_records_stem`: 원음 자산 → `origin == "separated"`, `attempt == 1`, `separated_stem_paths`에 그 트랙.
  - `test_one_track_failure_keeps_others`: 첫 트랙 `generate_track`이 `RuntimeError("Audyssey /generate failed (500): boom")` → 그 트랙 `track_status.state == "failed"`, 오류에 `"boom"`, 다른 트랙 `done`, 전체 상태는 오류 아님. (Review Focus 3)
  - `test_separate_without_original_audio_errors`: `original_audio_path=None`인데 `separate` → 그 트랙 오류에 `"생성으로 바꾸세요"`. (Review Focus 5)
  - `test_stem_only_asset_builds_preview`: 이벤트 경로 트랙 없이 트랙 단위 stem만 → 예외 없이 미리보기 생성.
- [ ] **Step 2: 실패 확인** — `uv run python -m unittest tests.test_track_pipeline -v`.
- [ ] **Step 3: 구현** — 위 Interfaces. HTTP 호출은 `asyncio.to_thread`.
- [ ] **Step 4: 통과 확인** — 같은 명령 + 전체 PASS.
- [ ] **Step 5: Commit** — `feat(pipeline): generate separate/selsync tracks as stems`

### Task 8: residual

**Files:**
- Create: `src/v2a_inspect/audio_generation/residual.py`
- Modify: `src/v2a_inspect/ui/pipeline.py` (트랙 단위 처리 뒤 residual 다시 계산)
- Test: `tests/test_residual.py` (새 파일)

**Interfaces:**
- Produces: `compute_residual(original_path: Path, stem_paths: list[Path], out_path: Path) -> Path` — 48 kHz 모노 float32. `original − Σ stems`, 각 stem은 겹치는 길이만 뺀다. 출력 길이는 원본 길이. 파이프라인은 `has_audio`이고 `separated_stem_paths`가 비어 있지 않을 때 `work/audio/residual.wav`로 계산해 `residual_path`에 기록한다(트랙을 생성으로 바꿔도 `separated_stem_paths`의 stem은 계속 뺀다).

- [ ] **Step 1: 실패하는 테스트**
  - `test_residual_subtracts_stems`: 원본 = a + b(사인파 두 개), stem = a → residual ≈ b (`np.allclose(atol=1e-5)`).
  - `test_residual_length_mismatch`: stem이 원본보다 37샘플 김/짧음 → 예외 없음, 출력 길이 = 원본 길이. (Review Focus 2)
- [ ] **Step 2: 실패 확인** — `uv run python -m unittest tests.test_residual -v`.
- [ ] **Step 3: 구현** — `scipy.io.wavfile` 읽기/쓰기(이 저장소의 기존 방식).
- [ ] **Step 4: 통과 확인**.
- [ ] **Step 5: Commit** — `feat(audio): compute residual from separated stems`

### Task 9: 트랙 재생성·residual API

**Files:**
- Modify: `src/v2a_inspect/ui/routes.py`, `src/v2a_inspect/ui/pipeline.py`, `web/src/api.ts`, `web/src/types.ts`
- Test: `tests/test_track_routes.py` (새 파일, FastAPI `TestClient`)

**Interfaces:**
- Consumes: Task 7 `_generate_track_stem`, `_rebuild_audio_outputs`; Task 8 `compute_residual`.
- Produces:
  - `POST /api/audio/tracks/{sound_track_id}/regenerate` form `method: Literal["separate", "selsync"]`, `description: str | None`, `seed: int | None` → `{"status": "queued"}`. 실행 중 409, 자산 없음 404, 없는 트랙 404, 무음 자산에 `separate` 422.
  - `async def run_track_regeneration_pipeline(video_asset, store, sound_track_id: str, method, description: str | None, seed: int | None) -> None` — 설명이 오면 `SoundTrack.description`에 저장하고 트랙 `generation_model = method`. selsync 시드 = 주어진 값 또는 `secrets.randbelow(2**31)`. separate attempt = 이전 attempt + 1(없으면 1). 실패 시 이전 stem 유지, `track_status` failed. 성공 시 그 트랙 stem 교체 → residual 다시 계산 → `_rebuild_audio_outputs`.
  - `POST /api/audio/residual` form `enabled: bool` → 미리보기 다시 믹스.
  - `web/src/api.ts`: `regenerateTrackAudio(trackId: string, method: "separate" | "selsync", description?: string, seed?: number): Promise<void>`, `setResidualEnabled(enabled: boolean): Promise<void>`. `types.ts`에 Task 3 필드.

- [ ] **Step 1: 실패하는 테스트**
  - `test_regenerate_track_conflict_while_running`: `store.set_running` 후 요청 → 409, 자산 버전 그대로. (Review Focus 4)
  - `test_regenerate_track_queues`: 200, `run_track_regeneration_pipeline`이 인자 그대로 예약됨(patch).
  - `test_separate_on_silent_asset_is_422`.
  - `test_track_regeneration_new_seed_and_description` (파이프라인 직접 호출, HTTP는 patch): seed 미지정 → 기록된 seed ≠ 42, 설명 저장, `generation_model == "selsync"`. seed=1234 지정 → 1234.
  - `test_reseparate_increments_attempt`: attempt 1 → 2.
- [ ] **Step 2: 실패 확인** — `uv run python -m unittest tests.test_track_routes -v`.
- [ ] **Step 3: 구현** — 기존 이벤트 재생성 라우트·파이프라인 모양을 따른다.
- [ ] **Step 4: 통과 확인** — 같은 명령 + 전체 PASS + `cd web && npx tsc --noEmit`.
- [ ] **Step 5: Commit** — `feat(api): per-track regenerate and residual toggle`

### Task 10: Hunyuan을 올리지 않는 서버 설정

**Files:**
- Modify: `server/src/v2a_inspect_server/settings.py`, `server/src/v2a_inspect_server/app.py:35-49`
- Test: `server/tests/test_settings.py`

**Interfaces:**
- Produces: `ServerSettings.hunyuan_enabled: bool = True` (`V2A_SERVER_HUNYUAN_ENABLED`). `False`면 lifespan에서 `HunyuanInferenceClient()`를 만들지 않고, `/infer/hunyuan/generate-v2a`는 503 `"Hunyuan is disabled on this server"`.

- [ ] **Step 1: 실패하는 테스트** — `test_hunyuan_enabled_env`: env `V2A_SERVER_HUNYUAN_ENABLED=false` → `False`. `test_app.py`에 비활성 시 503 테스트(기존 앱 테스트 방식으로 클라이언트 생성 patch).
- [ ] **Step 2: 실패 확인** — `uv run --project server python -m unittest discover server/tests -v`.
- [ ] **Step 3: 구현**.
- [ ] **Step 4: 통과 확인**.
- [ ] **Step 5: Commit** — `feat(server): allow disabling Hunyuan at startup`

### Task 11: GPU 실측 — 실제 도구 서버로 끝까지 (사용자 승인 후)

**Files:**
- Create: `docs/demo-measurements.md` (결과 기록)

- [ ] **Step 1: 승인 받기** — 사용할 GPU 번호(다른 사용자 프로세스 없음), 예상 점유(약 40 GB, 1시간 이내)를 사용자에게 알리고 승인.
- [ ] **Step 2: 서비스 띄우기** — Audyssey tools: `cd /home/gwpark0741/audyssey && CUDA_VISIBLE_DEVICES=<n> HF_HUB_OFFLINE=1 AUDYSSEY_TOOL_TOKEN=<t> uv run --project gpu --locked audyssey tool-server --tool separation --tool generation --impl real --host 127.0.0.1 --port <빈 포트>`. v2a_inspect 추론 서버는 `V2A_SERVER_HUNYUAN_ENABLED=false`로. UI는 `uv run v2a ui --port <빈 포트>`.
- [ ] **Step 3: 원음 시나리오** — `samples/sample.mp4`(7.1초, 원음)로 분석 → Generate Audio → 트랙별 `separated` stem 확인 → 한 트랙 다시 분리(attempt 2) → 한 트랙 생성으로 바꾸기 → residual 켜기. 걸린 시간과 최대 GPU 메모리(`nvidia-smi --query-gpu=memory.used`) 기록.
- [ ] **Step 4: 무음 시나리오** — 같은 영상에서 오디오를 지운 사본(`ffmpeg -an`)으로 같은 흐름. 트랙별 `generated`, 시드 42.
- [ ] **Step 5: 시드 재현 측정** — 같은 트랙을 seed 42로 두 번 생성해 파형 상관(`np.corrcoef`) 기록. 0.99 이상이면 통과, 아니면 설계 5.5의 낮춘 정의를 문서에 적는다.
- [ ] **Step 6: 트랙 설명 기본값 청취 비교** — 원음·무음 각각 `track_description_default=label`과 `first_event`로 stem을 만들어 파일 경로를 사용자에게 주고 사용자가 고른다. 고른 값을 `.env.example` 기본값과 설정 기본값에 반영.
- [ ] **Step 7: 정리·Commit** — 서비스 종료, GPU 비었는지 확인. `docs: record demo backend measurements`
