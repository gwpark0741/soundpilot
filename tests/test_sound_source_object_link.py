from __future__ import annotations

import unittest
from pathlib import Path

from v2a_inspect.models import SoundTrack, VideoAsset
from v2a_inspect.tools.sound_timeline.editor import SoundTimelineEditor
from v2a_inspect.tools.sound_timeline.schemas import (
    UpsertSoundSourceArgs,
    resolve_visual_object_id,
    visual_object_labels,
)
from v2a_inspect.ui.rows import track_highlight_targets

SAMPLE = Path(__file__).parent.parent / "samples" / "sample.video-asset.json"


def _asset() -> VideoAsset:
    return VideoAsset.model_validate_json(SAMPLE.read_text())


class SourceObjectLinkTest(unittest.TestCase):
    def test_label_resolves_to_second_same_named_object(self) -> None:
        layer = _asset().visual_identity_layer
        objects = layer.visual_objects
        labels = visual_object_labels(layer)

        self.assertEqual(
            list(labels.values()), [f"player character #{i}" for i in range(1, 5)]
        )
        self.assertEqual(
            resolve_visual_object_id(layer, None, "Player Character #2 "),
            objects[1].visual_object_id,
        )

    def test_unknown_label_lists_valid_labels(self) -> None:
        layer = _asset().visual_identity_layer
        with self.assertRaisesRegex(ValueError, "player character #4"):
            resolve_visual_object_id(layer, None, "player character")

    def test_no_visual_layer_is_an_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "omit visual_object_label"):
            resolve_visual_object_id(None, None, "player character #1")

    def test_id_and_mismatching_label_is_an_error(self) -> None:
        layer = _asset().visual_identity_layer
        first, second = layer.visual_objects[:2]
        with self.assertRaisesRegex(ValueError, "disagree"):
            resolve_visual_object_id(
                layer, first.visual_object_id, "player character #2"
            )
        self.assertEqual(
            resolve_visual_object_id(
                layer, second.visual_object_id, "player character #2"
            ),
            second.visual_object_id,
        )

    def test_id_alone_still_works(self) -> None:
        layer = _asset().visual_identity_layer
        target = layer.visual_objects[2].visual_object_id
        self.assertEqual(resolve_visual_object_id(layer, target, None), target)
        self.assertIsNone(resolve_visual_object_id(layer, None, None))

    def test_args_accept_label(self) -> None:
        args = UpsertSoundSourceArgs(
            source_type="visual_object",
            label="Footsteps",
            visual_object_label="player character #2",
        )
        self.assertEqual(args.visual_object_label, "player character #2")

    def test_upsert_links_object_and_highlights_exactly_one_track(self) -> None:
        asset = _asset()
        editor = SoundTimelineEditor(asset)
        second = asset.visual_identity_layer.visual_objects[1]

        source = editor.upsert_sound_source(
            source_type="visual_object",
            label="player character",
            visual_object_label="player character #2",
        )
        track = SoundTrack(
            track_type="sfx", label="Steps", sound_source_id=source.sound_source_id
        )
        timeline = asset.sound_timeline
        asset.sound_timeline = timeline.model_copy(
            update={"sound_tracks": [*timeline.sound_tracks, track]}
        )

        self.assertEqual(source.visual_object_id, second.visual_object_id)
        targets = track_highlight_targets(asset)[str(track.sound_track_id)]
        self.assertEqual(targets, [str(second.presences[0].scene_track_id)])

    def test_upsert_bad_label_raises_and_writes_nothing(self) -> None:
        editor = SoundTimelineEditor(_asset())
        before = editor.video_asset.sound_timeline
        with self.assertRaisesRegex(ValueError, "Valid labels"):
            editor.upsert_sound_source(
                source_type="visual_object",
                label="x",
                visual_object_label="dragon",
            )
        self.assertEqual(editor.video_asset.sound_timeline, before)

    def test_catalog_prints_object_label(self) -> None:
        asset = _asset()
        editor = SoundTimelineEditor(asset)
        editor.upsert_sound_source(
            source_type="visual_object",
            label="Steps",
            visual_object_label="player character #3",
        )
        text = editor.list_sound_sources("Steps").to_tool_string()
        self.assertIn("player character #3", text)


if __name__ == "__main__":
    unittest.main()
