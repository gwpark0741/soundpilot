from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

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
                    data={"method": "selsync", "description": "creak", "seed": "7"},
                )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"status": "queued"})
            args = pipeline.call_args.args
            self.assertEqual(args[1], store)
            self.assertEqual(args[2:], (str(track.sound_track_id), "selsync", "creak", 7))

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
            p.assert_not_called()

    def test_selsync_on_speech_track_is_422(self) -> None:
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
                    data={"method": "selsync"},
                )

            self.assertEqual(response.status_code, 422)
            p.assert_not_called()

    def test_residual_toggle_queues_and_guards(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            asset, _ = _asset(Path(temp_dir))
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
                    asset, track, method="selsync", description="  creaky door  "
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

            explicit = self._run(asset, track, method="selsync", seed=1234)
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
            snapshot = self._run(asset, track, method="selsync", seed=5)

            self.assertEqual(snapshot.status, "complete")
            self.assertEqual(
                snapshot.asset.separated_stem_paths, {track.sound_track_id: old}
            )
            self.assertIsNotNone(snapshot.asset.residual_path)

    def test_event_level_track_switch_does_not_double_mix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset, track = _asset(root)
            track = track.model_copy(update={"generation_model": "t2a"})
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
                        asset, store, str(track.sound_track_id), "selsync", None, None
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
                        asset, store, str(track.sound_track_id), "selsync", None, None
                    )
                )
            snapshot = asyncio.run(store.snapshot())

            self.assertEqual(snapshot.status, "failed")
            self.assertIn("boom", snapshot.error)
            self.assertEqual(snapshot.asset.sound_track_audio_artifacts, [old])
            status = snapshot.asset.track_status[track.sound_track_id]
            self.assertEqual(status.state, "failed")
            self.assertIn("boom", status.error)


if __name__ == "__main__":
    unittest.main()
