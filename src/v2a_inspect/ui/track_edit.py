"""Pure edits of the active sound timeline: clip a track, delete a track, add a track."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from v2a_inspect.audio_generation.clip import clamp_clip
from v2a_inspect.models import (
    SoundEvent,
    SoundSource,
    SoundTimeline,
    SoundTrack,
    VideoAsset,
)

NewTrackModel = Literal["selsync", "t2a", "tts", "separate"]
_AGENT_MODEL: dict[str, Literal["t2a", "v2a", "tts"]] = {"selsync": "v2a", "tts": "tts"}


def _timeline(asset: VideoAsset) -> SoundTimeline:
    if asset.sound_timeline is None:
        raise LookupError("No sound timeline found in video asset.")
    return asset.sound_timeline


def _replace_track(asset: VideoAsset, track: SoundTrack) -> VideoAsset:
    timeline = _timeline(asset)
    tracks = [
        track if t.sound_track_id == track.sound_track_id else t
        for t in timeline.sound_tracks
    ]
    return asset.model_copy(
        update={"sound_timeline": timeline.model_copy(update={"sound_tracks": tracks})}
    )


def reset_clips(
    timeline: SoundTimeline, track_ids: set[UUID] | None = None
) -> SoundTimeline:
    """Drop the clip of the given tracks (all when None): their audio changed."""
    tracks = [
        t.model_copy(
            update={
                "clip_start_sec": None,
                "clip_end_sec": None,
                "clip_offset_sec": 0.0,
                "clip_segments": None,
            }
        )
        if t.has_clip and (track_ids is None or t.sound_track_id in track_ids)
        else t
        for t in timeline.sound_tracks
    ]
    return timeline.model_copy(update={"sound_tracks": tracks})


def set_clip(
    asset: VideoAsset, track_id: UUID, start: float, end: float, offset: float
) -> VideoAsset:
    """Store the clamped clip on the track; the full original extent clears it."""
    track = next(
        (t for t in _timeline(asset).sound_tracks if t.sound_track_id == track_id), None
    )
    stem = next(
        (a for a in asset.sound_track_audio_artifacts if a.sound_track_id == track_id),
        None,
    )
    if track is None:
        raise LookupError("Sound track not found.")
    if stem is None:
        raise ValueError("The track has no audio yet.")
    start, end, offset = clamp_clip(
        start,
        end,
        offset,
        stem_duration=stem.duration_sec,
        video_duration=asset.duration_sec,
    )
    # artifact duration_sec is rounded to 1 ms, so the full extent can arrive a hair short of it
    if start == 0.0 and end >= stem.duration_sec - 1e-3 and offset == 0.0:
        return _replace_track(
            asset,
            track.model_copy(
                update={
                    "clip_start_sec": None,
                    "clip_end_sec": None,
                    "clip_offset_sec": 0.0,
                }
            ),
        )
    return _replace_track(
        asset,
        track.model_copy(
            update={
                "clip_start_sec": start,
                "clip_end_sec": end,
                "clip_offset_sec": offset,
            }
        ),
    )


def delete_track(asset: VideoAsset, track_id: UUID) -> VideoAsset:
    """Remove the track with its events, orphaned source, audio artifacts and status."""
    timeline = _timeline(asset)
    track = next(
        (t for t in timeline.sound_tracks if t.sound_track_id == track_id), None
    )
    if track is None:
        raise LookupError("Sound track not found.")
    tracks = [t for t in timeline.sound_tracks if t.sound_track_id != track_id]
    used_sources = {t.sound_source_id for t in tracks}
    timeline = timeline.model_copy(
        update={
            "sound_tracks": tracks,
            "sound_events": [
                e for e in timeline.sound_events if e.sound_track_id != track_id
            ],
            "sound_sources": [
                s
                for s in timeline.sound_sources
                if s.sound_source_id != track.sound_source_id
                or s.sound_source_id in used_sources
            ],
        }
    )
    return asset.model_copy(
        update={
            "sound_timeline": timeline,
            "sound_track_audio_artifacts": [
                a
                for a in asset.sound_track_audio_artifacts
                if a.sound_track_id != track_id
            ],
            "sound_event_audio_artifacts": [
                a
                for a in asset.sound_event_audio_artifacts
                if a.sound_track_id != track_id
            ],
            "track_status": {
                k: v for k, v in asset.track_status.items() if k != track_id
            },
            "separated_stem_paths": {
                k: v for k, v in asset.separated_stem_paths.items() if k != track_id
            },
        }
    )


def add_track(
    asset: VideoAsset,
    *,
    label: str,
    description: str,
    model: NewTrackModel,
    spoken_text: str | None,
    spans: list[tuple[int, int]],
) -> tuple[VideoAsset, SoundTrack]:
    """Append one source, track and an event per span; audio comes from the track regeneration."""
    label, description = label.strip(), description.strip()
    spoken_text = (spoken_text or "").strip() or None
    if not label or not description:
        raise ValueError("Name and description are required.")
    if model == "tts" and spoken_text is None:
        raise ValueError("Spoken text is required for speech.")
    spans = sorted((start, min(end, asset.frame_count)) for start, end in spans)
    if not spans or any(not 0 <= start < end for start, end in spans):
        raise ValueError("The event span is outside the video.")
    timeline = _timeline(asset)
    speech = model == "tts"
    # SelSync needs a non-t2a source: routing.generation_route sends offscreen sources to t2a.
    source = SoundSource(
        source_type="visual_object" if model == "selsync" else "offscreen_unknown",
        label=label,
    )
    track = SoundTrack(
        track_type="speech" if speech else "sfx",
        label=label,
        sound_source_id=source.sound_source_id,
        generation_model=model,
        agent_model=_AGENT_MODEL.get(model, "t2a"),
        description=description,
    )
    events = [
        SoundEvent(
            sound_track_id=track.sound_track_id,
            start_frame_index=start,
            end_frame_index=end,
            description=description,
            spoken_text=spoken_text if speech else None,
        )
        for start, end in spans
    ]
    timeline = timeline.model_copy(
        update={
            "sound_sources": [*timeline.sound_sources, source],
            "sound_tracks": [*timeline.sound_tracks, track],
            "sound_events": [*timeline.sound_events, *events],
        }
    )
    return asset.model_copy(update={"sound_timeline": timeline}), track


class LastEventError(ValueError):
    """Deleting every event of a track."""


def set_event_spans(
    asset: VideoAsset,
    track_id: UUID,
    spans: dict[UUID, tuple[int, int]],
    deleted: frozenset[UUID] = frozenset(),
) -> VideoAsset:
    """Move and delete events of one track; each must belong to it, moves must fit in the video.

    Deleting drops the event with its audio artifact; a track keeps at least one event. Spans of
    one track may overlap (no neighbour clamping), so a box can grow over a deleted box's range.
    """
    timeline = _timeline(asset)
    own = {
        e.sound_event_id for e in timeline.sound_events if e.sound_track_id == track_id
    }
    if not (spans.keys() | deleted) <= own:
        raise ValueError("The event does not belong to this track.")
    for start, end in spans.values():
        if not 0 <= start < end <= asset.frame_count:
            raise ValueError("The event span is outside the video.")
    if own and own <= deleted:
        raise LastEventError("A track needs at least one span.")
    events = [
        e.model_copy(
            update={
                "start_frame_index": spans[e.sound_event_id][0],
                "end_frame_index": spans[e.sound_event_id][1],
            }
        )
        if e.sound_event_id in spans
        else e
        for e in timeline.sound_events
        if e.sound_event_id not in deleted
    ]
    return asset.model_copy(
        update={
            "sound_timeline": timeline.model_copy(update={"sound_events": events}),
            "sound_event_audio_artifacts": [
                a
                for a in asset.sound_event_audio_artifacts
                if a.sound_event_id not in deleted
            ],
        }
    )


def trim_segments(asset: VideoAsset, track_id: UUID) -> list[tuple[float, float]]:
    """The track's event spans in video seconds, clamped to the video, overlaps merged."""
    spans = sorted(
        (
            e.start_frame_index / asset.fps,
            min(e.end_frame_index / asset.fps, asset.duration_sec),
        )
        for e in _timeline(asset).sound_events
        if e.sound_track_id == track_id
    )
    merged: list[tuple[float, float]] = []
    for start, end in spans:
        if start >= end:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def set_trim(asset: VideoAsset, track_id: UUID, enabled: bool) -> VideoAsset:
    """Gate the track's audio to its event spans (or lift the gate); the audio itself is untouched."""
    track = next(
        (t for t in _timeline(asset).sound_tracks if t.sound_track_id == track_id), None
    )
    if track is None:
        raise LookupError("Sound track not found.")
    if not any(a.sound_track_id == track_id for a in asset.sound_track_audio_artifacts):
        raise ValueError("The track has no audio yet.")
    segments = trim_segments(asset, track_id) if enabled else None
    return _replace_track(asset, track.model_copy(update={"clip_segments": segments}))
