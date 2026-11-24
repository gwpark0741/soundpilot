from __future__ import annotations

import asyncio
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from scipy.io import wavfile

from test_track_edit import _asset, _fake_mix
from v2a_inspect.audio_generation import mix as mix_module
from v2a_inspect.models import SoundTrack, VideoAsset
from v2a_inspect.models.audio_plan import AudioPlanItem
from v2a_inspect.ui.bundle import read_bundle, write_bundle
from v2a_inspect.ui.pipeline import run_track_clip_pipeline
from v2a_inspect.ui.routes import create_router
from v2a_inspect.ui.store import VideoAssetStore
from v2a_inspect.ui.track_edit import reset_clips, set_clip, set_gain


class GainModelTest(unittest.TestCase):
    def test_default_validation_and_old_json(self) -> None:
        old = {"track_type": "sfx", "label": "Old", "generation_model": "t2a"}
        self.assertEqual(SoundTrack.model_validate(old).gain, 1.0)
        for bad in (-0.1, 4.01):
            with self.assertRaises(ValidationError):
                SoundTrack.model_validate({**old, "gain": bad})
        self.assertEqual(SoundTrack.model_validate({**old, "gain": 4}).gain, 4.0)

    def test_gain_survives_clip_reset_and_edits(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            asset, (door, _wind) = _asset(Path(temp_dir))
            asset = set_gain(asset, door.sound_track_id, 1.5)
            asset = set_clip(asset, door.sound_track_id, 1.0, 2.5, 0.5)
            cleared = reset_clips(asset.sound_timeline)
            self.assertEqual(cleared.sound_tracks[0].gain, 1.5)
            self.assertFalse(cleared.sound_tracks[0].has_clip)
            with self.assertRaises(LookupError):
                set_gain(asset, uuid4(), 1.0)
            with self.assertRaises(ValueError):
                set_gain(
                    asset.model_copy(update={"sound_track_audio_artifacts": []}),
                    door.sound_track_id,
                    1.0,
                )

    def test_gain_survives_bundle_and_asset_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "a" / "v2a-inspect-ui").mkdir(parents=True)
            asset, (door, _wind) = _asset(root / "a" / "v2a-inspect-ui")
            asset = set_gain(asset, door.sound_track_id, 0.35)
            buf = io.BytesIO()
            write_bundle(asset, root / "a" / "v2a-inspect-ui", buf)
            buf.seek(0)
            loaded = read_bundle(buf, root / "b" / "v2a-inspect-ui")
            self.assertEqual(loaded.sound_timeline.sound_tracks[0].gain, 0.35)
            exported = VideoAsset.model_validate_json(
                asset.model_dump_json(exclude_computed_fields=True)
            )
            self.assertEqual(exported.sound_timeline.sound_tracks[0].gain, 0.35)


class GainMixTest(unittest.TestCase):
    def _stem(self, root: Path, volume: float) -> np.ndarray:
        src = root / "in.wav"
        wavfile.write(src, 8000, np.full(8000, 8000, dtype=np.int16))
        item = AudioPlanItem(
            item_id="a", type="sfx", time=(0.0, 1.0), description="x", volume=volume
        )
        out = root / f"out{volume}.wav"
        mix_module.mix_audio_items_to_wav(
            [item], {"a": str(src)}, str(out), total_duration=1.0, fps=8000
        )
        return wavfile.read(out)[1].astype(float)

    def test_item_volume_scales_the_mix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            base = self._stem(root, 1.0)[2000:4000].mean()
            self.assertEqual(self._stem(root, 0.0)[2000:4000].max(), 0)
            self.assertAlmostEqual(
                self._stem(root, 2.0)[2000:4000].mean() / base, 2.0, places=2
            )

    def test_limited_composite_never_exceeds_full_scale(self) -> None:
        from moviepy import AudioFileClip, CompositeAudioClip

        with tempfile.TemporaryDirectory() as temp_dir:
            src = Path(temp_dir) / "loud.wav"
            wavfile.write(src, 8000, np.full(16000, 26000, dtype=np.int16))
            clips = [AudioFileClip(str(src)) for _ in range(3)]
            mixed = CompositeAudioClip(clips).transform(mix_module._soft_limit)
            peak = np.abs(mixed.get_frame(np.linspace(0.01, 0.15, 50))).max()
        self.assertLessEqual(peak, 1.0)
        self.assertGreater(peak, 0.95)

    def test_soft_limit_passes_quiet_and_caps_loud(self) -> None:
        x = np.array([0.0, 0.5, -0.9, 1.0, 2.0, -4.0])
        y = mix_module._soft_limit(lambda _t: x, 0)
        self.assertTrue(np.array_equal(y[:3], x[:3]))
        self.assertTrue(np.all(np.abs(y) <= 1.0))
        self.assertGreater(y[4], y[3])
        self.assertLess(y[5], -y[4])


class GainEndpointTest(unittest.TestCase):
    def _client(self, asset, *, running=False):
        store = VideoAssetStore()
        if asset is not None:
            asyncio.run(store.set_complete_asset(asset))
        if running:
            asyncio.run(store.set_running(stage="busy"))
        app = FastAPI()
        app.include_router(create_router(store))
        return TestClient(app), store

    def test_guards(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            asset, (door, _wind) = _asset(Path(temp_dir))
            url = f"/api/audio/tracks/{door.sound_track_id}/gain"
            client, _ = self._client(asset, running=True)
            self.assertEqual(client.post(url, json={"gain": 1}).status_code, 409)
            self.assertEqual(
                self._client(None)[0].post(url, json={"gain": 1}).status_code, 404
            )
            client, _ = self._client(asset)
            ghost = "/api/audio/tracks/00000000-0000-0000-0000-000000000000/gain"
            self.assertEqual(client.post(ghost, json={"gain": 1}).status_code, 404)
            for bad in (-0.5, 4.5):
                self.assertEqual(client.post(url, json={"gain": bad}).status_code, 422)
            bare = asset.model_copy(update={"sound_track_audio_artifacts": []})
            client, _ = self._client(bare)
            self.assertEqual(client.post(url, json={"gain": 1}).status_code, 422)

    def test_stores_gain_and_remixes_without_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            asset, (door, wind) = _asset(Path(temp_dir))
            client, store = self._client(asset)
            with patch(
                "v2a_inspect.ui.pipeline.mix_audio_into_video", side_effect=_fake_mix
            ) as mix:
                response = client.post(
                    f"/api/audio/tracks/{door.sound_track_id}/gain", json={"gain": 1.5}
                )
            self.assertEqual(response.json(), {"status": "queued", "gain": 1.5})
            snapshot = asyncio.run(store.snapshot())
            tracks = {t.sound_track_id: t for t in snapshot.asset.sound_timeline.sound_tracks}
            self.assertEqual(tracks[door.sound_track_id].gain, 1.5)
            self.assertEqual(tracks[wind.sound_track_id].gain, 1.0)
            volumes = {i.item_id: i.volume for i in mix.call_args.kwargs["audio_plan"].items}
            self.assertEqual(volumes[str(door.sound_track_id)], 1.5)
            self.assertEqual(volumes[str(wind.sound_track_id)], 1.0)

    def test_pipeline_reads_gain_from_the_timeline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            asset, (door, _wind) = _asset(Path(temp_dir))
            asset = set_gain(asset, door.sound_track_id, 0.0)
            with patch(
                "v2a_inspect.ui.pipeline.mix_audio_into_video", side_effect=_fake_mix
            ) as mix:
                asyncio.run(run_track_clip_pipeline(asset, VideoAssetStore()))
            items = {i.item_id: i for i in mix.call_args.kwargs["audio_plan"].items}
            self.assertEqual(items[str(door.sound_track_id)].volume, 0.0)
