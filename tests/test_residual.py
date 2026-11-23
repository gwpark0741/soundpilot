from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import numpy as np
from scipy.io import wavfile

from v2a_inspect.audio_generation.residual import compute_residual
from v2a_inspect.models import (
    SoundEvent,
    SoundTimeline,
    SoundTrack,
    VideoAsset,
)
from v2a_inspect.ui.pipeline import _refresh_residual, run_audio_generation_pipeline
from v2a_inspect.ui.store import VideoAssetStore

SR = 48000


def _sine(freq: float, n: int = SR) -> np.ndarray:
    return (0.3 * np.sin(2 * np.pi * freq * np.arange(n) / SR)).astype(np.float32)


def _write(path: Path, data: np.ndarray) -> Path:
    wavfile.write(path, SR, data.astype(np.float32))
    return path


class ComputeResidualTest(unittest.TestCase):
    def test_residual_subtracts_stems(self) -> None:
        a, b = _sine(440), _sine(880)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            out = compute_residual(
                _write(root / "orig.wav", a + b),
                [_write(root / "a.wav", a)],
                root / "residual.wav",
            )
            rate, data = wavfile.read(out)

        self.assertEqual((rate, data.dtype), (SR, np.float32))
        self.assertTrue(np.allclose(data, b, atol=1e-5))

    def test_residual_length_mismatch(self) -> None:
        a, b = _sine(440), _sine(880)
        for delta in (37, -37):
            with self.subTest(delta=delta), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                stem = np.resize(a, SR + delta)
                out = compute_residual(
                    _write(root / "orig.wav", a + b),
                    [_write(root / "a.wav", stem)],
                    root / "residual.wav",
                )
                _rate, data = wavfile.read(out)

                self.assertEqual(len(data), SR)
                n = min(SR, SR + delta)
                self.assertTrue(np.allclose(data[:n], b[:n], atol=1e-5))
                self.assertTrue(np.allclose(data[n:], (a + b)[n:], atol=1e-5))


class RefreshResidualTest(unittest.TestCase):
    def _asset(self, root: Path, **update) -> VideoAsset:
        return VideoAsset(source_path=root / "video.mp4", frame_count=30, **update)

    def test_refresh_sets_residual_path_and_skips_missing_stems(self) -> None:
        a, b = _sine(440), _sine(880)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset = self._asset(
                root,
                has_audio=True,
                original_audio_path=_write(root / "orig.wav", a + b),
                separated_stem_paths={
                    UUID(int=1): _write(root / "a.wav", a),
                    UUID(int=2): root / "gone.wav",
                },
            )
            refreshed = asyncio.run(_refresh_residual(asset))

            self.assertEqual(refreshed.residual_path, root / "audio" / "residual.wav")
            _rate, data = wavfile.read(refreshed.residual_path)
            self.assertTrue(np.allclose(data, b, atol=1e-5))

    def test_refresh_noop_without_audio_or_stems(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            silent = self._asset(root)
            no_stems = self._asset(
                root,
                has_audio=True,
                original_audio_path=_write(root / "orig.wav", _sine(440)),
            )
            self.assertIsNone(asyncio.run(_refresh_residual(silent)).residual_path)
            self.assertIsNone(asyncio.run(_refresh_residual(no_stems)).residual_path)

    def test_pipeline_subtracts_stem_of_track_switched_to_generated(self) -> None:
        a, b = _sine(440), _sine(880)
        track = SoundTrack(
            track_type="sfx", label="Door", generation_model="selsync"
        )
        event = SoundEvent(
            sound_track_id=track.sound_track_id,
            start_frame_index=0,
            end_frame_index=30,
            description="door",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset = self._asset(
                root,
                has_audio=True,
                original_audio_path=_write(root / "orig.wav", a + b),
                separated_stem_paths={track.sound_track_id: _write(root / "a.wav", a)},
                sound_timeline=SoundTimeline(
                    sound_tracks=[track], sound_events=[event]
                ),
            )

            def fake_generate(*args):
                return _write(args[-1], _sine(300))

            def fake_mix(*, output_path: str, **_kwargs) -> str:
                Path(output_path).write_bytes(b"mp4")
                return output_path

            store = VideoAssetStore()
            with (
                patch("v2a_inspect.ui.pipeline.generate_track", side_effect=fake_generate),
                patch("v2a_inspect.ui.pipeline.mix_audio_into_video", side_effect=fake_mix),
            ):
                asyncio.run(run_audio_generation_pipeline(asset, store, None))
            snapshot = asyncio.run(store.snapshot())

            self.assertEqual(snapshot.status, "complete")
            path = snapshot.asset.residual_path
            self.assertEqual(path, root / "audio" / "residual.wav")
            _rate, data = wavfile.read(path)
            self.assertTrue(np.allclose(data, b, atol=1e-5))


if __name__ == "__main__":
    unittest.main()
