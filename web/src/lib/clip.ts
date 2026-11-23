import type { SoundTrack } from "../types";

// Mirrors audio_generation/clip.py: a clip plays the stem's [start, end] seconds, placed at start + offset.
export const MIN_CLIP_SEC = 0.1;

export interface Clip {
  start: number;
  end: number;
  offset: number;
}

export type ClipDrag = "trim-left" | "trim-right" | "move";

export function resolveClip(
  track: Pick<SoundTrack, "clip_start_sec" | "clip_end_sec" | "clip_offset_sec">,
  stemDuration: number,
): Clip {
  return {
    start: track.clip_start_sec ?? 0,
    end: track.clip_end_sec ?? stemDuration,
    offset: track.clip_offset_sec ?? 0,
  };
}

export function isFullClip(clip: Clip, stemDuration: number): boolean {
  return clip.start === 0 && clip.end >= stemDuration && clip.offset === 0;
}

// Where the placed clip sits on a timeline of `timelineSec` seconds, in percent of its width.
export function clipPlacement(clip: Clip, timelineSec: number): { left: number; width: number } {
  const total = Math.max(timelineSec, 1e-6);
  const left = ((clip.start + clip.offset) / total) * 100;
  return { left, width: ((clip.end - clip.start) / total) * 100 };
}

// The part of the stem's peaks (which cover [0, stemDuration]) that the clip plays.
export function clipPeaks(peaks: number[], clip: Clip, stemDuration: number): number[] {
  if (peaks.length === 0 || stemDuration <= 0) return [];
  const from = Math.floor((clip.start / stemDuration) * peaks.length);
  const to = Math.ceil((clip.end / stemDuration) * peaks.length);
  return peaks.slice(Math.max(0, from), Math.min(peaks.length, Math.max(to, from + 1)));
}

const clamp = (value: number, min: number, max: number) => Math.min(Math.max(value, min), max);
const round = (value: number) => Math.round(value * 1e4) / 1e4;

// Dragging by `deltaSec` (snapped to a frame): trims keep the other edge in place, a move keeps the length.
// The clip stays inside the stem and the video, like the server's clamp.
export function dragClip(
  mode: ClipDrag,
  clip: Clip,
  deltaSec: number,
  stemDuration: number,
  videoDuration: number,
  fps: number,
): Clip {
  const delta = Math.round(deltaSec * fps) / fps;
  if (mode === "trim-left") {
    const start = clamp(clip.start + delta, Math.max(0, -clip.offset), clip.end - MIN_CLIP_SEC);
    return { ...clip, start: round(start) };
  }
  if (mode === "trim-right") {
    const end = clamp(
      clip.end + delta,
      clip.start + MIN_CLIP_SEC,
      Math.min(stemDuration, videoDuration - clip.offset),
    );
    return { ...clip, end: round(end) };
  }
  const offset = clamp(clip.offset + delta, -clip.start, videoDuration - clip.end);
  return { ...clip, offset: round(offset) };
}
