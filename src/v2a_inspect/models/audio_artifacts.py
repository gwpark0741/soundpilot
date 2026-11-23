from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field

from .base import SchemaModel


class SoundEventAudioArtifact(SchemaModel):
    sound_event_id: UUID
    sound_track_id: UUID
    path: Path
    duration_sec: float
    generation_model: str
    description: str


class SoundTrackAudioArtifact(SchemaModel):
    sound_track_id: UUID
    track_label: str
    track_type: Literal["speech", "sfx", "ambience"]
    path: Path
    duration_sec: float
    event_count: int = Field(ge=0)
    waveform_peaks: list[float] = Field(default_factory=list)
    origin: Literal["mixed", "separated", "generated"] = "mixed"
    seed: int | None = None
    attempt: int | None = None
    description: str | None = None
    # Full-length WAV with the track's clip applied; what the preview and Play/Solo use.
    clip_path: Path | None = None

    @property
    def playback_path(self) -> Path:
        return self.clip_path or self.path
