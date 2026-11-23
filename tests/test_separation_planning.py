from __future__ import annotations

import asyncio
import io
import json
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest
from google.genai import types

from v2a_inspect import separation_planning as sp
from v2a_inspect.models import (
    SceneTrack,
    SceneTrackPoint,
    SoundTimeline,
    SoundTrack,
    VideoAsset,
    VisualIdentityLayer,
    VisualObject,
    VisualPresence,
)
from v2a_inspect.ui.bundle import read_bundle, write_bundle
from v2a_inspect.ui.pipeline import run_separation_planning_pipeline
from v2a_inspect.ui.store import VideoAssetStore


def _plan(name: str, kind: str = "sfx", spans=((1.0, 2.0),), hint: bool = True) -> sp.SourcePlan:
    return sp.SourcePlan(
        name=name,
        kind=kind,
        description=f"{name} sound",
        spans=[sp.Span(start=a, end=b) for a, b in spans],
        visual_hint=sp.VisualHint(time=1.0, box=(0, 0, 0.5, 0.5), description="x") if hint else None,
    )


def _check(status="supported", evidence="audio") -> sp._Check:
    return sp._Check(status=status, evidence=evidence, reason="heard it")


class FakeLLM:
    """Planner answers come from `rounds`; validator answers from `checks` by source name."""

    model = "fake"
    usage: list = []

    def __init__(self, rounds=(), checks=None) -> None:
        self.rounds = list(rounds)
        self.checks = checks or {}
        self.planner_texts: list[str] = []
        self.validated: list[str] = []
        self.media_calls: list[str] = []

    def media(self, data: bytes, mime_type: str) -> types.Part:
        self.media_calls.append(mime_type)
        return types.Part.from_bytes(data=data, mime_type=mime_type)

    def ask(self, system, parts, response_type):
        text = parts[-1].text
        if response_type is sp._Sources:
            self.planner_texts.append(text)
            return sp._Sources(sources=[p.model_dump() for p in self.rounds.pop(0)])
        name = json.loads(text)["source"]["name"]
        self.validated.append(name)
        result = self.checks.get(name, _check())
        if isinstance(result, Exception):
            raise result
        return result


LOUD = np.full((48000 * 10, 2), 0.1, dtype=np.float32)


def test_to_timeline_converts_sources_to_tracks_and_frames() -> None:
    plans = [
        _plan("Voice", "dialogue", spans=((0.01, 0.5), (9.0, 12.0))),
        _plan("Wind", "ambience", spans=(), hint=False),
        _plan("Hit", "music", hint=False),
        _plan("Clank", "sfx", spans=((2.0, 2.01),)),
    ]
    timeline = sp.to_timeline(plans, fps=30, frame_count=293)

    voice, wind, hit, clank = timeline.sound_tracks
    assert [t.track_type for t in timeline.sound_tracks] == ["speech", "ambience", "sfx", "sfx"]
    assert all(t.generation_model == "separate" for t in timeline.sound_tracks)
    assert [t.agent_model for t in timeline.sound_tracks] == ["tts", "t2a", "v2a", "v2a"]
    assert voice.description == "Voice sound" and voice.label == "Voice"
    sources = {s.sound_source_id: s for s in timeline.sound_sources}
    assert [sources[t.sound_source_id].source_type for t in timeline.sound_tracks] == [
        "visual_object",
        "scene_global",
        "offscreen_unknown",
        "visual_object",
    ]
    events = {t.sound_track_id: [e for e in timeline.sound_events if e.sound_track_id == t.sound_track_id] for t in timeline.sound_tracks}
    # floor(start * fps) .. ceil(end * fps), clamped to the video
    assert [(e.start_frame_index, e.end_frame_index) for e in events[voice.sound_track_id]] == [(0, 15), (270, 293)]
    assert all(e.spoken_text == "Voice sound" for e in events[voice.sound_track_id])
    assert all(e.spoken_text is None for e in events[hit.sound_track_id])
    assert all(e.description == "Hit sound" for e in events[hit.sound_track_id])
    # no spans: the whole video; a 10 ms span still gets one frame
    assert [(e.start_frame_index, e.end_frame_index) for e in events[wind.sound_track_id]] == [(0, 293)]
    assert [(e.start_frame_index, e.end_frame_index) for e in events[clank.sound_track_id]] == [(60, 61)]


def test_source_clamps_spans_and_hint() -> None:
    item = {
        "name": "Dog",
        "kind": "sfx",
        "description": "bark",
        "spans": [{"start": -1, "end": 2}, {"start": 11, "end": 12}],
        "visual_hint": {"time": 99, "box": [-1, 0.2, 2, 0.8], "description": "dog"},
    }
    source = sp._source(item, 10.0)
    assert [(s.start, s.end) for s in source.spans] == [(0, 2)]
    assert source.visual_hint.time == 10 and source.visual_hint.box == (0, 0.2, 1, 0.8)
    with pytest.raises(ValueError):
        sp._source({"name": "x", "kind": "noise", "description": ""}, 10.0)


def test_validation_windows_pad_merge_and_bound() -> None:
    source = _plan("A", spans=((0.2, 1.0), (1.4, 2.0), (9.8, 10.0)))
    assert sp.validation_windows(source, 10.0, 0.5) == [(0.0, 2.5), (9.3, 10.0)]
    assert sp.validation_windows(_plan("B", spans=()), 10.0, 0.5) == [(0.0, 10.0)]


@pytest.mark.parametrize(
    ("answer", "kind"),
    [
        (_check("unsupported", "none"), "not_found"),
        (_check("uncertain", "audio"), "validation_uncertain"),
        (_check("supported", "none"), "validation_uncertain"),
        (_check("supported", "visual"), "validation_uncertain"),
        (sp._InvalidJSON("bad"), "validation_error"),
        (_check("supported", "audio"), None),
        (_check("supported", "audio_visual"), None),
    ],
)
def test_validation_decision_rules(answer, kind) -> None:
    llm = FakeLLM(checks={"A": answer})
    issues = sp.validate_plan(llm, LOUD, [_plan("A")], 10.0)
    assert [i.kind for i in issues] == ([kind] if kind else [])
    assert llm.media_calls == ["audio/wav"]  # one merged context window


def test_digitally_silent_targets_are_rejected_without_a_call() -> None:
    samples = LOUD.copy()
    samples[48000 : 48000 * 2] = 0  # the target 1-2 s is silent; the padding is not
    llm = FakeLLM()
    (issue,) = sp.validate_plan(llm, samples, [_plan("A")], 10.0)
    assert issue.kind == "not_found" and "digitally silent" in issue.message
    assert llm.validated == [] and llm.media_calls == []


def test_validator_gets_stereo_16khz_wav_windows() -> None:
    llm = FakeLLM()
    samples = np.stack([np.full(480000, 0.5), np.full(480000, -0.5)], axis=1).astype(np.float32)
    (_, wav, *_) = sp._audio_parts(llm, samples, [(0.0, 1.0)])
    with wave.open(io.BytesIO(wav.inline_data.data)) as stream:
        assert (stream.getnchannels(), stream.getframerate(), stream.getsampwidth()) == (2, 16000, 2)
        assert stream.getnframes() == 16000


def test_replanning_keeps_accepted_and_only_replans_failing() -> None:
    good, bad = _plan("Good"), _plan("Bad")
    fixed = _plan("Bad")
    llm = FakeLLM(
        rounds=[[good, bad], [fixed]],
        checks={"Bad": _check("unsupported", "none")},
    )
    # Round 2 passes: swap the answer after the first Bad validation.
    original = llm.ask

    def ask(system, parts, response_type):
        result = original(system, parts, response_type)
        if response_type is sp._Check and llm.validated.count("Bad") == 1:
            llm.checks["Bad"] = _check()
        return result

    llm.ask = ask
    accepted, dropped = sp._plan_rounds(llm, types.Part(text="clip"), LOUD, 10.0)

    assert [p.name for p in accepted] == ["Good", "Bad"]
    assert dropped == []
    assert llm.validated == ["Good", "Bad", "Bad"]  # Good is not validated again
    second = llm.planner_texts[1]
    assert '"accepted_sources": [{"name": "Good"' in second
    feedback = second.split("Feedback: ")[1].split("\nRequest context")[0]
    assert [i["source_name"] for i in json.loads(feedback)] == ["Bad"]


def test_still_failing_sources_are_dropped_after_the_last_round() -> None:
    llm = FakeLLM(
        rounds=[[_plan("Good"), _plan("Bad"), _plan("Quiet")], [_plan("Bad")]],
        checks={"Bad": _check("uncertain", "none"), "Quiet": _check("unsupported", "none")},
    )
    accepted, dropped = sp._plan_rounds(llm, types.Part(text="clip"), LOUD, 10.0)

    assert [p.name for p in accepted] == ["Good"]
    assert {i.source_name: i.kind for i in dropped} == {
        "Bad": "validation_uncertain",
        "Quiet": "not_found",  # not re-proposed in round 2, still reported
    }
    assert len(llm.rounds) == 0  # two rounds at most


def test_clean_first_round_stops_planning() -> None:
    llm = FakeLLM(rounds=[[_plan("A")]])
    accepted, dropped = sp._plan_rounds(llm, types.Part(text="clip"), LOUD, 10.0)
    assert [p.name for p in accepted] == ["A"] and dropped == []


def test_duplicate_names_merge_and_empty_description_is_an_issue() -> None:
    merged, issues = sp.check_plans([_plan("A", spans=((0, 1),)), _plan("A", spans=((2, 3),))])
    assert [(s.start, s.end) for s in merged[0].spans] == [(0, 1), (2, 3)] and issues == []
    empty = _plan("B").model_copy(update={"description": " "})
    _, issues = sp.check_plans([empty])
    assert [i.kind for i in issues] == ["empty_description"]


def test_plan_separation_end_to_end_with_a_real_clip(tmp_path: Path) -> None:
    raw = tmp_path / "raw.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=d=2:s=640x360:r=30",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-shortest",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(raw)],
        check=True,
    )  # fmt: skip
    asset = VideoAsset(source_path=tmp_path / "v.mp4", frame_count=60, raw_video_path=raw, has_audio=True)
    llm = FakeLLM(rounds=[[_plan("Tone", spans=((0.0, 1.5),))]])

    timeline, dropped = sp.plan_separation(asset, llm)

    assert dropped == [] and [t.label for t in timeline.sound_tracks] == ["Tone"]
    assert llm.media_calls == ["video/mp4", "audio/wav"]
    (event,) = timeline.sound_events
    assert (event.start_frame_index, event.end_frame_index) == (0, 45)


def _asset(tmp_path: Path, **update) -> VideoAsset:
    track = SoundTrack(track_type="sfx", label="Door", generation_model="selsync")
    return VideoAsset(
        source_path=tmp_path / "v.mp4",
        frame_count=30,
        has_audio=True,
        sound_timeline=SoundTimeline(sound_tracks=[track]),
        **update,
    )


def test_switch_audio_mode_swaps_and_clears_the_preview(tmp_path: Path) -> None:
    separation = sp.to_timeline([_plan("Hit")], fps=30, frame_count=30)
    asset = _asset(tmp_path, inactive_timeline=separation, synthesized_video_path=tmp_path / "p.mp4")
    switched = sp.switch_audio_mode(asset, "separate")
    assert switched.audio_mode == "separate" and switched.sound_timeline == separation
    assert switched.inactive_timeline == asset.sound_timeline and switched.synthesized_video_path is None
    assert sp.switch_audio_mode(switched, "generate").sound_timeline == asset.sound_timeline


def test_switch_to_generate_without_a_stored_timeline_keeps_the_tracks(tmp_path: Path) -> None:
    asset = _asset(tmp_path, audio_mode="separate")
    switched = sp.switch_audio_mode(asset, "generate")
    assert switched.inactive_timeline is None
    assert [t.generation_model for t in switched.sound_timeline.sound_tracks] != ["separate"]


def test_planning_pipeline_publishes_the_planned_timeline(tmp_path: Path, monkeypatch) -> None:
    asset = _asset(tmp_path)
    planned = sp.to_timeline([_plan("Hit")], fps=30, frame_count=30)
    dropped = [sp.PlanIssue(source_name="Dog", kind="not_found", message="Dog: not audible")]
    monkeypatch.setattr("v2a_inspect.ui.pipeline.plan_separation", lambda _: (planned, dropped))
    store = VideoAssetStore()
    asyncio.run(store.set_complete_asset(asset))

    asyncio.run(run_separation_planning_pipeline(asset, store))
    snapshot = asyncio.run(store.snapshot())

    assert snapshot.status == "complete" and snapshot.asset.audio_mode == "separate"
    assert snapshot.asset.sound_timeline == planned
    assert snapshot.asset.inactive_timeline == asset.sound_timeline
    assert snapshot.current_stage == "Planned 1 sources: Hit. Skipped 1: Dog: not audible"


@pytest.mark.parametrize("empty", [False, True])
def test_planning_failure_leaves_the_asset_in_generate_mode(tmp_path: Path, monkeypatch, empty) -> None:
    asset = _asset(tmp_path)

    def fail(_):
        if empty:
            return SoundTimeline(), []
        raise RuntimeError("gemini request failed")

    monkeypatch.setattr("v2a_inspect.ui.pipeline.plan_separation", fail)
    store = VideoAssetStore()
    asyncio.run(store.set_complete_asset(asset))

    asyncio.run(run_separation_planning_pipeline(asset, store))
    snapshot = asyncio.run(store.snapshot())

    assert snapshot.status == "failed" and snapshot.error.startswith("Could not plan the separation")
    assert snapshot.asset.audio_mode == "generate" and snapshot.asset.inactive_timeline is None


def test_bundle_round_trips_inactive_timeline(tmp_path: Path) -> None:
    root = tmp_path / "a" / "v2a-inspect-ui"
    (root / "uploads").mkdir(parents=True)
    (root / "uploads" / "v.mp4").write_bytes(b"x")
    separation = sp.to_timeline([_plan("Hit")], fps=30, frame_count=30)
    asset = VideoAsset(
        source_path=root / "uploads" / "v.mp4", frame_count=30, inactive_timeline=separation
    )
    buf = io.BytesIO()
    write_bundle(asset, root, buf)
    buf.seek(0)
    assert read_bundle(buf, tmp_path / "b" / "v2a-inspect-ui").inactive_timeline == separation


def _scene_track(box_px, frame=30) -> SceneTrack:
    point = SceneTrackPoint(frame_index=frame, bbox_xyxy=box_px, confidence=0.9)
    return SceneTrack(tracking_prompt="x", points=[point], confidence=0.9)


def _identity(*tracks: SceneTrack, labels: list[str] | None = None) -> VisualIdentityLayer:
    objects = [
        VisualObject(
            label=labels[i] if labels else f"obj{i}",
            presences=[VisualPresence(start_frame_index=0, end_frame_index=60, state="visible", scene_track_id=t.scene_track_id)],
        )
        for i, t in enumerate(tracks)
    ]
    return VisualIdentityLayer(visual_objects=objects)


def _link(plans, tracks, identity="auto"):
    timeline = sp.to_timeline(plans, fps=30, frame_count=60)
    identity = _identity(*tracks) if identity == "auto" else identity
    linked = sp.link_visual_objects(
        timeline, plans, tracks, identity, fps=30, frame_count=60, frame_size=(1280, 720)
    )
    return timeline, linked, identity


def _hinted(box, time=1.0, name="Dog") -> sp.SourcePlan:
    plan = _plan(name)
    return plan.model_copy(update={"visual_hint": sp.VisualHint(time=time, box=box, description="x")})


def test_link_picks_the_track_with_the_best_iou() -> None:
    left = _scene_track((0, 0, 640, 360))
    right = _scene_track((640, 0, 1280, 360))
    _, linked, identity = _link([_hinted((0.55, 0.0, 1.0, 0.5))], [left, right])  # frame 30, x-first normalized
    assert linked.sound_sources[0].visual_object_id == identity.visual_objects[1].visual_object_id
    assert linked.sound_sources[0].source_type == "visual_object"


def test_link_uses_a_point_within_two_frames_only() -> None:
    near = _scene_track((0, 0, 640, 360), frame=32)
    far = _scene_track((0, 0, 640, 360), frame=33)
    _, linked, identity = _link([_hinted((0, 0, 0.5, 0.5))], [far, near])
    assert linked.sound_sources[0].visual_object_id == identity.visual_objects[1].visual_object_id


def test_link_prefers_an_object_named_in_the_source() -> None:
    body = _scene_track((448, 0, 832, 720))  # the samurai around the clash: IoU 0.42
    blade = _scene_track((820, 300, 900, 340))  # the thin blade: IoU ~0.01
    identity = _identity(body, blade, labels=["black samurai", "katana sword"])
    _, linked, _ = _link([_hinted((0.35, 0.0, 0.65, 0.7), name="Clashing swords")], [body, blade], identity)
    assert linked.sound_sources[0].visual_object_id == identity.visual_objects[1].visual_object_id


def test_link_skips_other_kinds_when_the_named_object_is_not_tracked_then() -> None:
    pole = _scene_track((0, 60, 400, 370))  # overlaps the hint well, but it is a polearm
    samurai = _scene_track((0, 0, 560, 720), frame=50)  # the named object, tracked later only
    identity = _identity(pole, samurai, labels=["polearm", "red samurai"])
    _, linked, _ = _link([_hinted((0.0, 0.03, 0.44, 0.99), name="Red-armored samurai")], [pole, samurai], identity)
    assert linked.sound_sources[0].visual_object_id is None


def test_link_falls_back_to_the_only_box_containing_the_hint_center() -> None:
    big = _scene_track((0, 0, 1280, 720))
    # hint center (0.5, 0.5); IoU with the full-frame box is 0.04 < 0.1
    _, linked, identity = _link([_hinted((0.4, 0.4, 0.6, 0.6))], [big])
    assert linked.sound_sources[0].visual_object_id == identity.visual_objects[0].visual_object_id


def test_link_center_fallback_is_skipped_when_ambiguous() -> None:
    a, b = _scene_track((0, 0, 1280, 720)), _scene_track((0, 0, 1280, 720))
    _, linked, _ = _link([_hinted((0.4, 0.4, 0.6, 0.6))], [a, b])
    assert linked.sound_sources[0].visual_object_id is None


def test_link_leaves_unmatched_and_offscreen_sources_unlinked() -> None:
    corner = _scene_track((1200, 640, 1280, 720))
    plans = [_hinted((0.0, 0.0, 0.2, 0.2)), _plan("Siren", hint=False)]
    _, linked, _ = _link(plans, [corner])
    assert [s.visual_object_id for s in linked.sound_sources] == [None, None]
    assert linked.sound_sources[1].source_type == "offscreen_unknown"


def test_link_without_tracking_data_returns_the_timeline_unchanged() -> None:
    plans = [_hinted((0, 0, 0.5, 0.5))]
    timeline, no_tracks, _ = _link(plans, [])
    assert no_tracks is timeline
    track = _scene_track((0, 0, 640, 360))
    timeline, no_identity, _ = _link(plans, [track], identity=None)
    assert no_identity is timeline
