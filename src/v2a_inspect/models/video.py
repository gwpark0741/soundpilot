from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, computed_field

from .audio_artifacts import SoundEventAudioArtifact, SoundTrackAudioArtifact
from .base import SchemaModel
from .initial_scene import InitialScene
from .sound_timeline import SoundTimeline
from .visual_identity import VisualIdentityLayer


class TrackStatus(SchemaModel):
    state: Literal["running", "done", "failed"]
    error: str | None = None


class VideoAsset(SchemaModel):
    """
    Prepared working video used by the pipeline.

    Assumptions:
    - The file is already normalized by ffmpeg.
    - Resolution is fixed to 1280x720.
    - FPS is fixed to 30.
    - Audio track is removed.
    - All downstream references use frame indices.
    """

    video_id: UUID = Field(default_factory=uuid4)
    source_path: Path
    sam3_tracking_path: Path | None = None

    frame_count: int = Field(gt=0)
    initial_scenes: list[InitialScene] = Field(default_factory=list)
    visual_identity_layer: VisualIdentityLayer | None = None
    sound_timeline: SoundTimeline | None = None
    synthesized_video_path: Path | None = None
    sound_event_audio_artifacts: list[SoundEventAudioArtifact] = Field(
        default_factory=list
    )
    sound_track_audio_artifacts: list[SoundTrackAudioArtifact] = Field(
        default_factory=list
    )
    raw_video_path: Path | None = None
    original_audio_path: Path | None = None
    has_audio: bool = False
    audio_mode: Literal["generate", "separate"] = "generate"
    separated_stem_paths: dict[UUID, Path] = Field(default_factory=dict)
    track_status: dict[UUID, TrackStatus] = Field(default_factory=dict)
    residual_path: Path | None = None
    residual_enabled: bool = False

    @computed_field
    @property
    def width(self) -> int:
        return 1280

    @computed_field
    @property
    def height(self) -> int:
        return 720

    @computed_field
    @property
    def fps(self) -> int:
        return 30

    @computed_field
    @property
    def sam3_tracking_width(self) -> int:
        return 640

    @computed_field
    @property
    def sam3_tracking_height(self) -> int:
        return 360

    @computed_field
    @property
    def sam3_tracking_fps(self) -> int:
        return 30

    @computed_field
    @property
    def duration_sec(self) -> float:
        return self.frame_count / self.fps
