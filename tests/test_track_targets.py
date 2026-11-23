from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from v2a_inspect.models import (
    SoundEvent,
    SoundSource,
    SoundTimeline,
    SoundTrack,
    VideoAsset,
    VisualIdentityLayer,
    VisualObject,
    VisualPresence,
)
from v2a_inspect.ui.routes import _summary_payload
from v2a_inspect.ui.rows import track_highlight, track_highlight_targets
from v2a_inspect.ui.store import VideoAssetSnapshot

SAMPLE = Path(__file__).parent.parent / "samples" / "sample.video-asset.json"


def _presence(scene_track_id: object) -> VisualPresence:
    return VisualPresence(
        start_frame_index=0,
        end_frame_index=10,
        state="visible",
        scene_track_id=scene_track_id,
    )


def _asset(
    source: SoundSource | None, objects: list[VisualObject]
) -> tuple[VideoAsset, SoundTrack]:
    track = SoundTrack(
        track_type="sfx",
        label="Track",
        sound_source_id=None if source is None else source.sound_source_id,
    )
    asset = VideoAsset(
        source_path=Path("video.mp4"),
        frame_count=30,
        visual_identity_layer=VisualIdentityLayer(visual_objects=objects),
        sound_timeline=SoundTimeline(
            sound_sources=[] if source is None else [source],
            sound_tracks=[track],
        ),
    )
    return asset, track


def _obj(label: str, start: int, end: int) -> tuple[VisualObject, str]:
    sid = uuid4()
    presence = VisualPresence(
        start_frame_index=start,
        end_frame_index=end,
        state="visible",
        scene_track_id=sid,
    )
    return VisualObject(label=label, presences=[presence]), str(sid)


class TrackTargetsTest(unittest.TestCase):
    def test_same_label_union_across_scenes(self) -> None:
        a, a_id = _obj("black samurai", 0, 60)
        b, b_id = _obj("Black Samurai ", 215, 253)
        source = SoundSource(
            source_type="visual_object",
            label="Samurai",
            visual_object_id=a.visual_object_id,
        )
        asset, track = _asset(source, [a, b])

        targets, fallbacks = track_highlight(asset)

        self.assertEqual(targets[str(track.sound_track_id)], [a_id, b_id])
        self.assertEqual(fallbacks[str(track.sound_track_id)], [])

    def test_head_noun_fallback_per_event(self) -> None:
        red, red_id = _obj("red samurai sword", 145, 187)
        sword, sword_id = _obj("sword", 193, 240)
        shield, _ = _obj("shield", 193, 240)
        far_sword, _ = _obj("sword", 300, 320)
        source = SoundSource(
            source_type="visual_object",
            label="Sword",
            visual_object_id=red.visual_object_id,
        )
        asset, track = _asset(source, [red, sword, shield, far_sword])
        for start, end in [(168, 187), (193, 239)]:
            asset.sound_timeline.sound_events.append(
                SoundEvent(
                    sound_track_id=track.sound_track_id,
                    start_frame_index=start,
                    end_frame_index=end,
                    description="x",
                )
            )

        targets, fallbacks = track_highlight(asset)
        key = str(track.sound_track_id)

        self.assertEqual(targets[key], [red_id, sword_id])
        self.assertEqual(fallbacks[key], [sword_id])

        # Event 1 alone overlaps an A presence: no fallback at all.
        asset.sound_timeline.sound_events.pop()
        targets, fallbacks = track_highlight(asset)
        self.assertEqual(targets[key], [red_id])
        self.assertEqual(fallbacks[key], [])

    def test_targets_by_visual_object_id(self) -> None:
        scene_track = uuid4()
        obj = VisualObject(
            label="unrelated", presences=[_presence(scene_track), _presence(None)]
        )
        source = SoundSource(
            source_type="visual_object",
            label="Sword",
            visual_object_id=obj.visual_object_id,
        )
        asset, track = _asset(source, [obj])

        self.assertEqual(
            track_highlight_targets(asset),
            {str(track.sound_track_id): [str(scene_track)]},
        )

    def test_targets_dedupe_keep_order(self) -> None:
        a, b = uuid4(), uuid4()
        obj = VisualObject(
            label="x", presences=[_presence(a), _presence(b), _presence(a)]
        )
        source = SoundSource(
            source_type="visual_object",
            label="x",
            visual_object_id=obj.visual_object_id,
        )
        asset, track = _asset(source, [obj])

        self.assertEqual(
            track_highlight_targets(asset)[str(track.sound_track_id)],
            [str(a), str(b)],
        )

    def test_targets_by_label_fallback(self) -> None:
        asset = VideoAsset.model_validate_json(SAMPLE.read_text())
        track = next(
            t
            for t in asset.sound_timeline.sound_tracks
            if t.label == "Player Footsteps"
        )

        targets = track_highlight_targets(asset)[str(track.sound_track_id)]

        self.assertTrue(targets)

    def test_targets_empty_for_scene_global(self) -> None:
        asset = VideoAsset.model_validate_json(SAMPLE.read_text())
        track = next(
            t
            for t in asset.sound_timeline.sound_tracks
            if t.label == "Background Wind Ambience"
        )

        self.assertEqual(track_highlight_targets(asset)[str(track.sound_track_id)], [])

    def test_targets_empty_without_source(self) -> None:
        asset, track = _asset(None, [])

        self.assertEqual(
            track_highlight_targets(asset), {str(track.sound_track_id): []}
        )

    def test_summary_includes_track_targets(self) -> None:
        asset = VideoAsset.model_validate_json(SAMPLE.read_text())
        snapshot = VideoAssetSnapshot(
            asset=asset,
            version=1,
            asset_version=1,
            status="idle",
            current_stage=None,
            error=None,
            updated_at=datetime.now(UTC),
        )

        self.assertEqual(
            _summary_payload(snapshot)["track_targets"], track_highlight_targets(asset)
        )
        self.assertIn("track_target_fallbacks", _summary_payload(snapshot))
        empty = VideoAssetSnapshot(**{**snapshot.__dict__, "asset": None})
        self.assertEqual(_summary_payload(empty)["track_targets"], {})


if __name__ == "__main__":
    unittest.main()
