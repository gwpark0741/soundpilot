from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from v2a_inspect.models import (
    SoundEvent,
    SoundEventAudioArtifact,
    SoundTimeline,
    SoundTrack,
    VideoAsset,
)
from v2a_inspect.ui.pipeline import (
    run_event_audio_delete_pipeline,
    run_event_audio_regeneration_pipeline,
)
from v2a_inspect.ui.store import VideoAssetStore


class UiAudioEventTest(unittest.TestCase):
    def test_delete_event_audio_removes_artifact(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                track = SoundTrack(track_type="sfx", label="Door")
                event = SoundEvent(
                    sound_track_id=track.sound_track_id,
                    start_frame_index=0,
                    end_frame_index=30,
                    description="Door slam",
                )
                wav_path = root / "event.wav"
                wav_path.write_bytes(b"wav")
                artifact = SoundEventAudioArtifact(
                    sound_event_id=event.sound_event_id,
                    sound_track_id=track.sound_track_id,
                    path=wav_path,
                    duration_sec=1.0,
                    generation_model="t2a",
                    description="Door slam",
                )
                asset = VideoAsset(
                    source_path=root / "video.mp4",
                    frame_count=30,
                    sound_timeline=SoundTimeline(
                        sound_tracks=[track],
                        sound_events=[event],
                    ),
                    sound_event_audio_artifacts=[artifact],
                )
                store = VideoAssetStore()
                rebuilt = asset.model_copy(update={"sound_event_audio_artifacts": []})

                with patch(
                    "v2a_inspect.ui.pipeline._rebuild_audio_outputs",
                    new=AsyncMock(return_value=rebuilt),
                ) as rebuild:
                    await run_event_audio_delete_pipeline(
                        asset, store, str(event.sound_event_id)
                    )

                snapshot = await store.snapshot()
                self.assertEqual(snapshot.status, "complete")
                self.assertEqual(snapshot.asset.sound_event_audio_artifacts, [])
                self.assertFalse(wav_path.exists())
                rebuild.assert_awaited_once()

        asyncio.run(run())

    def test_regenerate_event_audio_updates_prompt(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                track = SoundTrack(track_type="speech", label="Narrator")
                event = SoundEvent(
                    sound_track_id=track.sound_track_id,
                    start_frame_index=0,
                    end_frame_index=30,
                    description="Old voice direction",
                    spoken_text="Old line",
                )
                asset = VideoAsset(
                    source_path=root / "video.mp4",
                    frame_count=30,
                    sound_timeline=SoundTimeline(
                        sound_tracks=[track],
                        sound_events=[event],
                    ),
                )
                new_artifact = SoundEventAudioArtifact(
                    sound_event_id=event.sound_event_id,
                    sound_track_id=track.sound_track_id,
                    path=root / "new.wav",
                    duration_sec=1.0,
                    generation_model="tts",
                    description="New voice direction",
                )

                async def rebuild(video_asset, _audio_plan, artifacts, _store):
                    return video_asset.model_copy(
                        update={"sound_event_audio_artifacts": artifacts}
                    )

                store = VideoAssetStore()
                with (
                    patch(
                        "v2a_inspect.ui.pipeline._generate_event_audio_artifact",
                        new=AsyncMock(return_value=new_artifact),
                    ),
                    patch(
                        "v2a_inspect.ui.pipeline._rebuild_audio_outputs",
                        new=AsyncMock(side_effect=rebuild),
                    ),
                ):
                    await run_event_audio_regeneration_pipeline(
                        asset,
                        store,
                        str(event.sound_event_id),
                        "New voice direction",
                        "New line",
                        None,
                    )

                snapshot = await store.snapshot()
                next_event = snapshot.asset.sound_timeline.sound_events[0]
                self.assertEqual(snapshot.status, "complete")
                self.assertEqual(next_event.description, "New voice direction")
                self.assertEqual(next_event.spoken_text, "New line")
                self.assertEqual(
                    snapshot.asset.sound_event_audio_artifacts[0].sound_event_id,
                    event.sound_event_id,
                )

        asyncio.run(run())

    def test_regenerate_reports_generation_error(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                track = SoundTrack(track_type="speech", label="Narrator")
                event = SoundEvent(
                    sound_track_id=track.sound_track_id,
                    start_frame_index=0,
                    end_frame_index=30,
                    description="Voice direction",
                    spoken_text="A line",
                )
                asset = VideoAsset(
                    source_path=root / "video.mp4",
                    frame_count=30,
                    sound_timeline=SoundTimeline(
                        sound_tracks=[track],
                        sound_events=[event],
                    ),
                )
                store = VideoAssetStore()
                with patch(
                    "v2a_inspect.ui.pipeline.generate_audio_for_item",
                    side_effect=RuntimeError(
                        "Audyssey tools server unreachable at http://127.0.0.1:8700: "
                        "[Errno 111] Connection refused"
                    ),
                ):
                    await run_event_audio_regeneration_pipeline(
                        asset,
                        store,
                        str(event.sound_event_id),
                        "Voice direction",
                        "A line",
                        None,
                    )

                snapshot = await store.snapshot()
                # Top bar: short copy, no host/port/errno. Detail stays in the track status.
                self.assertEqual(snapshot.error, "Narrator failed. Previous audio kept.")
                self.assertNotIn("127.0.0.1", snapshot.error)
                self.assertNotIn("Errno", snapshot.error)
                status = snapshot.asset.track_status[track.sound_track_id]
                self.assertEqual(status.state, "failed")
                self.assertIn("unreachable", status.error)

        asyncio.run(run())


class TopBarErrorTest(unittest.TestCase):
    def test_tools_server_details_are_hidden_others_kept(self) -> None:
        from v2a_inspect.ui.pipeline import _top_bar_error

        for raw in (
            "Audyssey tools server unreachable at http://127.0.0.1:8700: [Errno 111]",
            "Audyssey /generate failed (500): boom",
        ):
            self.assertEqual(_top_bar_error(RuntimeError(raw), "Short."), "Short.")
        self.assertEqual(_top_bar_error(ValueError("Audio prompt is required."), "Short."), "Audio prompt is required.")


class ResidualToggleErrorTest(unittest.TestCase):
    def test_failure_top_bar_has_no_raw_detail(self) -> None:
        from v2a_inspect.ui.pipeline import run_residual_toggle_pipeline

        async def run() -> None:
            track = SoundTrack(track_type="sfx", label="Door")
            asset = VideoAsset(
                source_path=Path("video.mp4"),
                frame_count=30,
                sound_timeline=SoundTimeline(sound_tracks=[track], sound_events=[]),
            )
            store = VideoAssetStore()
            with patch(
                "v2a_inspect.ui.pipeline._rebuild_audio_outputs",
                new=AsyncMock(side_effect=RuntimeError("ffmpeg at /tmp/x: Errno 2")),
            ):
                with self.assertLogs("v2a_inspect.ui.pipeline", "ERROR"):
                    await run_residual_toggle_pipeline(asset, store, True)
            snapshot = await store.snapshot()
            self.assertEqual(snapshot.error, "Could not change the rest of original audio.")

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
