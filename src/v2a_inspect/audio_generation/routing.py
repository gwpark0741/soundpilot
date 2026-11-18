from __future__ import annotations

from v2a_inspect.models import SoundTimeline, SoundTrack


def _default_model(track: SoundTrack, *, has_audio: bool) -> str:
    if has_audio:
        return "separate"
    return "tts" if track.track_type == "speech" else "selsync"


def apply_default_routing(timeline: SoundTimeline, *, has_audio: bool) -> SoundTimeline:
    """Override every track's generation model by original-audio presence."""
    tracks = [
        track.model_copy(
            update={"generation_model": _default_model(track, has_audio=has_audio)}
        )
        for track in timeline.sound_tracks
    ]
    return timeline.model_copy(update={"sound_tracks": tracks})
