import unittest

from v2a_inspect.audio_generation.routing import generation_route, route_tracks
from v2a_inspect.models import SoundSource, SoundTimeline, SoundTrack


def _timeline() -> SoundTimeline:
    visual = SoundSource(source_type="visual_object", label="V")
    scene = SoundSource(source_type="scene_global", label="G")
    offscreen = SoundSource(source_type="offscreen_unknown", label="O")
    return SoundTimeline(
        sound_sources=[visual, scene, offscreen],
        sound_tracks=[
            SoundTrack(
                track_type="sfx",
                label="visual",
                generation_model="v2a",
                sound_source_id=visual.sound_source_id,
            ),
            SoundTrack(track_type="ambience", label="amb", generation_model="v2a"),
            SoundTrack(
                track_type="sfx",
                label="scene",
                generation_model="v2a",
                sound_source_id=scene.sound_source_id,
            ),
            SoundTrack(
                track_type="sfx",
                label="off",
                generation_model="v2a",
                sound_source_id=offscreen.sound_source_id,
            ),
            SoundTrack(track_type="sfx", label="t2a", generation_model="t2a"),
            SoundTrack(track_type="speech", label="speech", generation_model="tts"),
        ],
    )


def _models(timeline: SoundTimeline) -> dict[str, str]:
    return {t.label: t.generation_model for t in timeline.sound_tracks}


class RouteTracksGenerateTests(unittest.TestCase):
    def test_generate_uses_agent_choice_and_t2a_rules(self) -> None:
        routed = route_tracks(_timeline(), mode="generate", has_audio=True)
        self.assertEqual(
            _models(routed),
            {
                "visual": "selsync",
                "amb": "t2a",
                "scene": "t2a",
                "off": "t2a",
                "t2a": "t2a",
                "speech": "tts",
            },
        )

    def test_agent_model_is_kept(self) -> None:
        routed = route_tracks(_timeline(), mode="generate", has_audio=False)
        self.assertEqual(
            [t.agent_model for t in routed.sound_tracks],
            ["v2a", "v2a", "v2a", "v2a", "t2a", "tts"],
        )

    def test_input_timeline_is_not_mutated(self) -> None:
        timeline = _timeline()
        routed = route_tracks(timeline, mode="generate", has_audio=False)
        self.assertIsNot(routed, timeline)
        self.assertEqual(timeline.sound_tracks[0].generation_model, "v2a")
        self.assertIsNone(timeline.sound_tracks[0].agent_model)

    def test_old_asset_infers_agent_model_from_track_type(self) -> None:
        timeline = SoundTimeline(
            sound_tracks=[
                SoundTrack(track_type="sfx", label="S", generation_model="selsync"),
                SoundTrack(
                    track_type="ambience", label="A", generation_model="separate"
                ),
                SoundTrack(track_type="speech", label="V", generation_model="separate"),
            ]
        )
        routed = route_tracks(timeline, mode="generate", has_audio=True)
        self.assertEqual(
            [t.agent_model for t in routed.sound_tracks], ["v2a", "t2a", "tts"]
        )
        self.assertEqual(_models(routed), {"S": "selsync", "A": "t2a", "V": "tts"})


class RouteTracksSeparateTests(unittest.TestCase):
    def test_separate_routes_every_track_and_keeps_agent_model(self) -> None:
        routed = route_tracks(_timeline(), mode="separate", has_audio=True)
        self.assertEqual(set(_models(routed).values()), {"separate"})
        self.assertEqual(routed.sound_tracks[0].agent_model, "v2a")

    def test_separate_without_original_audio_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "No original audio to separate."):
            route_tracks(_timeline(), mode="separate", has_audio=False)

    def test_generate_separate_generate_roundtrip(self) -> None:
        first = route_tracks(_timeline(), mode="generate", has_audio=True)
        back = route_tracks(
            route_tracks(first, mode="separate", has_audio=True),
            mode="generate",
            has_audio=True,
        )
        self.assertEqual(back, first)


class GenerationRouteTests(unittest.TestCase):
    def test_single_track_matches_route_tracks(self) -> None:
        timeline = route_tracks(_timeline(), mode="generate", has_audio=False)
        sources = {s.sound_source_id: s for s in timeline.sound_sources}
        for track in timeline.sound_tracks:
            self.assertEqual(generation_route(track, sources), track.generation_model)


if __name__ == "__main__":
    unittest.main()
