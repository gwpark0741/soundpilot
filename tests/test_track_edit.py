from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient
from scipy.io import wavfile

from v2a_inspect.audio_generation.clip import (
    MIN_CLIP_SEC,
    clamp_clip,
    render_clip_wav,
)
from v2a_inspect.models import (
    SoundEvent,
    SoundEventAudioArtifact,
    SoundSource,
    SoundTimeline,
    SoundTrack,
    SoundTrackAudioArtifact,
    TrackStatus,
    VideoAsset,
)
from v2a_inspect.ui.pipeline import (
    run_audio_generation_pipeline,
    run_audio_mode_switch,
    run_track_clip_pipeline,
    run_track_delete_pipeline,
    run_track_regeneration_pipeline,
)
from v2a_inspect.ui.routes import create_router
from v2a_inspect.ui.store import VideoAssetStore
from v2a_inspect.ui.track_edit import (
    LastEventError,
    add_track,
    delete_track,
    reset_clips,
    set_clip,
    set_event_spans,
    set_trim,
    trim_segments,
)

RATE = 1000  # 4 s video -> 4000 samples; the sample value is its index


def _ramp(path: Path, seconds: int = 4) -> Path:
    wavfile.write(path, RATE, np.arange(seconds * RATE, dtype=np.int16))
    return path


def _fake_stem(*args) -> Path:
    return _ramp(args[-1])


def _fake_mix(*, output_path: str, **_kwargs) -> str:
    Path(output_path).write_bytes(b"mp4")
    return output_path


def _stem(root: Path, track: SoundTrack, name: str) -> SoundTrackAudioArtifact:
    return SoundTrackAudioArtifact(
        sound_track_id=track.sound_track_id,
        track_label=track.label,
        track_type=track.track_type,
        path=_ramp(root / name),
        duration_sec=4.0,
        event_count=1,
        origin="generated",
        seed=1,
    )


def _asset(root: Path, labels=("Door", "Wind")) -> tuple[VideoAsset, list[SoundTrack]]:
    sources, tracks, events = [], [], []
    for label in labels:
        # A visual-object source routes to SelSync (faked in these tests), never to ElevenLabs.
        source = SoundSource(source_type="visual_object", label=label)
        track = SoundTrack(
            track_type="sfx",
            label=label,
            sound_source_id=source.sound_source_id,
            generation_model="selsync",
        )
        sources.append(source)
        tracks.append(track)
        events.append(
            SoundEvent(
                sound_track_id=track.sound_track_id,
                start_frame_index=0,
                end_frame_index=30,
                description=f"{label} event",
            )
        )
    asset = VideoAsset(
        source_path=root / "video.mp4",
        frame_count=120,  # 4 s
        sound_timeline=SoundTimeline(
            sound_sources=sources, sound_tracks=tracks, sound_events=events
        ),
        sound_track_audio_artifacts=[
            _stem(root, t, f"{t.label}.wav") for t in tracks
        ],
        track_status={t.sound_track_id: TrackStatus(state="done") for t in tracks},
    )
    return asset, tracks


def _with_clip(asset: VideoAsset, track: SoundTrack, **clip) -> VideoAsset:
    timeline = asset.sound_timeline
    tracks = [
        t.model_copy(update=clip) if t.sound_track_id == track.sound_track_id else t
        for t in timeline.sound_tracks
    ]
    return asset.model_copy(
        update={"sound_timeline": timeline.model_copy(update={"sound_tracks": tracks})}
    )


def _add_event(asset: VideoAsset, track: SoundTrack, start: int, end: int):
    """(asset with one more event on the track, that event)."""
    event = SoundEvent(
        sound_track_id=track.sound_track_id,
        start_frame_index=start,
        end_frame_index=end,
        description="extra",
    )
    timeline = asset.sound_timeline
    return (
        asset.model_copy(
            update={
                "sound_timeline": timeline.model_copy(
                    update={"sound_events": [*timeline.sound_events, event]}
                )
            }
        ),
        event,
    )


class ClipMathTest(unittest.TestCase):
    def test_clamp_keeps_window_inside_stem_and_video(self) -> None:
        kw = {"stem_duration": 4.0, "video_duration": 4.0}
        self.assertEqual(clamp_clip(-1, 99, 0, **kw), (0.0, 4.0, 0.0))
        self.assertEqual(clamp_clip(1, 2, 10, **kw), (1.0, 2.0, 2.0))  # 4 - end
        self.assertEqual(clamp_clip(1, 2, -10, **kw), (1.0, 2.0, -1.0))  # -start
        start, end, _ = clamp_clip(3.95, 3.96, 0, **kw)
        self.assertAlmostEqual(end - start, MIN_CLIP_SEC)
        self.assertLessEqual(end, 4.0)
        with self.assertRaises(ValueError):
            clamp_clip(0, 0.05, 0, stem_duration=0.05, video_duration=4.0)

    def test_clamp_stem_longer_than_video_stays_in_video(self) -> None:
        start, end, offset = clamp_clip(
            0, 6, 3, stem_duration=6.0, video_duration=4.0
        )
        self.assertEqual((start, end, offset), (0.0, 4.0, 0.0))

    def test_render_places_the_slice_at_start_plus_offset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            render_clip_wav(
                _ramp(root / "in.wav"),
                root / "out.wav",
                start=1.0,
                end=2.5,
                offset=0.5,
                video_duration=4.0,
            )
            rate, out = wavfile.read(root / "out.wav")
            self.assertEqual((rate, len(out)), (RATE, 4000))
            self.assertTrue(np.all(out[:1500] == 0))
            # The slice is untouched except for the 5 ms (5 sample) fades at both ends.
            self.assertTrue(np.array_equal(out[1505:2995], np.arange(1005, 2495)))
            self.assertTrue(np.all(out[3000:] == 0))
            self.assertEqual((out[1500], out[2999]), (0, 0))
            self.assertTrue(np.all(np.diff(out[1500:1506]) > 0))
            self.assertTrue(np.all(np.diff(out[2994:3000]) < 0))
            self.assertLess(out[1502], 1002)

    def test_render_moves_before_the_stem_start(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            render_clip_wav(
                _ramp(root / "in.wav"),
                root / "out.wav",
                start=2.0,
                end=3.0,
                offset=-2.0,
                video_duration=4.0,
            )
            _, out = wavfile.read(root / "out.wav")
            self.assertTrue(np.array_equal(out[5:995], np.arange(2005, 2995)))
            self.assertEqual((out[0], out[999]), (0, 0))
            self.assertTrue(np.all(out[1000:] == 0))


class TrimGateRenderTest(unittest.TestCase):
    def test_segments_keep_inside_silence_outside_and_fade_each_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            render_clip_wav(
                _ramp(root / "in.wav"),
                root / "out.wav",
                start=0.0,
                end=4.0,
                offset=0.0,
                video_duration=4.0,
                segments=[(0.5, 1.0), (2.0, 3.0)],
            )
            _, out = wavfile.read(root / "out.wav")
            self.assertEqual(len(out), 4000)
            for a, b in ((500, 1000), (2000, 3000)):
                self.assertTrue(np.array_equal(out[a + 5 : b - 5], np.arange(a + 5, b - 5)))
                self.assertEqual((out[a], out[b - 1]), (0, 0))
                self.assertTrue(np.all(np.diff(out[a : a + 6]) > 0))  # 5 ms ramp in
                self.assertTrue(np.all(np.diff(out[b - 6 : b]) < 0))  # and out
            for a, b in ((0, 500), (1000, 2000), (3000, 4000)):
                self.assertTrue(np.all(out[a:b] == 0))

    def test_segments_apply_after_the_clip_and_no_gate_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            src = _ramp(root / "in.wav")
            kw = {"start": 1.0, "end": 3.0, "offset": 0.0, "video_duration": 4.0}
            render_clip_wav(src, root / "plain.wav", **kw)
            render_clip_wav(src, root / "gated.wav", segments=[(0.0, 2.0)], **kw)
            _, plain = wavfile.read(root / "plain.wav")
            _, gated = wavfile.read(root / "gated.wav")
            # The clip plays 1..3 s; the gate keeps only 1..2 s of it.
            self.assertTrue(np.array_equal(gated[1005:1995], plain[1005:1995]))
            self.assertTrue(np.all(gated[:1000] == 0) and np.all(gated[2000:] == 0))
            self.assertTrue(np.array_equal(plain[1005:2995], np.arange(1005, 2995)))


class TrackEditTest(unittest.TestCase):
    def test_set_clip_stores_clamped_values_and_full_extent_clears(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            asset, (door, wind) = _asset(Path(temp_dir))
            clipped = set_clip(asset, door.sound_track_id, 0.5, 9.0, 9.0)
            track = clipped.sound_timeline.sound_tracks[0]
            self.assertEqual(
                (track.clip_start_sec, track.clip_end_sec, track.clip_offset_sec),
                (0.5, 4.0, 0.0),  # offset limited so the clip ends at the video end
            )
            self.assertFalse(clipped.sound_timeline.sound_tracks[1].has_clip)
            # Raw stem and peaks stay untouched.
            self.assertEqual(
                clipped.sound_track_audio_artifacts, asset.sound_track_audio_artifacts
            )

            cleared = set_clip(clipped, door.sound_track_id, 0.0, 4.0, 0.0)
            self.assertFalse(cleared.sound_timeline.sound_tracks[0].has_clip)

    def test_set_clip_full_extent_within_a_millisecond_clears(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            asset, (door, _) = _asset(Path(temp_dir))
            rounded = asset.model_copy(
                update={
                    "sound_track_audio_artifacts": [
                        a.model_copy(update={"duration_sec": 3.767})
                        for a in asset.sound_track_audio_artifacts
                    ]
                }
            )
            trimmed = set_clip(rounded, door.sound_track_id, 0.5, 3.0, 0.0)
            self.assertTrue(trimmed.sound_timeline.sound_tracks[0].has_clip)
            cleared = set_clip(trimmed, door.sound_track_id, 0.0, 3.7667, 0.0)
            self.assertFalse(cleared.sound_timeline.sound_tracks[0].has_clip)

    def test_set_clip_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            asset, (door, _) = _asset(Path(temp_dir))
            with self.assertRaises(LookupError):
                set_clip(asset, uuid4(), 0, 1, 0)
            silent = asset.model_copy(update={"sound_track_audio_artifacts": []})
            with self.assertRaises(ValueError):
                set_clip(silent, door.sound_track_id, 0, 1, 0)

    def test_delete_removes_track_events_orphan_source_and_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset, (door, wind) = _asset(root)
            event_artifact = SoundEventAudioArtifact(
                sound_event_id=asset.sound_timeline.sound_events[0].sound_event_id,
                sound_track_id=door.sound_track_id,
                path=root / "e.wav",
                duration_sec=1.0,
                generation_model="t2a",
                description="x",
            )
            asset = asset.model_copy(
                update={
                    "sound_event_audio_artifacts": [event_artifact],
                    "separated_stem_paths": {
                        door.sound_track_id: root / "a.wav",
                        wind.sound_track_id: root / "b.wav",
                    },
                }
            )
            result = delete_track(asset, door.sound_track_id)
            timeline = result.sound_timeline

            self.assertEqual([t.label for t in timeline.sound_tracks], ["Wind"])
            self.assertEqual([e.description for e in timeline.sound_events], ["Wind event"])
            self.assertEqual([s.label for s in timeline.sound_sources], ["Wind"])
            self.assertEqual(
                [a.sound_track_id for a in result.sound_track_audio_artifacts],
                [wind.sound_track_id],
            )
            self.assertEqual(result.sound_event_audio_artifacts, [])
            self.assertEqual(list(result.track_status), [wind.sound_track_id])
            self.assertEqual(list(result.separated_stem_paths), [wind.sound_track_id])
            with self.assertRaises(LookupError):
                delete_track(result, door.sound_track_id)

    def test_delete_keeps_a_source_another_track_still_uses(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            asset, (door, wind) = _asset(Path(temp_dir))
            shared = asset.sound_timeline.sound_sources[0]
            timeline = asset.sound_timeline
            tracks = [
                t.model_copy(update={"sound_source_id": shared.sound_source_id})
                for t in timeline.sound_tracks
            ]
            asset = asset.model_copy(
                update={"sound_timeline": timeline.model_copy(update={"sound_tracks": tracks})}
            )
            result = delete_track(asset, door.sound_track_id)
            self.assertIn(
                shared.sound_source_id,
                [s.sound_source_id for s in result.sound_timeline.sound_sources],
            )

    def test_add_track_sets_model_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            asset, _ = _asset(Path(temp_dir))
            expected = {
                "selsync": ("sfx", "selsync", "v2a", "visual_object"),
                "t2a": ("sfx", "t2a", "t2a", "offscreen_unknown"),
                "tts": ("speech", "tts", "tts", "offscreen_unknown"),
                "separate": ("sfx", "separate", "t2a", "offscreen_unknown"),
            }
            for model, (ttype, gen, agent, source_type) in expected.items():
                result, track = add_track(
                    asset,
                    label=f" New {model} ",
                    description=" a sound ",
                    model=model,
                    spoken_text="Hello" if model == "tts" else "ignored",
                    spans=[(10, 500)],
                )
                timeline = result.sound_timeline
                self.assertEqual(len(timeline.sound_tracks), 3)
                self.assertEqual(
                    (track.track_type, track.generation_model, track.agent_model),
                    (ttype, gen, agent),
                )
                self.assertEqual(track.label, f"New {model}")
                self.assertEqual(track.description, "a sound")
                source = timeline.sound_sources[-1]
                self.assertEqual(source.source_type, source_type)
                self.assertEqual(track.sound_source_id, source.sound_source_id)
                event = timeline.sound_events[-1]
                self.assertEqual(event.sound_track_id, track.sound_track_id)
                self.assertEqual((event.start_frame_index, event.end_frame_index), (10, 120))
                self.assertEqual(event.description, "a sound")
                self.assertEqual(event.spoken_text, "Hello" if model == "tts" else None)
                SoundTimeline.model_validate(timeline.model_dump(mode="json"))

    def test_add_track_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            asset, _ = _asset(Path(temp_dir))
            ok = {"label": "A", "description": "b", "model": "t2a",
                  "spoken_text": None, "spans": [(0, 30)]}
            for bad in (
                {"label": " "},
                {"description": ""},
                {"model": "tts"},  # speech needs spoken text
                {"spans": []},
                {"spans": [(120, 130)]},
                {"spans": [(30, 30)]},
                {"spans": [(0, 30), (-1, 5)]},
            ):
                with self.assertRaises(ValueError, msg=str(bad)):
                    add_track(asset, **{**ok, **bad})

    def test_add_track_makes_one_event_per_span_sorted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            asset, _ = _asset(Path(temp_dir))
            result, track = add_track(
                asset,
                label="Steps",
                description="steps",
                model="tts",
                spoken_text="Hi",
                spans=[(60, 90), (0, 30), (100, 500)],
            )
            events = [
                e for e in result.sound_timeline.sound_events
                if e.sound_track_id == track.sound_track_id
            ]
            self.assertEqual(
                [(e.start_frame_index, e.end_frame_index) for e in events],
                [(0, 30), (60, 90), (100, 120)],  # sorted, end clamped to the video
            )
            self.assertEqual({e.spoken_text for e in events}, {"Hi"})
            self.assertEqual(len({e.sound_event_id for e in events}), 3)

    def test_set_event_spans_applies_and_validates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            asset, (door, wind) = _asset(Path(temp_dir))
            events = asset.sound_timeline.sound_events
            door_event, wind_event = events[0].sound_event_id, events[1].sound_event_id
            result = set_event_spans(asset, door.sound_track_id, {door_event: (10, 120)})
            spans = {
                e.sound_event_id: (e.start_frame_index, e.end_frame_index)
                for e in result.sound_timeline.sound_events
            }
            self.assertEqual(spans, {door_event: (10, 120), wind_event: (0, 30)})
            for bad in ({wind_event: (1, 2)}, {door_event: (5, 121)}, {door_event: (5, 5)}, {door_event: (-1, 5)}):
                with self.assertRaises(ValueError, msg=str(bad)):
                    set_event_spans(asset, door.sound_track_id, bad)

    def test_trim_segments_clamp_and_merge_overlaps(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            asset, (door, _) = _asset(Path(temp_dir))  # Door has (0, 30)
            asset, _ = _add_event(asset, door, 20, 45)  # overlaps the first
            asset, _ = _add_event(asset, door, 60, 90)
            asset, _ = _add_event(asset, door, 110, 120)
            self.assertEqual(
                trim_segments(asset, door.sound_track_id),
                [(0.0, 1.5), (2.0, 3.0), (110 / 30, 4.0)],
            )

    def test_trim_gate_counts_as_clip_and_reset_clears_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            asset, (door, wind) = _asset(Path(temp_dir))
            trimmed = set_trim(asset, door.sound_track_id, True)
            track = trimmed.sound_timeline.sound_tracks[0]
            self.assertEqual(track.clip_segments, [(0.0, 1.0)])
            self.assertTrue(track.has_clip)
            self.assertFalse(trimmed.sound_timeline.sound_tracks[1].has_clip)
            # Orthogonal to the single clip: moving the clip keeps the gate.
            clipped = set_clip(trimmed, door.sound_track_id, 0.0, 2.0, 0.0)
            self.assertEqual(clipped.sound_timeline.sound_tracks[0].clip_segments, [(0.0, 1.0)])
            # Only the given tracks are reset.
            kept = reset_clips(trimmed.sound_timeline, {wind.sound_track_id})
            self.assertIsNotNone(kept.sound_tracks[0].clip_segments)
            cleared = reset_clips(trimmed.sound_timeline)
            self.assertIsNone(cleared.sound_tracks[0].clip_segments)
            self.assertFalse(cleared.sound_tracks[0].has_clip)
            undone = set_trim(trimmed, door.sound_track_id, False)
            self.assertFalse(undone.sound_timeline.sound_tracks[0].has_clip)
            with self.assertRaises(ValueError):
                set_trim(asset.model_copy(update={"sound_track_audio_artifacts": []}), door.sound_track_id, True)
            with self.assertRaises(LookupError):
                set_trim(asset, uuid4(), True)

    def test_set_event_spans_deletes_events_and_their_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset, (door, wind) = _asset(root)
            asset, extra = _add_event(asset, door, 60, 90)
            first = asset.sound_timeline.sound_events[0].sound_event_id
            artifact = SoundEventAudioArtifact(
                sound_event_id=extra.sound_event_id,
                sound_track_id=door.sound_track_id,
                path=root / "extra.wav",
                duration_sec=1.0,
                generation_model="t2a",
                description="extra",
            )
            asset = asset.model_copy(update={"sound_event_audio_artifacts": [artifact]})
            result = set_event_spans(
                asset,
                door.sound_track_id,
                {first: (0, 90)},  # the kept box stretches over the removed box's range
                frozenset({extra.sound_event_id}),
            )
            spans = {e.sound_event_id: (e.start_frame_index, e.end_frame_index) for e in result.sound_timeline.sound_events}
            self.assertNotIn(extra.sound_event_id, spans)
            self.assertEqual(spans[first], (0, 90))
            self.assertEqual(len(spans), 2)  # Door's first + Wind's
            self.assertEqual(result.sound_event_audio_artifacts, [])
            # Deleting before any audio exists works the same.
            no_audio = asset.model_copy(update={"sound_event_audio_artifacts": [], "sound_track_audio_artifacts": []})
            self.assertEqual(
                len(set_event_spans(no_audio, door.sound_track_id, {}, frozenset({extra.sound_event_id})).sound_timeline.sound_events),
                2,
            )

    def test_set_event_spans_rejects_deleting_the_last_or_a_foreign_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            asset, (door, wind) = _asset(Path(temp_dir))
            door_event, wind_event = (e.sound_event_id for e in asset.sound_timeline.sound_events)
            with self.assertRaises(LastEventError):
                set_event_spans(asset, door.sound_track_id, {}, frozenset({door_event}))
            with self.assertRaises(ValueError):
                set_event_spans(asset, door.sound_track_id, {}, frozenset({wind_event}))

    def test_old_track_json_loads_without_clip_fields(self) -> None:
        track = SoundTrack.model_validate(
            {"track_type": "sfx", "label": "Old", "generation_model": "t2a"}
        )
        self.assertFalse(track.has_clip)


class TrackEditPipelineTest(unittest.TestCase):
    def test_clip_pipeline_mixes_and_serves_the_clipped_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset, (door, wind) = _asset(root)
            asset = set_clip(asset, door.sound_track_id, 1.0, 2.5, 0.5)
            store = VideoAssetStore()
            with patch(
                "v2a_inspect.ui.pipeline.mix_audio_into_video", side_effect=_fake_mix
            ) as mix:
                asyncio.run(run_track_clip_pipeline(asset, store))
            snapshot = asyncio.run(store.snapshot())

            self.assertEqual(snapshot.status, "complete")
            by_track = {a.sound_track_id: a for a in snapshot.asset.sound_track_audio_artifacts}
            door_art, wind_art = by_track[door.sound_track_id], by_track[wind.sound_track_id]
            self.assertEqual(door_art.path, root / "Door.wav")  # raw stem untouched
            self.assertTrue(door_art.clip_path.exists())
            self.assertIsNone(wind_art.clip_path)
            mixed = mix.call_args.kwargs["generated_audio"]
            self.assertEqual(mixed[str(door.sound_track_id)], str(door_art.clip_path))
            self.assertEqual(mixed[str(wind.sound_track_id)], str(wind_art.path))
            _, out = wavfile.read(door_art.clip_path)
            self.assertTrue(np.array_equal(out[1505:2995], np.arange(1005, 2495)))  # faded ends

            app = FastAPI()
            app.include_router(create_router(store))
            body = TestClient(app).get(f"/api/audio/tracks/{door.sound_track_id}").content
            self.assertEqual(body, door_art.clip_path.read_bytes())

    def test_mode_switch_rebuilds_the_preview_only_when_the_new_timeline_has_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset, _ = _asset(root)
            other = SoundTimeline(
                sound_tracks=[SoundTrack(track_type="sfx", label="Rain", generation_model="separate")]
            )
            asset = asset.model_copy(
                update={"inactive_timeline": other, "synthesized_video_path": root / "old.mp4"}
            )
            store = VideoAssetStore()
            with patch(
                "v2a_inspect.ui.pipeline.mix_audio_into_video", side_effect=_fake_mix
            ) as mix:
                asyncio.run(run_audio_mode_switch(asset, store, "separate"))
                separate = asyncio.run(store.snapshot()).asset
                mix.assert_not_called()  # no artifact belongs to the Rain track
                self.assertIsNone(separate.synthesized_video_path)
                self.assertEqual(
                    separate.sound_track_audio_artifacts, asset.sound_track_audio_artifacts
                )

                asyncio.run(run_audio_mode_switch(separate, store, "generate"))
            snapshot = asyncio.run(store.snapshot())
            mix.assert_called_once()
            self.assertEqual(snapshot.status, "complete")
            self.assertEqual(snapshot.asset.audio_mode, "generate")
            self.assertTrue(snapshot.asset.synthesized_video_path.exists())

    def test_trim_pipeline_renders_the_gate_and_undo_drops_the_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset, (door, _) = _asset(root)
            asset = set_trim(asset, door.sound_track_id, True)
            store = VideoAssetStore()
            with patch("v2a_inspect.ui.pipeline.mix_audio_into_video", side_effect=_fake_mix):
                asyncio.run(run_track_clip_pipeline(asset, store))
                art = asyncio.run(store.snapshot()).asset.sound_track_audio_artifacts[0]
                _, out = wavfile.read(art.clip_path)
                self.assertTrue(np.all(out[1000:] == 0))
                self.assertTrue(np.array_equal(out[5:995], np.arange(5, 995)))
                undone = set_trim(asyncio.run(store.snapshot()).asset, door.sound_track_id, False)
                asyncio.run(run_track_clip_pipeline(undone, store))
            art = asyncio.run(store.snapshot()).asset.sound_track_audio_artifacts[0]
            self.assertIsNone(art.clip_path)
            self.assertFalse((root / "Door.clip.wav").exists())

    def test_removing_the_clip_drops_the_edited_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset, (door, _) = _asset(root)
            store = VideoAssetStore()
            with patch(
                "v2a_inspect.ui.pipeline.mix_audio_into_video", side_effect=_fake_mix
            ):
                asyncio.run(
                    run_track_clip_pipeline(set_clip(asset, door.sound_track_id, 1, 2, 0), store)
                )
                clipped = asyncio.run(store.snapshot()).asset
                edited = clipped.sound_track_audio_artifacts[0].clip_path
                self.assertTrue(edited.exists())
                asyncio.run(
                    run_track_clip_pipeline(set_clip(clipped, door.sound_track_id, 0, 4, 0), store)
                )
            self.assertFalse(edited.exists())
            self.assertIsNone(
                asyncio.run(store.snapshot()).asset.sound_track_audio_artifacts[0].clip_path
            )

    def test_delete_pipeline_rebuilds_preview_and_removes_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset, (door, wind) = _asset(root)
            store = VideoAssetStore()
            with patch(
                "v2a_inspect.ui.pipeline.mix_audio_into_video", side_effect=_fake_mix
            ) as mix:
                asyncio.run(run_track_delete_pipeline(asset, store, str(door.sound_track_id)))
                snapshot = asyncio.run(store.snapshot())
                self.assertEqual(snapshot.status, "complete")
                self.assertEqual(
                    list(mix.call_args.kwargs["generated_audio"]), [str(wind.sound_track_id)]
                )
                self.assertFalse((root / "Door.wav").exists())
                self.assertTrue((root / "Wind.wav").exists())
                self.assertIsNotNone(snapshot.asset.synthesized_video_path)
                self.assertEqual(
                    [t.label for t in snapshot.asset.sound_timeline.sound_tracks], ["Wind"]
                )

                asyncio.run(
                    run_track_delete_pipeline(snapshot.asset, store, str(wind.sound_track_id))
                )
            last = asyncio.run(store.snapshot()).asset
            self.assertEqual(last.sound_timeline.sound_tracks, [])
            self.assertIsNone(last.synthesized_video_path)  # no audio left
            self.assertEqual(last.sound_track_audio_artifacts, [])

    def test_regenerate_resets_only_that_tracks_clip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset, (door, wind) = _asset(root)
            asset = set_clip(asset, door.sound_track_id, 1, 2, 0)
            asset = set_clip(asset, wind.sound_track_id, 0, 3, 0.5)
            store = VideoAssetStore()
            with (
                patch("v2a_inspect.ui.pipeline.generate_track", side_effect=_fake_stem),
                patch("v2a_inspect.ui.pipeline.mix_audio_into_video", side_effect=_fake_mix),
            ):
                asyncio.run(
                    run_track_regeneration_pipeline(
                        asset, store, str(door.sound_track_id), "generate", None, 5
                    )
                )
            snapshot = asyncio.run(store.snapshot())

            self.assertEqual(snapshot.status, "complete")
            door_t, wind_t = snapshot.asset.sound_timeline.sound_tracks
            self.assertFalse(door_t.has_clip)
            self.assertEqual((wind_t.clip_start_sec, wind_t.clip_end_sec, wind_t.clip_offset_sec), (0.0, 3.0, 0.5))
            by_track = {a.sound_track_id: a for a in snapshot.asset.sound_track_audio_artifacts}
            self.assertIsNone(by_track[door.sound_track_id].clip_path)
            self.assertIsNotNone(by_track[wind.sound_track_id].clip_path)

    def _event_level(self, root: Path):
        """Door as an event-level (t2a) track with one event artifact and no stem."""
        asset, (door, wind) = _asset(root)
        timeline = asset.sound_timeline
        tracks = [
            t.model_copy(update={"generation_model": "t2a", "agent_model": "t2a"})
            if t.sound_track_id == door.sound_track_id
            else t
            for t in timeline.sound_tracks
        ]
        event = timeline.sound_events[0]
        art = SoundEventAudioArtifact(
            sound_event_id=event.sound_event_id,
            sound_track_id=door.sound_track_id,
            path=_ramp(root / "door-event.wav", 1),
            duration_sec=1.0,
            generation_model="t2a",
            description="Door event",
        )
        asset = asset.model_copy(
            update={
                "sound_timeline": timeline.model_copy(update={"sound_tracks": tracks}),
                "sound_event_audio_artifacts": [art],
                "sound_track_audio_artifacts": [
                    a for a in asset.sound_track_audio_artifacts if a.sound_track_id != door.sound_track_id
                ],
            }
        )
        return asset, door, art

    def test_model_pick_switches_event_level_to_selsync_and_drops_event_clips(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            asset, door, art = self._event_level(Path(temp_dir))
            store = VideoAssetStore()
            with (
                patch("v2a_inspect.ui.pipeline.generate_track", side_effect=_fake_stem),
                patch("v2a_inspect.ui.pipeline.mix_audio_into_video", side_effect=_fake_mix),
            ):
                asyncio.run(
                    run_track_regeneration_pipeline(
                        asset, store, str(door.sound_track_id), "generate", "new text", 5, "selsync"
                    )
                )
            snap = asyncio.run(store.snapshot())
            self.assertEqual(snap.status, "complete")
            track = snap.asset.sound_timeline.sound_tracks[0]
            self.assertEqual((track.generation_model, track.agent_model), ("selsync", "v2a"))
            self.assertEqual(track.description, "new text")
            self.assertEqual(snap.asset.sound_event_audio_artifacts, [])
            stem = next(a for a in snap.asset.sound_track_audio_artifacts if a.sound_track_id == door.sound_track_id)
            self.assertEqual(stem.origin, "generated")

    def test_model_pick_switches_selsync_to_event_level_and_replaces_the_stem(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset, (door, _) = _asset(root)
            event = asset.sound_timeline.sound_events[0]
            art = SoundEventAudioArtifact(
                sound_event_id=event.sound_event_id,
                sound_track_id=door.sound_track_id,
                path=_ramp(root / "new-event.wav", 1),
                duration_sec=1.0,
                generation_model="t2a",
                description="Door event",
            )
            store = VideoAssetStore()
            with (
                patch(
                    "v2a_inspect.ui.pipeline._generate_track_event_artifacts",
                    new=AsyncMock(return_value=[art]),
                ) as gen,
                patch("v2a_inspect.ui.pipeline.mix_audio_into_video", side_effect=_fake_mix),
            ):
                asyncio.run(
                    run_track_regeneration_pipeline(
                        asset, store, str(door.sound_track_id), "generate", None, 5, "t2a"
                    )
                )
            snap = asyncio.run(store.snapshot())
            gen.assert_awaited_once()
            self.assertEqual(snap.status, "complete")
            track = snap.asset.sound_timeline.sound_tracks[0]
            self.assertEqual((track.generation_model, track.agent_model), ("t2a", "t2a"))
            self.assertEqual([a.sound_event_id for a in snap.asset.sound_event_audio_artifacts], [event.sound_event_id])
            door_stems = [a for a in snap.asset.sound_track_audio_artifacts if a.sound_track_id == door.sound_track_id]
            self.assertTrue(door_stems and all(a.origin == "mixed" for a in door_stems))

    def test_failed_model_switch_keeps_the_previous_model_and_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            asset, door, art = self._event_level(Path(temp_dir))
            store = VideoAssetStore()
            with (
                patch("v2a_inspect.ui.pipeline.generate_track", side_effect=RuntimeError("boom")),
                patch("v2a_inspect.ui.pipeline.mix_audio_into_video", side_effect=_fake_mix),
            ):
                asyncio.run(
                    run_track_regeneration_pipeline(
                        asset, store, str(door.sound_track_id), "generate", None, 5, "selsync"
                    )
                )
            snap = asyncio.run(store.snapshot())
            self.assertEqual(snap.status, "failed")
            track = snap.asset.sound_timeline.sound_tracks[0]
            self.assertEqual((track.generation_model, track.agent_model), ("t2a", "t2a"))
            self.assertEqual(snap.asset.sound_event_audio_artifacts, [art])
            self.assertEqual(snap.asset.track_status[door.sound_track_id].state, "failed")

    def test_silent_selsync_regeneration_keeps_the_audio_and_flags_the_track(self) -> None:
        def silent_stem(*args) -> Path:
            wavfile.write(args[-1], RATE, np.zeros(4 * RATE, dtype=np.int16))
            return args[-1]

        with tempfile.TemporaryDirectory() as temp_dir:
            asset, (door, _) = _asset(Path(temp_dir))
            timeline = asset.sound_timeline
            asset = asset.model_copy(  # SelSync only routes visual-object sources
                update={
                    "sound_timeline": timeline.model_copy(
                        update={
                            "sound_sources": [
                                s.model_copy(update={"source_type": "visual_object"})
                                for s in timeline.sound_sources
                            ]
                        }
                    )
                }
            )
            store = VideoAssetStore()
            with (
                patch("v2a_inspect.ui.pipeline.generate_track", side_effect=silent_stem) as gen,
                patch("v2a_inspect.ui.pipeline.mix_audio_into_video", side_effect=_fake_mix),
            ):
                asyncio.run(
                    run_track_regeneration_pipeline(
                        asset, store, str(door.sound_track_id), "generate", None, 5
                    )
                )
            snapshot = asyncio.run(store.snapshot())

            gen.assert_called_once()  # no fallback to another model
            self.assertEqual(snapshot.status, "complete")
            status = snapshot.asset.track_status[door.sound_track_id]
            self.assertEqual(status.state, "failed")
            self.assertIn("SelSync returned silence", status.error)
            track = snapshot.asset.sound_timeline.sound_tracks[0]
            self.assertEqual(track.generation_model, "selsync")
            stem = next(
                a for a in snapshot.asset.sound_track_audio_artifacts
                if a.sound_track_id == door.sound_track_id
            )
            self.assertEqual(stem.origin, "generated")  # the new audio stays
            self.assertEqual(
                snapshot.asset.track_status[next(
                    t.sound_track_id for t in snapshot.asset.sound_timeline.sound_tracks
                    if t.label == "Wind"
                )].state,
                "done",
            )

    def test_full_generation_resets_every_clip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset, (door, wind) = _asset(root)
            asset = set_clip(asset, door.sound_track_id, 1, 2, 0)
            asset = set_clip(asset, wind.sound_track_id, 0, 3, 0.5)
            asset = asset.model_copy(update={"raw_video_path": root / "raw.mp4"})
            store = VideoAssetStore()
            with (
                patch("v2a_inspect.ui.pipeline.generate_track", side_effect=_fake_stem),
                patch("v2a_inspect.ui.pipeline.mix_audio_into_video", side_effect=_fake_mix),
            ):
                asyncio.run(run_audio_generation_pipeline(asset, store, None))
            snapshot = asyncio.run(store.snapshot())

            self.assertEqual(snapshot.status, "complete")
            self.assertFalse(any(t.has_clip for t in snapshot.asset.sound_timeline.sound_tracks))
            self.assertTrue(
                all(a.clip_path is None for a in snapshot.asset.sound_track_audio_artifacts)
            )


class TrackEditRouteTest(unittest.TestCase):
    def _client(self, asset: VideoAsset | None, *, running: bool = False):
        store = VideoAssetStore()
        if asset is not None:
            asyncio.run(store.set_complete_asset(asset))
        if running:
            asyncio.run(store.set_running(stage="busy"))
        app = FastAPI()
        app.include_router(create_router(store))
        return TestClient(app), store

    def test_clip_route_queues_validates_and_guards(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            asset, (door, _) = _asset(Path(temp_dir))
            body = {"start": 1.0, "end": 2.0, "offset": 99.0}
            url = f"/api/audio/tracks/{door.sound_track_id}/clip"

            client, store = self._client(asset)
            with patch("v2a_inspect.ui.routes.run_track_clip_pipeline") as pipeline:
                response = client.post(url, json=body)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                response.json(),
                {"status": "queued", "clip": {"start": 1.0, "end": 2.0, "offset": 2.0}},
            )
            queued = pipeline.call_args.args[0]
            self.assertEqual(queued.sound_timeline.sound_tracks[0].clip_offset_sec, 2.0)
            self.assertEqual(
                asyncio.run(store.snapshot()).asset.sound_timeline.sound_tracks[0].clip_end_sec,
                2.0,
            )

            busy, _ = self._client(asset, running=True)
            self.assertEqual(busy.post(url, json=body).status_code, 409)
            empty, _ = self._client(None)
            self.assertEqual(empty.post(url, json=body).status_code, 404)
            client, _ = self._client(asset)
            for bad_id in (uuid4(), "nope"):
                self.assertEqual(
                    client.post(f"/api/audio/tracks/{bad_id}/clip", json=body).status_code, 404
                )
            silent = asset.model_copy(update={"sound_track_audio_artifacts": []})
            client, _ = self._client(silent)
            self.assertEqual(client.post(url, json=body).status_code, 422)

    def test_delete_route_queues_and_guards(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            asset, (door, _) = _asset(Path(temp_dir))
            url = f"/api/audio/tracks/{door.sound_track_id}"

            client, _ = self._client(asset)
            with patch("v2a_inspect.ui.routes.run_track_delete_pipeline") as pipeline:
                response = client.delete(url)
            self.assertEqual(response.json(), {"status": "queued"})
            self.assertEqual(pipeline.call_args.args[2], str(door.sound_track_id))

            busy, _ = self._client(asset, running=True)
            self.assertEqual(busy.delete(url).status_code, 409)
            empty, _ = self._client(None)
            self.assertEqual(empty.delete(url).status_code, 404)
            client, _ = self._client(asset)
            self.assertEqual(client.delete(f"/api/audio/tracks/{uuid4()}").status_code, 404)

    def test_regenerate_route_applies_event_spans_to_that_track_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset, (door, wind) = _asset(root)
            door_event, wind_event = (e.sound_event_id for e in asset.sound_timeline.sound_events)
            url = f"/api/audio/tracks/{door.sound_track_id}/regenerate"

            def spans(snapshot):
                return {
                    e.sound_event_id: (e.start_frame_index, e.end_frame_index)
                    for e in snapshot.asset.sound_timeline.sound_events
                }

            client, store = self._client(asset)
            events = json.dumps([{"sound_event_id": str(door_event), "start_frame": 40, "end_frame": 100}])
            with (
                patch("v2a_inspect.ui.pipeline.generate_track", side_effect=_fake_stem),
                patch("v2a_inspect.ui.pipeline.mix_audio_into_video", side_effect=_fake_mix),
            ):
                response = client.post(url, data={"method": "generate", "events": events})
            snapshot = asyncio.run(store.snapshot())
            self.assertEqual(response.status_code, 200)
            self.assertEqual(snapshot.status, "complete")  # the pipeline ran and saved the asset
            self.assertEqual(spans(snapshot), {door_event: (40, 100), wind_event: (0, 30)})

            # Rejected before anything is queued: another track's event, out of range, start >= end, bad JSON.
            for bad in (
                [{"sound_event_id": str(wind_event), "start_frame": 1, "end_frame": 9}],
                [{"sound_event_id": str(door_event), "start_frame": 0, "end_frame": 121}],
                [{"sound_event_id": str(door_event), "start_frame": 9, "end_frame": 9}],
                [{"sound_event_id": str(door_event), "start_frame": -1, "end_frame": 9}],
                [{"sound_event_id": "nope", "start_frame": 1, "end_frame": 9}],
                {"not": "a list"},
            ):
                client, store = self._client(asset)
                with patch("v2a_inspect.ui.routes.run_track_regeneration_pipeline") as pipeline:
                    response = client.post(url, data={"method": "generate", "events": json.dumps(bad)})
                self.assertEqual(response.status_code, 422, msg=str(bad))
                pipeline.assert_not_called()
                self.assertEqual(spans(asyncio.run(store.snapshot())), {door_event: (0, 30), wind_event: (0, 30)})
            client, _ = self._client(asset)
            self.assertEqual(client.post(url, data={"method": "generate", "events": "{"}).status_code, 422)

    def test_trim_route_with_and_without_events_then_undo(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            asset, (door, wind) = _asset(Path(temp_dir))
            asset, extra = _add_event(asset, door, 60, 90)
            first = asset.sound_timeline.sound_events[0].sound_event_id
            url = f"/api/audio/tracks/{door.sound_track_id}/trim-to-spans"

            def door_track(snapshot):
                return snapshot.asset.sound_timeline.sound_tracks[0]

            client, store = self._client(asset)
            with patch("v2a_inspect.ui.routes.run_track_clip_pipeline") as pipeline:
                response = client.post(url)
            self.assertEqual(response.json(), {"status": "queued"})
            pipeline.assert_called_once()
            self.assertEqual(door_track(asyncio.run(store.snapshot())).clip_segments, [(0.0, 1.0), (2.0, 3.0)])

            # With pending span edits: a move and a deletion are applied first, without regenerating.
            events = json.dumps(
                [
                    {"sound_event_id": str(first), "start_frame": 0, "end_frame": 45},
                    {"sound_event_id": str(extra.sound_event_id), "start_frame": 60, "end_frame": 90, "deleted": True},
                ]
            )
            client, store = self._client(asset)
            with patch("v2a_inspect.ui.routes.run_track_clip_pipeline") as pipeline:
                response = client.post(url, data={"events": events})
            self.assertEqual(response.status_code, 200)
            snapshot = asyncio.run(store.snapshot())
            self.assertEqual(door_track(snapshot).clip_segments, [(0.0, 1.5)])
            self.assertEqual(len(snapshot.asset.sound_timeline.sound_events), 2)
            self.assertEqual(pipeline.call_args.args[0].sound_timeline.sound_tracks[0].clip_segments, [(0.0, 1.5)])

            # Undo clears the gate (the patched pipeline left the store "running", so start from the saved asset).
            client, store = self._client(snapshot.asset)
            with patch("v2a_inspect.ui.routes.run_track_clip_pipeline") as pipeline:
                response = client.delete(url)
            self.assertEqual(response.json(), {"status": "queued"})
            self.assertFalse(door_track(asyncio.run(store.snapshot())).has_clip)
            pipeline.assert_called_once()

    def test_trim_route_guards(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            asset, (door, _) = _asset(Path(temp_dir))
            first = asset.sound_timeline.sound_events[0].sound_event_id
            url = f"/api/audio/tracks/{door.sound_track_id}/trim-to-spans"
            busy, _ = self._client(asset, running=True)
            self.assertEqual(busy.post(url).status_code, 409)
            self.assertEqual(busy.delete(url).status_code, 409)
            empty, _ = self._client(None)
            self.assertEqual(empty.post(url).status_code, 404)
            client, _ = self._client(asset)
            self.assertEqual(client.post(f"/api/audio/tracks/{uuid4()}/trim-to-spans").status_code, 404)
            self.assertEqual(client.post("/api/audio/tracks/nope/trim-to-spans").status_code, 404)
            silent, _ = self._client(asset.model_copy(update={"sound_track_audio_artifacts": []}))
            self.assertEqual(silent.post(url).status_code, 422)
            # Bad events change nothing: out of range -> 422, deleting the only event -> 400.
            for body, code in (
                ([{"sound_event_id": str(first), "start_frame": 0, "end_frame": 999}], 422),
                ([{"sound_event_id": str(first), "start_frame": 0, "end_frame": 9, "deleted": True}], 400),
            ):
                client, store = self._client(asset)
                with patch("v2a_inspect.ui.routes.run_track_clip_pipeline") as pipeline:
                    response = client.post(url, data={"events": json.dumps(body)})
                self.assertEqual(response.status_code, code)
                pipeline.assert_not_called()
                self.assertFalse(asyncio.run(store.snapshot()).asset.sound_timeline.sound_tracks[0].has_clip)

    def test_regenerate_route_deletes_events_and_rejects_deleting_all(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            asset, (door, _) = _asset(Path(temp_dir))
            asset, extra = _add_event(asset, door, 60, 90)
            first = asset.sound_timeline.sound_events[0].sound_event_id
            url = f"/api/audio/tracks/{door.sound_track_id}/regenerate"
            gone = [{"sound_event_id": str(extra.sound_event_id), "start_frame": 60, "end_frame": 90, "deleted": True}]
            client, store = self._client(asset)
            with patch("v2a_inspect.ui.routes.run_track_regeneration_pipeline") as pipeline:
                response = client.post(url, data={"method": "generate", "events": json.dumps(gone)})
            self.assertEqual(response.status_code, 200)
            queued = pipeline.call_args.args[0]
            self.assertNotIn(extra.sound_event_id, {e.sound_event_id for e in queued.sound_timeline.sound_events})
            both = [*gone, {"sound_event_id": str(first), "start_frame": 0, "end_frame": 9, "deleted": True}]
            client, _ = self._client(asset)
            with patch("v2a_inspect.ui.routes.run_track_regeneration_pipeline") as pipeline:
                response = client.post(url, data={"method": "generate", "events": json.dumps(both)})
            self.assertEqual(response.status_code, 400)
            pipeline.assert_not_called()

    def test_regenerate_route_passes_the_model_and_rejects_tts_for_non_speech(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            asset, (door, _) = _asset(Path(temp_dir))
            url = f"/api/audio/tracks/{door.sound_track_id}/regenerate"
            client, _ = self._client(asset)
            with patch("v2a_inspect.ui.routes.run_track_regeneration_pipeline") as pipeline:
                response = client.post(url, data={"method": "generate", "model": "t2a"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(pipeline.call_args.args[-1], "t2a")
            client, _ = self._client(asset)
            with patch("v2a_inspect.ui.routes.run_track_regeneration_pipeline") as pipeline:
                self.assertEqual(client.post(url, data={"method": "generate", "model": "tts"}).status_code, 422)
                self.assertEqual(client.post(url, data={"method": "generate", "model": "nope"}).status_code, 422)
            pipeline.assert_not_called()

    def test_create_route_adds_the_track_and_stays_idle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            asset, _ = _asset(Path(temp_dir))
            body = {
                "label": "Glass",
                "description": "glass breaking",
                "model": "t2a",
                "spans": [{"start_frame": 60, "end_frame": 90}, {"start_frame": 0, "end_frame": 30}],
            }
            client, store = self._client(asset)
            response = client.post("/api/audio/tracks", json=body)
            snapshot = asyncio.run(store.snapshot())

            self.assertEqual(response.status_code, 200)
            tracks = snapshot.asset.sound_timeline.sound_tracks
            self.assertEqual(response.json()["sound_track_id"], str(tracks[-1].sound_track_id))
            self.assertEqual(snapshot.status, "complete")  # regenerate can start right away
            self.assertEqual(tracks[-1].generation_model, "t2a")
            added = [
                (e.start_frame_index, e.end_frame_index)
                for e in snapshot.asset.sound_timeline.sound_events
                if e.sound_track_id == tracks[-1].sound_track_id
            ]
            self.assertEqual(added, [(0, 30), (60, 90)])

            self.assertEqual(client.post("/api/audio/tracks", json={**body, "label": " "}).status_code, 422)
            self.assertEqual(client.post("/api/audio/tracks", json={**body, "model": "x"}).status_code, 422)
            self.assertEqual(client.post("/api/audio/tracks", json={**body, "spans": []}).status_code, 422)
            self.assertEqual(
                client.post("/api/audio/tracks", json={**body, "model": "separate"}).status_code, 422
            )  # the asset has no original audio
            busy, _ = self._client(asset, running=True)
            self.assertEqual(busy.post("/api/audio/tracks", json=body).status_code, 409)
            empty, _ = self._client(None)
            self.assertEqual(empty.post("/api/audio/tracks", json=body).status_code, 404)


if __name__ == "__main__":
    unittest.main()
