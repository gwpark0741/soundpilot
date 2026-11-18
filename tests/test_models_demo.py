import unittest
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from v2a_inspect.models import (
    SoundTrack,
    SoundTrackAudioArtifact,
    TrackStatus,
    VideoAsset,
)

SAMPLE = Path(__file__).parent.parent / "samples" / "sample.video-asset.json"


class SoundTrackModelTests(unittest.TestCase):
    def test_speech_track_keeps_separate(self) -> None:
        track = SoundTrack(track_type="speech", label="V", generation_model="separate")
        self.assertEqual(track.generation_model, "separate")

    def test_speech_track_defaults_to_tts(self) -> None:
        track = SoundTrack(track_type="speech", label="V", generation_model="t2a")
        self.assertEqual(track.generation_model, "tts")

    def test_sfx_rejects_tts(self) -> None:
        with self.assertRaises(ValidationError):
            SoundTrack(track_type="sfx", label="S", generation_model="tts")

    def test_sfx_accepts_separate_and_selsync(self) -> None:
        for model in ("separate", "selsync"):
            track = SoundTrack(track_type="sfx", label="S", generation_model=model)
            self.assertEqual(track.generation_model, model)

    def test_description_defaults_to_none(self) -> None:
        self.assertIsNone(SoundTrack(track_type="sfx", label="S").description)


class VideoAssetModelTests(unittest.TestCase):
    def test_old_asset_json_loads(self) -> None:
        asset = VideoAsset.model_validate_json(SAMPLE.read_text())
        self.assertIs(asset.has_audio, False)
        self.assertIsNone(asset.raw_video_path)
        self.assertIsNone(asset.original_audio_path)
        self.assertIsNone(asset.residual_path)
        self.assertIs(asset.residual_enabled, False)
        self.assertEqual(asset.separated_stem_paths, {})
        self.assertEqual(asset.track_status, {})

    def test_asset_roundtrip_new_fields(self) -> None:
        track_id = uuid4()
        asset = VideoAsset(
            source_path=Path("a.mp4"),
            frame_count=30,
            raw_video_path=Path("raw.mp4"),
            original_audio_path=Path("orig.wav"),
            has_audio=True,
            separated_stem_paths={track_id: Path("stem.wav")},
            track_status={track_id: TrackStatus(state="failed", error="boom")},
            residual_path=Path("residual.wav"),
            residual_enabled=True,
            sound_track_audio_artifacts=[
                SoundTrackAudioArtifact(
                    sound_track_id=track_id,
                    track_label="V",
                    track_type="sfx",
                    path=Path("t.wav"),
                    duration_sec=1.0,
                    event_count=1,
                    origin="generated",
                    seed=42,
                    attempt=2,
                    description="d",
                )
            ],
        )
        again = VideoAsset.model_validate_json(
            asset.model_dump_json(exclude_computed_fields=True)
        )
        self.assertEqual(again, asset)
        self.assertEqual(again.track_status[track_id].error, "boom")

    def test_artifact_defaults(self) -> None:
        artifact = SoundTrackAudioArtifact(
            sound_track_id=uuid4(),
            track_label="V",
            track_type="sfx",
            path=Path("t.wav"),
            duration_sec=1.0,
            event_count=0,
        )
        self.assertEqual(artifact.origin, "mixed")
        self.assertIsNone(artifact.seed)
        self.assertIsNone(artifact.attempt)
        self.assertIsNone(artifact.description)

    def test_track_status_rejects_unknown_state(self) -> None:
        with self.assertRaises(ValidationError):
            TrackStatus(state="queued")


if __name__ == "__main__":
    unittest.main()
