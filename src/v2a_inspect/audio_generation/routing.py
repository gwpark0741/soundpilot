from __future__ import annotations

from typing import Literal
from uuid import UUID

from v2a_inspect.models import SoundSource, SoundTimeline, SoundTrack

_T2A_SOURCE_TYPES = {"scene_global", "offscreen_unknown"}


def backfill_agent_model(track: SoundTrack) -> SoundTrack:
    """Keep the agent's choice; fill it once from the current model or track type."""
    if track.agent_model is not None:
        return track
    if track.generation_model in ("t2a", "v2a", "tts"):
        agent_model = track.generation_model
    elif track.track_type == "speech":
        agent_model = "tts"
    elif track.track_type == "ambience":
        agent_model = "t2a"
    else:
        agent_model = "v2a"
    return track.model_copy(update={"agent_model": agent_model})


def generation_route(
    track: SoundTrack, sources: dict[UUID, SoundSource]
) -> Literal["selsync", "t2a", "tts"]:
    """Generate-mode model for one track; the agent's choice unless a rule overrides."""
    if track.track_type == "speech":
        return "tts"
    source = sources.get(track.sound_source_id) if track.sound_source_id else None
    if track.track_type == "ambience" or (
        source is not None and source.source_type in _T2A_SOURCE_TYPES
    ):
        return "t2a"
    return "selsync" if track.agent_model == "v2a" else track.agent_model or "t2a"


def route_tracks(
    timeline: SoundTimeline,
    *,
    mode: Literal["generate", "separate"],
    has_audio: bool,
) -> SoundTimeline:
    """Set every track's generation model for the audio mode; keep agent_model."""
    if mode == "separate" and not has_audio:
        raise ValueError("No original audio to separate.")
    sources = {s.sound_source_id: s for s in timeline.sound_sources}
    tracks = []
    for track in timeline.sound_tracks:
        track = backfill_agent_model(track)
        model = "separate" if mode == "separate" else generation_route(track, sources)
        tracks.append(track.model_copy(update={"generation_model": model}))
    return timeline.model_copy(update={"sound_tracks": tracks})
