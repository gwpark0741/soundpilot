"""Non-destructive trim/move of a track stem (SoundTrack.clip_*_sec)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from scipy.io import wavfile

from v2a_inspect.models import SoundTrack

MIN_CLIP_SEC = 0.1
FADE_SEC = 0.005  # linear fade at both ends of the placed piece, so a hard cut does not click


def resolve_clip(track: SoundTrack, stem_duration: float) -> tuple[float, float, float]:
    """(start, end, offset) in seconds with the None defaults filled in."""
    start = track.clip_start_sec or 0.0
    end = stem_duration if track.clip_end_sec is None else track.clip_end_sec
    return start, end, track.clip_offset_sec


def clamp_clip(
    start: float,
    end: float,
    offset: float,
    *,
    stem_duration: float,
    video_duration: float,
) -> tuple[float, float, float]:
    """Keep 0 <= start < end <= stem, MIN_CLIP_SEC long, and the placed clip inside the video."""
    if stem_duration < MIN_CLIP_SEC:
        raise ValueError("Track audio is too short to edit.")
    start = min(max(start, 0.0), stem_duration - MIN_CLIP_SEC)
    end = min(max(end, start + MIN_CLIP_SEC), stem_duration)
    end = min(end, start + max(video_duration, MIN_CLIP_SEC))
    offset = min(max(offset, -start), max(video_duration - end, -start))
    return start, end, offset


def render_clip_wav(
    src: Path,
    dst: Path,
    *,
    start: float,
    end: float,
    offset: float,
    video_duration: float,
) -> Path:
    """Write a video-length WAV: silence, with src[start:end] placed at start + offset and faded at both ends."""
    rate, data = wavfile.read(src)
    total = round(video_duration * rate)
    out = np.zeros((total, *data.shape[1:]), dtype=data.dtype)
    piece = data[round(start * rate) : round(end * rate)]
    pos = max(0, round((start + offset) * rate))
    piece = piece[: max(0, total - pos)]
    fade = min(round(FADE_SEC * rate), len(piece) // 2)
    if fade:
        gain = np.ones(len(piece))
        ramp = np.arange(fade) / fade
        gain[:fade] = ramp
        gain[-fade:] = ramp[::-1]
        piece = (piece * gain.reshape(-1, *[1] * (piece.ndim - 1))).astype(data.dtype)
    out[pos : pos + len(piece)] = piece
    wavfile.write(dst, rate, out)
    return dst
