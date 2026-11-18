from __future__ import annotations

import asyncio
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from scipy.io import wavfile

from v2a_inspect.models import (
    AudioPlan,
    SoundEvent,
    SoundEventAudioArtifact,
    SoundTimeline,
    SoundTrack,
    SoundTrackAudioArtifact,
    VideoAsset,
)
from v2a_inspect.audio_generation.plan import build_audio_plan
from v2a_inspect.ui.pipeline import (
    _rebuild_audio_outputs,
    run_audio_generation_pipeline,
)
from v2a_inspect.ui.store import VideoAssetStore


def _write_sine(path: Path) -> Path:
    t = np.arange(48000) / 48000
    data = (np.sin(2 * np.pi * 440 * t) * 0.5 * 32767).astype(np.int16)
    wavfile.write(path, 48000, data)
    return path


def _fake_stem(*args) -> Path:
    return _write_sine(args[-1])


def _fake_mix(*, output_path: str, **_kwargs) -> str:
    Path(output_path).write_bytes(b"mp4")
    return output_path


def _asset(
    root: Path, model: str, labels: list[str], **update
) -> tuple[VideoAsset, list[SoundTrack]]:
    tracks = [
        SoundTrack(track_type="sfx", label=label, generation_model=model)
        for label in labels
    ]
    events = [
        SoundEvent(
            sound_track_id=track.sound_track_id,
            start_frame_index=0,
            end_frame_index=30,
            description=f"{track.label} event",
        )
        for track in tracks
    ]
    asset = VideoAsset(
        source_path=root / "video.mp4",
        frame_count=30,
        sound_timeline=SoundTimeline(sound_tracks=tracks, sound_events=events),
        **update,
    )
    return asset, tracks


async def _run(asset: VideoAsset):
    store = VideoAssetStore()
    await run_audio_generation_pipeline(asset, store, None)
    return await store.snapshot()


class TrackPipelineTest(unittest.TestCase):
    def test_silent_asset_generates_selsync_stems(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset, _tracks = _asset(
                root, "selsync", ["Door", "Wind"], raw_video_path=root / "raw.mp4"
            )
            with (
                patch(
                    "v2a_inspect.ui.pipeline.generate_track", side_effect=_fake_stem
                ) as gen,
                patch(
                    "v2a_inspect.ui.pipeline.mix_audio_into_video",
                    side_effect=_fake_mix,
                ),
            ):
                snapshot = asyncio.run(_run(asset))

            self.assertEqual(snapshot.status, "complete")
            stems = snapshot.asset.sound_track_audio_artifacts
            self.assertEqual(len(stems), 2)
            self.assertTrue(
                all(s.origin == "generated" and s.seed == 42 for s in stems)
            )
            self.assertIsNotNone(snapshot.asset.synthesized_video_path)
            self.assertEqual(gen.call_args.args[0], root / "raw.mp4")
            self.assertTrue(
                all(s.state == "done" for s in snapshot.asset.track_status.values())
            )

    def test_audio_asset_separates_and_records_stem(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset, tracks = _asset(
                root,
                "separate",
                ["Door"],
                original_audio_path=_write_sine(root / "orig.wav"),
                has_audio=True,
            )
            with (
                patch("v2a_inspect.ui.pipeline.separate_track", side_effect=_fake_stem),
                patch(
                    "v2a_inspect.ui.pipeline.mix_audio_into_video",
                    side_effect=_fake_mix,
                ),
            ):
                snapshot = asyncio.run(_run(asset))

            (stem,) = snapshot.asset.sound_track_audio_artifacts
            self.assertEqual((stem.origin, stem.attempt), ("separated", 1))
            self.assertEqual(
                snapshot.asset.separated_stem_paths[tracks[0].sound_track_id], stem.path
            )

    def test_one_track_failure_keeps_others(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset, tracks = _asset(root, "selsync", ["Door", "Wind"])
            calls = []

            def flaky(video, plan, seed, out_path):
                calls.append(plan["name"])
                if len(calls) == 1:
                    raise RuntimeError("Audyssey /generate failed (500): boom")
                return _write_sine(out_path)

            with (
                patch("v2a_inspect.ui.pipeline.generate_track", side_effect=flaky),
                patch(
                    "v2a_inspect.ui.pipeline.mix_audio_into_video",
                    side_effect=_fake_mix,
                ),
            ):
                snapshot = asyncio.run(_run(asset))

            self.assertEqual(snapshot.status, "complete")
            first = snapshot.asset.track_status[tracks[0].sound_track_id]
            self.assertEqual(first.state, "failed")
            self.assertIn("boom", first.error)
            self.assertEqual(
                snapshot.asset.track_status[tracks[1].sound_track_id].state, "done"
            )

    def test_separate_without_original_audio_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset, tracks = _asset(root, "separate", ["Door"], original_audio_path=None)
            snapshot = asyncio.run(_run(asset))

            self.assertEqual(snapshot.status, "failed")
            status = snapshot.asset.track_status[tracks[0].sound_track_id]
            self.assertEqual(status.state, "failed")
            self.assertIn("생성으로 바꾸세요", status.error)
            self.assertIn("생성으로 바꾸세요", snapshot.error)

    def test_stem_only_asset_builds_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset, tracks = _asset(root, "selsync", ["Door"])
            stem = SoundTrackAudioArtifact(
                sound_track_id=tracks[0].sound_track_id,
                track_label="Door",
                track_type="sfx",
                path=_write_sine(root / "stem.wav"),
                duration_sec=1.0,
                event_count=1,
                origin="generated",
                seed=42,
            )
            with patch(
                "v2a_inspect.ui.pipeline.mix_audio_into_video", side_effect=_fake_mix
            ):
                rebuilt = asyncio.run(
                    _rebuild_audio_outputs(
                        asset,
                        AudioPlan(total_duration=1.0),
                        [],
                        VideoAssetStore(),
                        [stem],
                    )
                )

            self.assertEqual(rebuilt.sound_track_audio_artifacts, [stem])
            self.assertIsNotNone(rebuilt.synthesized_video_path)

    def test_enabled_residual_joins_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset, tracks = _asset(
                root,
                "selsync",
                ["Door"],
                residual_path=_write_sine(root / "residual.wav"),
                residual_enabled=True,
            )
            stem = SoundTrackAudioArtifact(
                sound_track_id=tracks[0].sound_track_id,
                track_label="Door",
                track_type="sfx",
                path=_write_sine(root / "stem.wav"),
                duration_sec=1.0,
                event_count=1,
                origin="generated",
            )
            with patch(
                "v2a_inspect.ui.pipeline.mix_audio_into_video", side_effect=_fake_mix
            ) as mix:
                asyncio.run(
                    _rebuild_audio_outputs(
                        asset,
                        AudioPlan(total_duration=1.0),
                        [],
                        VideoAssetStore(),
                        [stem],
                    )
                )

            self.assertIn("residual", mix.call_args.kwargs["generated_audio"])

    def test_eventless_selsync_track_generates_stem(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            track = SoundTrack(
                track_type="sfx", label="Wind", generation_model="selsync"
            )
            asset = VideoAsset(
                source_path=root / "video.mp4",
                frame_count=30,
                sound_timeline=SoundTimeline(sound_tracks=[track], sound_events=[]),
            )
            with (
                patch(
                    "v2a_inspect.ui.pipeline.generate_track", side_effect=_fake_stem
                ) as gen,
                patch(
                    "v2a_inspect.ui.pipeline.mix_audio_into_video",
                    side_effect=_fake_mix,
                ),
            ):
                snapshot = asyncio.run(_run(asset))

            self.assertEqual(snapshot.status, "complete")
            self.assertEqual(gen.call_args.args[1]["spans"], [])
            self.assertEqual(len(snapshot.asset.sound_track_audio_artifacts), 1)
            self.assertEqual(
                snapshot.asset.track_status[track.sound_track_id].state, "done"
            )

    def test_failed_track_keeps_previous_stem_and_separated_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset, tracks = _asset(root, "selsync", ["Door"])
            tid = tracks[0].sound_track_id
            old = SoundTrackAudioArtifact(
                sound_track_id=tid,
                track_label="Door",
                track_type="sfx",
                path=_write_sine(root / "old_stem.wav"),
                duration_sec=1.0,
                event_count=1,
                origin="separated",
                attempt=1,
            )
            asset = asset.model_copy(
                update={
                    "sound_track_audio_artifacts": [old],
                    "separated_stem_paths": {tid: old.path},
                }
            )
            with (
                patch(
                    "v2a_inspect.ui.pipeline.generate_track",
                    side_effect=RuntimeError("Audyssey /generate failed (500): boom"),
                ),
                patch(
                    "v2a_inspect.ui.pipeline.mix_audio_into_video",
                    side_effect=_fake_mix,
                ) as mix,
            ):
                snapshot = asyncio.run(_run(asset))

            self.assertEqual(snapshot.status, "complete")
            self.assertEqual(snapshot.asset.track_status[tid].state, "failed")
            self.assertEqual(snapshot.asset.sound_track_audio_artifacts, [old])
            self.assertEqual(snapshot.asset.separated_stem_paths, {tid: old.path})
            self.assertEqual(
                mix.call_args.kwargs["generated_audio"], {str(tid): str(old.path)}
            )

    def test_event_rebuild_drops_event_items_and_stale_stems_of_track_level_tracks(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            track = SoundTrack(
                track_type="speech", label="Voice", generation_model="separate"
            )
            gone = SoundTrack(track_type="sfx", label="Gone", generation_model="t2a")
            event = SoundEvent(
                sound_track_id=track.sound_track_id,
                start_frame_index=0,
                end_frame_index=30,
                description="hi",
                spoken_text="hi",
            )

            def stem(t: SoundTrack, name: str) -> SoundTrackAudioArtifact:
                return SoundTrackAudioArtifact(
                    sound_track_id=t.sound_track_id,
                    track_label=t.label,
                    track_type=t.track_type,
                    path=_write_sine(root / name),
                    duration_sec=1.0,
                    event_count=1,
                    origin="separated",
                    attempt=1,
                )

            asset = VideoAsset(
                source_path=root / "video.mp4",
                frame_count=30,
                sound_timeline=SoundTimeline(
                    sound_tracks=[track], sound_events=[event]
                ),
                # `gone` is no longer in the timeline, so its stem must be dropped.
                sound_track_audio_artifacts=[
                    stem(track, "a.wav"),
                    stem(gone, "b.wav"),
                ],
            )
            plan = build_audio_plan(
                asset.sound_timeline, fps=asset.fps, total_duration=asset.duration_sec
            )
            event_wav = _write_sine(root / "event.wav")
            event_artifact = SoundEventAudioArtifact(
                sound_event_id=event.sound_event_id,
                sound_track_id=track.sound_track_id,
                path=event_wav,
                duration_sec=1.0,
                generation_model="tts",
                description="hi",
            )
            with (
                patch(
                    "v2a_inspect.ui.pipeline.mix_audio_into_video",
                    side_effect=_fake_mix,
                ),
                patch(
                    "v2a_inspect.ui.pipeline.mix_audio_items_to_wav",
                    side_effect=lambda items, ga, out, **k: str(_write_sine(Path(out))),
                ) as mix_items,
            ):
                rebuilt = asyncio.run(
                    _rebuild_audio_outputs(
                        asset, plan, [event_artifact], VideoAssetStore()
                    )
                )

            mix_items.assert_not_called()
            (only,) = rebuilt.sound_track_audio_artifacts
            self.assertEqual(only.sound_track_id, track.sound_track_id)
            self.assertEqual(only.origin, "separated")
            self.assertEqual(rebuilt.sound_event_audio_artifacts, [])

    def test_event_level_track_failure_is_reported_in_track_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            speech = SoundTrack(track_type="speech", label="Voice", generation_model="tts")
            ok = SoundTrack(track_type="sfx", label="Door", generation_model="t2a")
            selsync = SoundTrack(track_type="sfx", label="Wind", generation_model="selsync")
            events = [
                SoundEvent(
                    sound_track_id=t.sound_track_id,
                    start_frame_index=0,
                    end_frame_index=30,
                    description=t.label,
                    spoken_text="hi" if t is speech else None,
                )
                for t in (speech, ok)
            ]
            asset = VideoAsset(
                source_path=root / "video.mp4",
                frame_count=30,
                sound_timeline=SoundTimeline(
                    sound_tracks=[speech, ok, selsync], sound_events=events
                ),
            )

            def fake_generate(**kw):
                if kw["generation_model"] == "tts":
                    raise RuntimeError("kokoro down")
                return str(_write_sine(Path(kw["out_path"])))

            with (
                patch(
                    "v2a_inspect.ui.pipeline.generate_audio_for_item",
                    side_effect=fake_generate,
                ),
                patch(
                    "v2a_inspect.ui.pipeline._convert_audio_to_wav",
                    side_effect=lambda src, dst: Path(shutil.copy(src, dst)),
                ),
                patch("v2a_inspect.ui.pipeline.generate_track", side_effect=_fake_stem),
                patch(
                    "v2a_inspect.ui.pipeline.mix_audio_into_video",
                    side_effect=_fake_mix,
                ),
            ):
                snapshot = asyncio.run(_run(asset))

            self.assertEqual(snapshot.status, "complete")
            status = snapshot.asset.track_status
            self.assertEqual(status[speech.sound_track_id].state, "failed")
            self.assertIn("kokoro down", status[speech.sound_track_id].error)
            self.assertEqual(status[ok.sound_track_id].state, "done")
            self.assertEqual(status[selsync.sound_track_id].state, "done")


if __name__ == "__main__":
    unittest.main()
