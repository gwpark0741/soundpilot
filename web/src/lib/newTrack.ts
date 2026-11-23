export type NewTrackModel = "selsync" | "t2a" | "tts" | "separate";

export const NEW_TRACK_MODELS: { value: NewTrackModel; label: string }[] = [
  { value: "selsync", label: "SelSync (video-synced effects)" },
  { value: "t2a", label: "ElevenLabs (text-to-audio effects)" },
  { value: "tts", label: "Kokoro (speech)" },
  { value: "separate", label: "SAM Audio (separate from the original audio)" },
];

// Default event span of a new track: 2 s from the playhead for ElevenLabs and Kokoro (clamped to the video),
// the whole video for SelSync and SAM Audio.
export function newTrackSpan(
  model: NewTrackModel,
  frame: number,
  frameCount: number,
  fps: number,
): { start_frame: number; end_frame: number } {
  if (model === "selsync" || model === "separate") return { start_frame: 0, end_frame: frameCount };
  const start = Math.min(Math.max(frame, 0), Math.max(frameCount - 1, 0));
  return { start_frame: start, end_frame: Math.min(start + Math.round(2 * fps), frameCount) };
}
