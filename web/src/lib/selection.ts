import type { GenerationModel, SoundTrack, TrackStatus } from "../types";

// Order follows the approved mockup (--c-player, --c-enemy, --c-none).
const OBJECT_COLORS = ["#4cc9f0", "#ff7ab6"];
const NONE_COLOR = "#9aa3b2";

export function objectColor(index: number): string {
  return OBJECT_COLORS[index] ?? NONE_COLOR;
}

export function highlightFor(trackId: string | null, targets: Record<string, string[]>): string[] {
  return (trackId && targets[trackId]) || [];
}

export function methodLabel(
  model: GenerationModel,
): "Separated" | "SelVA w/ SelSync" | "ElevenLabs TTS" | "ElevenLabs" | "Hunyuan" {
  return {
    separate: "Separated",
    selsync: "SelVA w/ SelSync",
    tts: "ElevenLabs TTS",
    t2a: "ElevenLabs",
    v2a: "Hunyuan",
  }[model] as ReturnType<typeof methodLabel>;
}

// Mirrors the backend's generate-mode routing (audio_generation/routing.py), so a separated track can say
// what "Generate instead" will use. Keep in sync with generation_route / backfill_agent_model.
export function generationLabel(
  track: Pick<SoundTrack, "track_type" | "agent_model" | "generation_model">,
  sourceType?: string,
): "SelVA w/ SelSync" | "ElevenLabs" | "ElevenLabs TTS" {
  if (track.track_type === "speech") return "ElevenLabs TTS";
  if (track.track_type === "ambience" || sourceType === "scene_global" || sourceType === "offscreen_unknown") {
    return "ElevenLabs";
  }
  const gm = track.generation_model;
  const agent = track.agent_model ?? (gm === "t2a" || gm === "v2a" || gm === "tts" ? gm : "v2a");
  return agent === "v2a" ? "SelVA w/ SelSync" : agent === "tts" ? "ElevenLabs TTS" : "ElevenLabs";
}

export type TrackAction = "re-separate" | "generate-instead" | "regenerate";

export function trackActions(model: GenerationModel, hasAudio: boolean, _trackType: string): TrackAction[] {
  if (model === "separate") return ["re-separate", "generate-instead"];
  if (model === "selsync" || model === "t2a" || model === "tts") return hasAudio ? ["regenerate", "re-separate"] : ["regenerate"];
  return [];
}

export function actionMethod(action: TrackAction): "separate" | "generate" {
  return action === "re-separate" ? "separate" : "generate";
}

export function statusView(
  status: TrackStatus | undefined,
  appRunning: boolean,
): { label: "Done" | "Generating…" | "Queued" | "Failed" | null; tone: "ok" | "run" | "wait" | "err" | null } {
  if (status?.state === "done") return { label: "Done", tone: "ok" };
  if (status?.state === "running") return { label: "Generating…", tone: "run" };
  if (status?.state === "failed") return { label: "Failed", tone: "err" };
  return appRunning ? { label: "Queued", tone: "wait" } : { label: null, tone: null };
}

export type TransportAction = "stop-preview" | "pause-video" | "play-solo" | "play-mix";

// Main play button: a running preview stem or video always stops first; only an idle press starts playback.
export function transportAction(s: {
  previewPlaying: boolean;
  videoPlaying: boolean;
  soloTrackId: string | null;
}): TransportAction {
  if (s.previewPlaying) return "stop-preview";
  if (s.videoPlaying) return "pause-video";
  return s.soloTrackId ? "play-solo" : "play-mix";
}

// Solo toggled while something plays: restart with the new choice, else leave playback alone.
// Solo on -> that stem; Solo off -> the mix, but only if a stem preview was playing (the mix already plays otherwise).
export function soloToggleAction(s: {
  previewPlaying: boolean;
  videoPlaying: boolean;
  soloTrackId: string | null;
}): "play-solo" | "play-mix" | null {
  if (!s.previewPlaying && !s.videoPlaying) return null;
  if (s.soloTrackId) return "play-solo";
  return s.previewPlaying ? "play-mix" : null;
}

// Keeps the selection while its track exists; otherwise (first load, new video, loaded project) the first track.
export function reconcileSelection(current: string | null, trackIds: string[]): string | null {
  if (trackIds.length === 0 || (current !== null && trackIds.includes(current))) return current;
  return trackIds[0];
}
