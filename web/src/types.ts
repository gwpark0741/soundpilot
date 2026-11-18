export type RunStatus = "idle" | "running" | "complete" | "failed";

export type SoundTrackType = "speech" | "sfx" | "ambience";
export type GenerationModel = "t2a" | "v2a" | "tts" | "separate" | "selsync";
export type TrackStatus = { state: "running" | "done" | "failed"; error?: string | null };

export interface AssetResponse {
  status: RunStatus;
  stage: string | null;
  error: string | null;
  version: number;
  asset_version: number;
  updated_at: string;
  video: VideoSummary | null;
  timeline_rows: TimelineRow[];
  audio_tracks: AudioTrackArtifact[];
  audio_events: AudioEventArtifact[];
  track_targets: Record<string, string[]>;
}

export interface FullAssetResponse extends AssetResponse {
  asset: VideoAsset | null;
}

export interface AssetUpdateEvent {
  status: RunStatus;
  stage: string | null;
  error: string | null;
  version: number;
  asset_version: number;
}

export interface VideoAsset {
  video_id: string;
  source_path: string;
  sam3_tracking_path?: string | null;
  frame_count: number;
  width?: number;
  height?: number;
  initial_scenes: InitialScene[];
  visual_identity_layer?: VisualIdentityLayer | null;
  sound_timeline?: SoundTimeline | null;
  synthesized_video_path?: string | null;
  sound_event_audio_artifacts?: AudioEventArtifact[];
  sound_track_audio_artifacts?: AudioTrackArtifact[];
  raw_video_path?: string | null;
  original_audio_path?: string | null;
  has_audio?: boolean;
  separated_stem_paths?: Record<string, string>;
  track_status?: Record<string, TrackStatus>;
  residual_path?: string | null;
  residual_enabled?: boolean;
}


export interface AudioEventArtifact {
  sound_event_id: string;
  sound_track_id: string;
  path: string;
  duration_sec: number;
  generation_model: string;
  description: string;
}

export interface AudioTrackArtifact {
  sound_track_id: string;
  track_label: string;
  track_type: string;
  path: string;
  duration_sec: number;
  event_count: number;
  waveform_peaks: number[];
  origin?: "mixed" | "separated" | "generated";
  seed?: number | null;
  attempt?: number | null;
  description?: string | null;
}

export interface VideoSummary {
  video_id: string;
  frame_count: number;
  fps: number;
  duration_sec: number;
  width: number;
  height: number;
}

export interface InitialScene {
  initial_scene_id: string;
  start_frame_index: number;
  end_frame_index: number;
  keyframes: Keyframe[];
  initial_analysis?: unknown | null;
  scene_tracks: SceneTrack[];
}

export interface Keyframe {
  keyframe_id: string;
  frame_index: number;
  image_path: string;
}

export interface SceneTrack {
  start_frame_index: number;
  end_frame_index: number;
  confidence: number;
  points: SceneTrackPoint[];
  source_object_seed?: { label?: string | null } | null;
  tracking_prompt?: string | null;
}

export interface SceneTrackPoint {
  frame_index: number;
  bbox_xyxy: [number, number, number, number] | null;
  confidence: number;
  mask?: unknown | null;
}

export interface TrackWindowResponse {
  version: number;
  start_frame: number;
  end_frame: number;
  tracks: TrackWindowTrack[];
}

export interface TrackWindowTrack {
  scene: number;
  track: number;
  scene_track_id: string;
  label: string;
  start_frame: number;
  end_frame: number;
  confidence: number;
  points: TrackWindowPoint[];
}

export interface TrackWindowPoint {
  frame_index: number;
  bbox_xyxy: [number, number, number, number];
  confidence: number;
  mask_rle?: string | null;
}

export interface VisualIdentityLayer {
  visual_objects: unknown[];
  visual_events: VisualEvent[];
}

export interface VisualEvent {
  event_type: string;
  start_frame_index: number;
  end_frame_index: number;
  description: string;
}

export interface SoundTimeline {
  sound_sources: SoundSource[];
  sound_tracks: SoundTrack[];
  sound_events: SoundEvent[];
  notes?: string | null;
}

export interface SoundSource {
  sound_source_id: string;
  source_type: string;
  label: string;
  visual_object_id?: string | null;
  notes?: string | null;
}

export interface SoundTrack {
  sound_track_id: string;
  track_type: SoundTrackType;
  label: string;
  canonical_key?: string | null;
  sound_source_id?: string | null;
  generation_model: GenerationModel;
  description?: string | null;
  notes?: string | null;
}

export interface SoundEvent {
  sound_event_id: string;
  sound_track_id: string;
  start_frame_index: number;
  end_frame_index: number;
  description: string;
  spoken_text?: string | null;
  notes?: string | null;
}

export interface TimelineRow {
  lane: string;
  label: string;
  start_frame: number;
  end_frame: number;
  kind: string;
  sound_event_id?: string;
  sound_track_id?: string;
  generation_model?: GenerationModel;
  spoken_text?: string | null;
}
