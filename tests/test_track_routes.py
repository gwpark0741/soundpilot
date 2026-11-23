from __future__ import annotations

import asyncio
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient
from scipy.io import wavfile

from v2a_inspect.models import (
    SoundEvent,
    SoundEventAudioArtifact,
    SoundTimeline,
    SoundTrack,
    SoundTrackAudioArtifact,
    TrackStatus,
    VideoAsset,
)
from v2a_inspect.ui.pipeline import run_track_regeneration_pipeline
from v2a_inspect.ui.routes import create_router
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


def _asset(root: Path, **update) -> tuple[VideoAsset, SoundTrack]:
    track = SoundTrack(track_type="sfx", label="Door", generation_model="selsync")
    event = SoundEvent(
        sound_track_id=track.sound_track_id,
        start_frame_index=0,
        end_frame_index=30,
        description="Door event",
    )
    asset = VideoAsset(
        source_path=root / "video.mp4",
        frame_count=30,
        sound_timeline=SoundTimeline(sound_tracks=[track], sound_events=[event]),
        **update,
    )
    return asset, track


class TrackRouteTest(unittest.TestCase):
    def _client(self, asset: VideoAsset | None, *, running: bool = False):
        store = VideoAssetStore()
        if asset is not None:
            asyncio.run(store.set_complete_asset(asset))
        if running:
            asyncio.run(store.set_running(stage="busy"))
        app = FastAPI()
        app.include_router(create_router(store))
        return TestClient(app), store

    def test_regenerate_track_conflict_while_running(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            asset, track = _asset(Path(temp_dir))
            client, store = self._client(asset, running=True)
            before = asyncio.run(store.snapshot())
            with patch("v2a_inspect.ui.routes.run_track_regeneration_pipeline"):
                response = client.post(
                    f"/api/audio/tracks/{track.sound_track_id}/regenerate",
                    data={"method": "selsync"},
                )
            after = asyncio.run(store.snapshot())

            self.assertEqual(response.status_code, 409)
            self.assertEqual(after.asset_version, before.asset_version)
            self.assertEqual(after.version, before.version)

    def test_regenerate_track_queues(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            asset, track = _asset(Path(temp_dir))
            client, store = self._client(asset)
            with patch(
                "v2a_inspect.ui.routes.run_track_regeneration_pipeline"
            ) as pipeline:
                response = client.post(
                    f"/api/audio/tracks/{track.sound_track_id}/regenerate",
                    data={"method": "generate", "description": "creak", "seed": "7"},
                )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"status": "queued"})
            args = pipeline.call_args.args
            self.assertEqual(args[1], store)
            self.assertEqual(args[2:], (str(track.sound_track_id), "generate", "creak", 7))

            client, _ = self._client(asset)  # the first request left the store running
            with patch(
                "v2a_inspect.ui.routes.run_track_regeneration_pipeline"
            ) as pipeline:
                client.post(
                    f"/api/audio/tracks/{track.sound_track_id}/regenerate",
                    data={"method": "selsync"},
                )
            self.assertEqual(pipeline.call_args.args[3], "generate")

    def test_regenerate_track_missing_asset_or_track_is_404(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            asset, track = _asset(Path(temp_dir))
            empty, _ = self._client(None)
            self.assertEqual(
                empty.post(
                    f"/api/audio/tracks/{track.sound_track_id}/regenerate",
                    data={"method": "selsync"},
                ).status_code,
                404,
            )
            client, _ = self._client(asset)
            self.assertEqual(
                client.post(
                    "/api/audio/tracks/00000000-0000-0000-0000-000000000000/regenerate",
                    data={"method": "selsync"},
                ).status_code,
                404,
            )

    def test_separate_on_silent_asset_is_422(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            asset, track = _asset(Path(temp_dir), has_audio=False)
            client, _ = self._client(asset)
            with patch("v2a_inspect.ui.routes.run_track_regeneration_pipeline") as p:
                response = client.post(
                    f"/api/audio/tracks/{track.sound_track_id}/regenerate",
                    data={"method": "separate"},
                )

            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json()["detail"], "No original audio to separate.")
            p.assert_not_called()

    def test_generate_on_speech_track_queues(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            track = SoundTrack(track_type="speech", label="Voice", generation_model="tts")
            asset = VideoAsset(
                source_path=Path(temp_dir) / "video.mp4",
                frame_count=30,
                sound_timeline=SoundTimeline(sound_tracks=[track], sound_events=[]),
            )
            client, _ = self._client(asset)
            with patch("v2a_inspect.ui.routes.run_track_regeneration_pipeline") as p:
                response = client.post(
                    f"/api/audio/tracks/{track.sound_track_id}/regenerate",
                    data={"method": "generate"},
                )

            self.assertEqual(response.status_code, 200)
            p.assert_called_once()

    def test_set_mode_conflict_and_missing_asset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            asset, _ = _asset(Path(temp_dir))
            busy, _ = self._client(asset, running=True)
            self.assertEqual(
                busy.post("/api/audio/mode", data={"mode": "generate"}).status_code, 409
            )
            empty, _ = self._client(None)
            self.assertEqual(
                empty.post("/api/audio/mode", data={"mode": "generate"}).status_code, 404
            )

    def test_set_mode_separate_without_audio_is_422_and_changes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            asset, _ = _asset(Path(temp_dir), has_audio=False)
            client, store = self._client(asset)
            before = asyncio.run(store.snapshot())
            response = client.post("/api/audio/mode", data={"mode": "separate"})
            after = asyncio.run(store.snapshot())

            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json()["detail"], "No original audio to separate.")
            self.assertEqual(after.asset_version, before.asset_version)
            self.assertEqual(after.asset.audio_mode, "generate")
            self.assertEqual(
                [t.generation_model for t in after.asset.sound_timeline.sound_tracks],
                ["selsync"],
            )

    def test_set_mode_separate_plans_in_background_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            asset, _ = _asset(Path(temp_dir), has_audio=True)
            client, store = self._client(asset)
            with patch("v2a_inspect.ui.routes.run_separation_planning_pipeline") as plan:
                response = client.post("/api/audio/mode", data={"mode": "separate"})
                again = client.post("/api/audio/mode", data={"mode": "separate"})

            self.assertEqual(response.json(), {"status": "queued"})
            self.assertEqual(again.status_code, 409)  # the store is running
            plan.assert_called_once()
            snapshot = asyncio.run(store.snapshot())
            self.assertEqual(snapshot.status, "running")
            self.assertEqual(snapshot.current_stage, "planning separation")
            self.assertEqual(snapshot.asset.audio_mode, "generate")  # changes when planning ends

    def test_set_mode_swaps_stored_timelines(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            asset, _ = _asset(
                Path(temp_dir),
                has_audio=True,
                synthesized_video_path=Path(temp_dir) / "preview.mp4",
            )
            separation = SoundTimeline(
                sound_tracks=[
                    SoundTrack(track_type="sfx", label="Wind", generation_model="separate")
                ]
            )
            asset = asset.model_copy(update={"inactive_timeline": separation})
            generate = asset.sound_timeline
            client, store = self._client(asset)
            version = asyncio.run(store.snapshot()).asset_version
            with patch("v2a_inspect.ui.routes.run_separation_planning_pipeline") as plan:
                response = client.post("/api/audio/mode", data={"mode": "separate"})
                snapshot = asyncio.run(store.snapshot())
                self.assertEqual(response.json(), {"status": "ok"})
                self.assertEqual(snapshot.status, "complete")
                self.assertEqual(snapshot.asset_version, version + 1)  # media reloads
                self.assertEqual(snapshot.asset.audio_mode, "separate")
                self.assertEqual(snapshot.asset.sound_timeline, separation)
                self.assertEqual(snapshot.asset.inactive_timeline, generate)
                self.assertIsNone(snapshot.asset.synthesized_video_path)

                client.post("/api/audio/mode", data={"mode": "generate"})
                snapshot = asyncio.run(store.snapshot())
                self.assertEqual(snapshot.asset.audio_mode, "generate")
                self.assertEqual(snapshot.asset.sound_timeline, generate)
                self.assertEqual(snapshot.asset.inactive_timeline, separation)
            plan.assert_not_called()

    def test_mode_switch_then_partial_action_keeps_event_level_track_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset, wind = _asset(
                root, original_audio_path=_write_sine(root / "orig.wav"), has_audio=True
            )
            door = SoundTrack(track_type="sfx", label="Door", generation_model="t2a")
            event = SoundEvent(
                sound_track_id=door.sound_track_id,
                start_frame_index=0,
                end_frame_index=30,
                description="Door event",
            )
            timeline = asset.sound_timeline
            asset = asset.model_copy(
                update={
                    "sound_timeline": SoundTimeline(
                        sound_tracks=[*timeline.sound_tracks, door],
                        sound_events=[*timeline.sound_events, event],
                    ),
                    "sound_event_audio_artifacts": [
                        SoundEventAudioArtifact(
                            sound_event_id=event.sound_event_id,
                            sound_track_id=door.sound_track_id,
                            path=_write_sine(root / "event.wav"),
                            duration_sec=1.0,
                            generation_model="t2a",
                            description="Door event",
                        )
                    ],
                }
            )
            switched = asset.model_copy(update={"audio_mode": "separate"})

            store = VideoAssetStore()
            asyncio.run(store.set_complete_asset(switched))
            with (
                patch("v2a_inspect.ui.pipeline.separate_track", side_effect=_fake_stem),
                patch(
                    "v2a_inspect.ui.pipeline.mix_audio_into_video",
                    side_effect=_fake_mix,
                ),
            ):
                asyncio.run(
                    run_track_regeneration_pipeline(
                        switched, store, str(wind.sound_track_id), "separate", None, None
                    )
                )
            snapshot = asyncio.run(store.snapshot())

            self.assertEqual(snapshot.status, "complete")
            ids = {a.sound_track_id for a in snapshot.asset.sound_track_audio_artifacts}
            self.assertEqual(ids, {door.sound_track_id, wind.sound_track_id})
            self.assertEqual(len(snapshot.asset.sound_event_audio_artifacts), 1)

    def test_residual_toggle_queues_and_guards(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset, _ = _asset(
                root, has_audio=True, original_audio_path=_write_sine(root / "o.wav")
            )
            client, _ = self._client(asset)
            with patch("v2a_inspect.ui.routes.run_residual_toggle_pipeline") as p:
                response = client.post("/api/audio/residual", data={"enabled": "true"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(p.call_args.args[2], True)

            busy, _ = self._client(asset, running=True)
            self.assertEqual(
                busy.post("/api/audio/residual", data={"enabled": "true"}).status_code,
                409,
            )
            empty, _ = self._client(None)
            self.assertEqual(
                empty.post("/api/audio/residual", data={"enabled": "true"}).status_code,
                404,
            )

    def test_event_regenerate_and_delete_rejected_on_track_level_track(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            asset, track = _asset(Path(temp_dir))  # selsync track
            event = asset.sound_timeline.sound_events[0]
            client, _ = self._client(asset)
            with (
                patch("v2a_inspect.ui.routes.run_event_audio_regeneration_pipeline") as regen,
                patch("v2a_inspect.ui.routes.run_event_audio_delete_pipeline") as delete,
            ):
                r1 = client.post(
                    f"/api/audio/events/{event.sound_event_id}/regenerate",
                    data={"description": "x"},
                )
                r2 = client.delete(f"/api/audio/events/{event.sound_event_id}")

            self.assertEqual((r1.status_code, r2.status_code), (409, 409))
            self.assertIn("트랙", r1.json()["detail"])
            regen.assert_not_called()
            delete.assert_not_called()

    def test_reset_run_clears_separation_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset, track = _asset(
                root,
                separated_stem_paths={track_id: root / "s.wav" for track_id in [uuid4()]},
                residual_path=root / "r.wav",
                residual_enabled=True,
                track_status={uuid4(): TrackStatus(state="done")},
            )
            client, store = self._client(asset)
            with patch("v2a_inspect.ui.routes.run_sound_timeline_pipeline"):
                response = client.post("/api/sound-timeline/reset-run")

            self.assertEqual(response.status_code, 200)
            reset = asyncio.run(store.snapshot()).asset
            self.assertEqual(reset.separated_stem_paths, {})
            self.assertIsNone(reset.residual_path)
            self.assertFalse(reset.residual_enabled)
            self.assertEqual(reset.track_status, {})

    def test_import_keeps_json_audio_fields_when_files_exist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            raw = root / "raw.mp4"
            raw.write_bytes(b"raw")
            original = _write_sine(root / "orig.wav")
            asset, _ = _asset(
                root,
                raw_video_path=raw,
                original_audio_path=original,
                has_audio=True,
            )
            letterboxed = VideoAsset(
                source_path=root / "prepared.mp4",
                frame_count=30,
                raw_video_path=root / "prepared.mp4",
                original_audio_path=None,
                has_audio=False,
            )
            client, store = self._client(None)
            with patch(
                "v2a_inspect.ui.routes._resolve_import_video",
                return_value=letterboxed,
            ):
                response = client.post(
                    "/api/asset/import",
                    files={
                        "video": ("prepared.mp4", b"v", "video/mp4"),
                        "asset": (
                            "asset.json",
                            asset.model_dump_json(exclude_computed_fields=True),
                            "application/json",
                        ),
                    },
                    data={"work_dir": temp_dir},
                )

            self.assertEqual(response.status_code, 200)
            imported = asyncio.run(store.snapshot()).asset
            self.assertEqual(imported.raw_video_path, raw)
            self.assertEqual(imported.original_audio_path, original)
            self.assertTrue(imported.has_audio)

    def test_residual_enable_without_original_audio_is_422(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            asset, _ = _asset(Path(temp_dir), has_audio=False)
            client, _ = self._client(asset)
            with patch("v2a_inspect.ui.routes.run_residual_toggle_pipeline") as p:
                on = client.post("/api/audio/residual", data={"enabled": "true"})
                off = client.post("/api/audio/residual", data={"enabled": "false"})

            self.assertEqual(on.status_code, 422)
            self.assertIn("원본 오디오", on.json()["detail"])
            self.assertEqual(off.status_code, 200)
            self.assertEqual(p.call_count, 1)


class TrackRegenerationPipelineTest(unittest.TestCase):
    def _run(self, asset, track, *, method, description=None, seed=None, **patches):
        store = VideoAssetStore()
        asyncio.run(store.set_complete_asset(asset))
        with (
            patch("v2a_inspect.ui.pipeline.generate_track", side_effect=_fake_stem),
            patch("v2a_inspect.ui.pipeline.separate_track", side_effect=_fake_stem),
            patch(
                "v2a_inspect.ui.pipeline.mix_audio_into_video", side_effect=_fake_mix
            ),
        ):
            asyncio.run(
                run_track_regeneration_pipeline(
                    asset,
                    store,
                    str(track.sound_track_id),
                    method,
                    description,
                    seed,
                )
            )
        return asyncio.run(store.snapshot())

    def test_track_regeneration_new_seed_and_description(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            asset, track = _asset(Path(temp_dir))
            with patch("v2a_inspect.ui.pipeline.secrets.randbelow", return_value=999):
                snapshot = self._run(
                    asset, track, method="generate", description="  creaky door  "
                )

            self.assertEqual(snapshot.status, "complete")
            (stem,) = snapshot.asset.sound_track_audio_artifacts
            self.assertEqual(stem.seed, 999)
            self.assertNotEqual(stem.seed, 42)
            self.assertEqual(stem.description, "creaky door")
            (saved,) = snapshot.asset.sound_timeline.sound_tracks
            self.assertEqual(saved.description, "creaky door")
            self.assertEqual(saved.generation_model, "selsync")
            self.assertEqual(
                snapshot.asset.track_status[track.sound_track_id].state, "done"
            )

            explicit = self._run(asset, track, method="generate", seed=1234)
            (stem,) = explicit.asset.sound_track_audio_artifacts
            self.assertEqual(stem.seed, 1234)

    def test_reseparate_increments_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset, track = _asset(
                root, original_audio_path=_write_sine(root / "orig.wav"), has_audio=True
            )
            old = SoundTrackAudioArtifact(
                sound_track_id=track.sound_track_id,
                track_label="Door",
                track_type="sfx",
                path=_write_sine(root / "old.wav"),
                duration_sec=1.0,
                event_count=1,
                origin="separated",
                attempt=1,
            )
            asset = asset.model_copy(
                update={
                    "sound_track_audio_artifacts": [old],
                    "separated_stem_paths": {track.sound_track_id: old.path},
                }
            )
            snapshot = self._run(asset, track, method="separate")

            self.assertEqual(snapshot.status, "complete")
            (stem,) = snapshot.asset.sound_track_audio_artifacts
            self.assertEqual((stem.origin, stem.attempt), ("separated", 2))
            self.assertEqual(
                snapshot.asset.separated_stem_paths[track.sound_track_id], stem.path
            )
            self.assertIsNotNone(snapshot.asset.residual_path)

    def test_separate_to_selsync_keeps_separated_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset, track = _asset(
                root, original_audio_path=_write_sine(root / "orig.wav"), has_audio=True
            )
            old = _write_sine(root / "old.wav")
            asset = asset.model_copy(
                update={"separated_stem_paths": {track.sound_track_id: old}}
            )
            snapshot = self._run(asset, track, method="generate", seed=5)

            self.assertEqual(snapshot.status, "complete")
            self.assertEqual(
                snapshot.asset.separated_stem_paths, {track.sound_track_id: old}
            )
            self.assertIsNotNone(snapshot.asset.residual_path)

    def test_event_level_track_switch_does_not_double_mix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset, track = _asset(root)
            track = track.model_copy(update={"generation_model": "t2a", "agent_model": "v2a"})
            event = asset.sound_timeline.sound_events[0]
            asset = asset.model_copy(
                update={
                    "sound_timeline": asset.sound_timeline.model_copy(
                        update={"sound_tracks": [track]}
                    ),
                    "sound_event_audio_artifacts": [
                        SoundEventAudioArtifact(
                            sound_event_id=event.sound_event_id,
                            sound_track_id=track.sound_track_id,
                            path=_write_sine(root / "event.wav"),
                            duration_sec=1.0,
                            generation_model="t2a",
                            description="Door event",
                        )
                    ],
                }
            )
            store = VideoAssetStore()
            asyncio.run(store.set_complete_asset(asset))
            with (
                patch("v2a_inspect.ui.pipeline.generate_track", side_effect=_fake_stem),
                patch(
                    "v2a_inspect.ui.pipeline.mix_audio_into_video",
                    side_effect=_fake_mix,
                ) as mix,
            ):
                asyncio.run(
                    run_track_regeneration_pipeline(
                        asset, store, str(track.sound_track_id), "generate", None, None
                    )
                )
            snapshot = asyncio.run(store.snapshot())

            self.assertEqual(snapshot.status, "complete")
            (stem,) = snapshot.asset.sound_track_audio_artifacts
            self.assertEqual(stem.origin, "generated")
            self.assertEqual(snapshot.asset.sound_event_audio_artifacts, [])
            items = mix.call_args.kwargs["audio_plan"].items
            self.assertEqual([i.item_id for i in items], [str(track.sound_track_id)])

    def test_failure_keeps_previous_stem_and_reports_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset, track = _asset(root)
            old = SoundTrackAudioArtifact(
                sound_track_id=track.sound_track_id,
                track_label="Door",
                track_type="sfx",
                path=_write_sine(root / "old.wav"),
                duration_sec=1.0,
                event_count=1,
                origin="generated",
                seed=42,
            )
            asset = asset.model_copy(update={"sound_track_audio_artifacts": [old]})
            store = VideoAssetStore()
            asyncio.run(store.set_complete_asset(asset))
            with patch(
                "v2a_inspect.ui.pipeline.generate_track",
                side_effect=RuntimeError("boom"),
            ):
                asyncio.run(
                    run_track_regeneration_pipeline(
                        asset, store, str(track.sound_track_id), "generate", None, None
                    )
                )
            snapshot = asyncio.run(store.snapshot())

            self.assertEqual(snapshot.status, "failed")
            # Top bar gets short copy; the raw detail stays in the track status only.
            self.assertEqual(snapshot.error, "Door failed. Previous audio kept.")
            self.assertNotIn("boom", snapshot.error)
            self.assertEqual(snapshot.asset.sound_track_audio_artifacts, [old])
            status = snapshot.asset.track_status[track.sound_track_id]
            self.assertEqual(status.state, "failed")
            self.assertIn("boom", status.error)

    def test_failed_switch_restores_track_generation_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset, track = _asset(root)
            track = track.model_copy(update={"generation_model": "separate"})
            asset = asset.model_copy(
                update={
                    "sound_timeline": asset.sound_timeline.model_copy(
                        update={"sound_tracks": [track]}
                    )
                }
            )
            store = VideoAssetStore()
            asyncio.run(store.set_complete_asset(asset))
            with patch(
                "v2a_inspect.ui.pipeline.generate_track",
                side_effect=RuntimeError("boom"),
            ):
                asyncio.run(
                    run_track_regeneration_pipeline(
                        asset, store, str(track.sound_track_id), "generate", "new", None
                    )
                )
            snapshot = asyncio.run(store.snapshot())

            self.assertEqual(snapshot.status, "failed")
            (shown,) = snapshot.asset.sound_timeline.sound_tracks
            self.assertEqual(shown.generation_model, "separate")

    def _event_asset(self, root, *, agent, track_type="sfx", n_events=2, model="separate"):
        track = SoundTrack(
            track_type=track_type,
            label="Door" if track_type == "sfx" else "Voice",
            generation_model=model,
            agent_model=agent,
        )
        events = [
            SoundEvent(
                sound_track_id=track.sound_track_id,
                start_frame_index=i * 30,
                end_frame_index=i * 30 + 20,
                description=f"event {i}",
                spoken_text="hello" if track_type == "speech" else None,
            )
            for i in range(n_events)
        ]
        stem = SoundTrackAudioArtifact(
            sound_track_id=track.sound_track_id,
            track_label=track.label,
            track_type=track_type,
            path=_write_sine(root / "old_stem.wav"),
            duration_sec=1.0,
            event_count=n_events,
            origin="separated",
            attempt=1,
        )
        asset = VideoAsset(
            source_path=root / "video.mp4",
            frame_count=30 * n_events,
            original_audio_path=_write_sine(root / "orig.wav"),
            has_audio=True,
            sound_timeline=SoundTimeline(sound_tracks=[track], sound_events=events),
            sound_track_audio_artifacts=[stem],
            separated_stem_paths={track.sound_track_id: stem.path},
        )
        return asset, track, stem

    def _run_generate(self, asset, track, fake_generate):
        store = VideoAssetStore()
        asyncio.run(store.set_complete_asset(asset))
        with (
            patch("v2a_inspect.ui.pipeline.generate_track", side_effect=_fake_stem) as gen,
            patch(
                "v2a_inspect.ui.pipeline.generate_audio_for_item",
                side_effect=fake_generate,
            ) as items,
            patch(
                "v2a_inspect.ui.pipeline._convert_audio_to_wav",
                side_effect=lambda src, dst: Path(shutil.copy(src, dst)),
            ),
            patch(
                "v2a_inspect.ui.pipeline.mix_audio_into_video", side_effect=_fake_mix
            ) as mix,
        ):
            asyncio.run(
                run_track_regeneration_pipeline(
                    asset, store, str(track.sound_track_id), "generate", None, None
                )
            )
        return asyncio.run(store.snapshot()), gen, items, mix

    @staticmethod
    def _ok(**kw):
        return str(_write_sine(Path(kw["out_path"])))

    def test_generate_follows_agent_v2a_to_selsync_stem(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            asset, track, _ = self._event_asset(Path(temp_dir), agent="v2a")
            snapshot, gen, items, _ = self._run_generate(asset, track, self._ok)

            self.assertEqual(snapshot.status, "complete")
            gen.assert_called_once()
            items.assert_not_called()
            (stem,) = snapshot.asset.sound_track_audio_artifacts
            self.assertEqual(stem.origin, "generated")
            (saved,) = snapshot.asset.sound_timeline.sound_tracks
            self.assertEqual((saved.generation_model, saved.agent_model), ("selsync", "v2a"))

    def test_generate_t2a_track_regenerates_events_and_remixes_stem(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            asset, track, old = self._event_asset(Path(temp_dir), agent="t2a")
            snapshot, gen, items, mix = self._run_generate(asset, track, self._ok)

            self.assertEqual(snapshot.status, "complete")
            gen.assert_not_called()
            self.assertEqual(items.call_count, 2)
            self.assertEqual(
                {c.kwargs["generation_model"] for c in items.call_args_list}, {"t2a"}
            )
            self.assertEqual(len(snapshot.asset.sound_event_audio_artifacts), 2)
            # The old separated stem is gone; the stem is now the mix of the new events.
            (stem,) = snapshot.asset.sound_track_audio_artifacts
            self.assertEqual(stem.origin, "mixed")
            self.assertNotEqual(stem.path, old.path)
            self.assertEqual(stem.event_count, 2)
            items_in_preview = mix.call_args.kwargs["audio_plan"].items
            self.assertEqual([i.item_id for i in items_in_preview], [str(track.sound_track_id)])
            (saved,) = snapshot.asset.sound_timeline.sound_tracks
            self.assertEqual((saved.generation_model, saved.agent_model), ("t2a", "t2a"))
            self.assertEqual(
                snapshot.asset.track_status[track.sound_track_id].state, "done"
            )
            # Separated audio still counts toward the rest-of-original residual.
            self.assertEqual(
                snapshot.asset.separated_stem_paths, {track.sound_track_id: old.path}
            )

    def test_generate_speech_track_uses_tts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            asset, track, _ = self._event_asset(
                Path(temp_dir), agent="v2a", track_type="speech", n_events=1
            )
            snapshot, _, items, _ = self._run_generate(asset, track, self._ok)

            self.assertEqual(snapshot.status, "complete")
            (call,) = items.call_args_list
            self.assertEqual(call.kwargs["generation_model"], "tts")
            self.assertEqual(call.kwargs["spoken_text"], "hello")
            (saved,) = snapshot.asset.sound_timeline.sound_tracks
            self.assertEqual((saved.generation_model, saved.agent_model), ("tts", "v2a"))

    def test_generate_event_failure_keeps_previous_events_and_stem(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset, track, old = self._event_asset(root, agent="t2a", model="t2a")
            event_dir = root / "audio" / "events"
            event_dir.mkdir(parents=True)
            previous = []
            for event in asset.sound_timeline.sound_events:
                wav = _write_sine(event_dir / f"prev_{event.sound_event_id}.wav")
                previous.append(
                    SoundEventAudioArtifact(
                        sound_event_id=event.sound_event_id,
                        sound_track_id=track.sound_track_id,
                        path=wav,
                        duration_sec=1.0,
                        generation_model="t2a",
                        description="old",
                    )
                )
            mixed = SoundTrackAudioArtifact(
                sound_track_id=track.sound_track_id,
                track_label="Door",
                track_type="sfx",
                path=old.path,
                duration_sec=1.0,
                event_count=2,
            )
            asset = asset.model_copy(
                update={
                    "sound_event_audio_artifacts": previous,
                    "sound_track_audio_artifacts": [mixed],
                    "separated_stem_paths": {},
                }
            )
            before = [a.path.read_bytes() for a in previous]
            calls = []

            def flaky(**kw):
                calls.append(kw)
                if len(calls) == 2:
                    raise RuntimeError("elevenlabs down")
                return str(_write_sine(Path(kw["out_path"])))

            with patch("v2a_inspect.ui.pipeline._audio_work_root", return_value=root):
                snapshot, _, _, _ = self._run_generate(asset, track, flaky)

            self.assertEqual(snapshot.status, "failed")
            self.assertEqual(snapshot.error, "Door failed. Previous audio kept.")
            status = snapshot.asset.track_status[track.sound_track_id]
            self.assertEqual(status.state, "failed")
            self.assertIn("elevenlabs down", status.error)
            self.assertEqual(snapshot.asset.sound_event_audio_artifacts, previous)
            self.assertEqual([a.path.read_bytes() for a in previous], before)
            self.assertEqual(snapshot.asset.sound_track_audio_artifacts, [mixed])
            (saved,) = snapshot.asset.sound_timeline.sound_tracks
            self.assertEqual(saved.generation_model, "t2a")


if __name__ == "__main__":
    unittest.main()
