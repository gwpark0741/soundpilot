import unittest

from v2a_inspect.audio_generation.routing import apply_default_routing
from v2a_inspect.models import SoundTimeline, SoundTrack


def _timeline() -> SoundTimeline:
    return SoundTimeline(
        sound_tracks=[
            SoundTrack(track_type="sfx", label="S", generation_model="v2a"),
            SoundTrack(track_type="ambience", label="A", generation_model="t2a"),
            SoundTrack(track_type="speech", label="V", generation_model="tts"),
        ]
    )


def _models(timeline: SoundTimeline) -> dict[str, str]:
    return {t.track_type: t.generation_model for t in timeline.sound_tracks}


class ApplyDefaultRoutingTests(unittest.TestCase):
    def test_original_audio_routes_every_track_to_separate(self) -> None:
        routed = apply_default_routing(_timeline(), has_audio=True)
        self.assertEqual(
            _models(routed),
            {"sfx": "separate", "ambience": "separate", "speech": "separate"},
        )

    def test_silent_video_routes_sfx_ambience_to_selsync_speech_to_tts(self) -> None:
        routed = apply_default_routing(_timeline(), has_audio=False)
        self.assertEqual(
            _models(routed),
            {"sfx": "selsync", "ambience": "selsync", "speech": "tts"},
        )

    def test_input_timeline_is_not_mutated(self) -> None:
        timeline = _timeline()
        routed = apply_default_routing(timeline, has_audio=True)
        self.assertIsNot(routed, timeline)
        self.assertEqual(
            _models(timeline), {"sfx": "v2a", "ambience": "t2a", "speech": "tts"}
        )


if __name__ == "__main__":
    unittest.main()
