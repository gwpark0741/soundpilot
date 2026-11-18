import type {
  AssetResponse,
  FullAssetResponse,
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

export async function importAsset(form: HTMLFormElement): Promise<void> {
  const formData = new FormData(form);
  const response = await fetch("/api/asset/import", {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Failed to import asset: ${response.status}`);
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
): Promise<void> {
  const formData = new FormData();
  formData.append("method", method);
  if (description) {
    formData.append("description", description);
  }
  if (seed !== undefined) {
    formData.append("seed", String(seed));
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
