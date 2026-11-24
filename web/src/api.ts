import type { Clip } from "./lib/clip";
import type { NewTrackModel } from "./lib/newTrack";
import type { EventSpan, SpanEdit } from "./lib/pendingEdits";
import type {
  AssetResponse,
  FullAssetResponse,
  ProjectSummary,
  TrackWindowResponse,
  VideoAsset,
} from "./types";

export async function fetchAssetSummary(): Promise<AssetResponse> {
  const response = await fetch("/api/asset-summary");
  if (!response.ok) {
    throw new Error(`Failed to fetch asset summary: ${response.status}`);
  }
  return response.json();
}

export async function fetchAssetForExport(): Promise<VideoAsset | null> {
  const response = await fetch("/api/asset");
  if (!response.ok) {
    throw new Error(`Failed to fetch asset: ${response.status}`);
  }
  const payload = (await response.json()) as FullAssetResponse;
  return payload.asset;
}

export async function cleanAsset(): Promise<void> {
  const response = await fetch("/api/asset/clean", {
    method: "POST",
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Failed to clean asset: ${response.status}`);
  }
}

export async function fetchTrackingWindow(
  startFrame: number,
  endFrame: number,
): Promise<TrackWindowResponse> {
  const params = new URLSearchParams({
    start_frame: String(startFrame),
    end_frame: String(endFrame),
  });
  const response = await fetch(`/api/tracks/window?${params.toString()}`);
  if (!response.ok) {
    throw new Error(`Failed to fetch tracking window: ${response.status}`);
  }
  return response.json();
}

export async function startRun(form: HTMLFormElement): Promise<void> {
  const formData = new FormData(form);
  const response = await fetch("/api/runs", {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Failed to start run: ${response.status}`);
  }
}

export async function importBundle(form: HTMLFormElement): Promise<void> {
  const response = await fetch("/api/bundle/import", {
    method: "POST",
    body: new FormData(form),
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Failed to import bundle: ${response.status}`);
  }
}

export async function fetchProjects(): Promise<ProjectSummary[]> {
  const response = await fetch("/api/projects");
  if (!response.ok) {
    throw new Error(`Failed to list projects: ${response.status}`);
  }
  return response.json();
}

export async function openProject(id: string): Promise<void> {
  const response = await fetch(`/api/projects/${id}/open`, { method: "POST" });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Failed to open project: ${response.status}`);
  }
}

export async function deleteProject(id: string): Promise<void> {
  const response = await fetch(`/api/projects/${id}`, { method: "DELETE" });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Failed to delete project: ${response.status}`);
  }
}

export async function resetSoundTimeline(): Promise<void> {
  const response = await fetch("/api/sound-timeline/reset-run", {
    method: "POST",
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Failed to reset soundtrack: ${response.status}`);
  }
}

export async function generateAudio(serverUrl: string, asset: VideoAsset | null): Promise<void> {
  const formData = new FormData();
  if (serverUrl.trim()) {
    formData.append("server_url", serverUrl.trim());
  }
  if (asset) {
    const assetBlob = new Blob([JSON.stringify(asset)], { type: "application/json" });
    formData.append("asset", assetBlob, "edited-asset.json");
  }
  const response = await fetch("/api/audio/generate", {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Failed to generate audio: ${response.status}`);
  }
}

export async function regenerateEventAudio(
  soundEventId: string,
  description: string,
  spokenText: string | null,
  serverUrl: string | null,
): Promise<void> {
  const formData = new FormData();
  formData.append("description", description);
  if (spokenText) {
    formData.append("spoken_text", spokenText);
  }
  if (serverUrl) {
    formData.append("server_url", serverUrl);
  }
  const response = await fetch(`/api/audio/events/${soundEventId}/regenerate`, {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Failed to regenerate event audio: ${response.status}`);
  }
}

export async function regenerateTrackAudio(
  trackId: string,
  method: "separate" | "generate",
  description?: string,
  seed?: number,
  events?: EventSpan[],
  model?: "selsync" | "t2a" | "tts",
): Promise<void> {
  const formData = new FormData();
  formData.append("method", method);
  if (model) {
    formData.append("model", model);
  }
  if (description) {
    formData.append("description", description);
  }
  if (seed !== undefined) {
    formData.append("seed", String(seed));
  }
  if (events?.length) {
    formData.append("events", JSON.stringify(events));
  }
  const response = await fetch(`/api/audio/tracks/${trackId}/regenerate`, {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Failed to regenerate track audio: ${response.status}`);
  }
}

export async function setAudioMode(mode: "generate" | "separate"): Promise<void> {
  const formData = new FormData();
  formData.append("mode", mode);
  const response = await fetch("/api/audio/mode", { method: "POST", body: formData });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Failed to set audio mode: ${response.status}`);
  }
}

export async function setResidualEnabled(enabled: boolean): Promise<void> {
  const formData = new FormData();
  formData.append("enabled", String(enabled));
  const response = await fetch("/api/audio/residual", {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Failed to update residual: ${response.status}`);
  }
}

export async function setTrackClip(trackId: string, clip: Clip): Promise<void> {
  const response = await fetch(`/api/audio/tracks/${trackId}/clip`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(clip),
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Failed to update track audio: ${response.status}`);
  }
}

export async function setTrackGain(trackId: string, gain: number): Promise<void> {
  const response = await fetch(`/api/audio/tracks/${trackId}/gain`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ gain }),
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Failed to update track volume: ${response.status}`);
  }
}

// Keeps the track's audio only inside its span boxes; `events` are the track's pending span edits.
export async function trimTrackToSpans(trackId: string, events?: EventSpan[]): Promise<void> {
  const formData = new FormData();
  if (events?.length) {
    formData.append("events", JSON.stringify(events));
  }
  const response = await fetch(`/api/audio/tracks/${trackId}/trim-to-spans`, { method: "POST", body: formData });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Failed to trim the track: ${response.status}`);
  }
}

export async function undoTrackTrim(trackId: string): Promise<void> {
  const response = await fetch(`/api/audio/tracks/${trackId}/trim-to-spans`, { method: "DELETE" });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Failed to undo the trim: ${response.status}`);
  }
}

export async function deleteTrack(trackId: string): Promise<void> {
  const response = await fetch(`/api/audio/tracks/${trackId}`, { method: "DELETE" });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Failed to delete track: ${response.status}`);
  }
}

export interface NewTrack {
  label: string;
  description: string;
  model: NewTrackModel;
  spoken_text?: string;
  spans: SpanEdit[];
}

export async function createTrack(track: NewTrack): Promise<string> {
  const response = await fetch("/api/audio/tracks", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(track),
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Failed to add track: ${response.status}`);
  }
  return (await response.json()).sound_track_id;
}
