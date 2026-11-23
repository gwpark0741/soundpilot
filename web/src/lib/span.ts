import type { SpanEdit as Span } from "./pendingEdits";

export type { Span };
export type SpanDrag = "move" | "resize-left" | "resize-right";

// A drawn or resized span is at least about 0.1 s long.
export function minSpanFrames(fps: number): number {
  return Math.max(1, Math.round(0.1 * fps));
}

const clamp = (value: number, min: number, max: number) => Math.min(Math.max(value, min), max);

// Dragging a span by `deltaFrames` inside [0, limit]: a resize keeps the other edge and `minFrames` of length,
// a move keeps the length. The timeline's event boxes and the new-track draft use the same rules.
export function dragSpan(mode: SpanDrag, span: Span, deltaFrames: number, limit: number, minFrames = 1): Span {
  const { start_frame: start, end_frame: end } = span;
  if (mode === "resize-left") {
    return { start_frame: clamp(start + deltaFrames, 0, end - minFrames), end_frame: end };
  }
  if (mode === "resize-right") {
    return { start_frame: start, end_frame: clamp(end + deltaFrames, start + minFrames, limit) };
  }
  const length = Math.max(minFrames, end - start);
  const next = clamp(start + deltaFrames, 0, Math.max(0, limit - length));
  return { start_frame: next, end_frame: next + length };
}

// Adds the span drawn between two frames (either order, clamped to [0, limit]); a drag shorter than
// `minFrames` draws nothing.
export function drawSpan(spans: Span[], from: number, to: number, limit: number, minFrames: number): Span[] {
  const start = clamp(Math.min(from, to), 0, limit);
  const end = clamp(Math.max(from, to), 0, limit);
  return end - start < minFrames ? spans : [...spans, { start_frame: start, end_frame: end }];
}

export function removeSpan(spans: Span[], index: number): Span[] {
  return spans.filter((_, i) => i !== index);
}

export function sortSpans(spans: Span[]): Span[] {
  return [...spans].sort((a, b) => a.start_frame - b.start_frame || a.end_frame - b.end_frame);
}

export function formatSpan(span: Span, fps: number): string {
  return `${(span.start_frame / fps).toFixed(1)}–${(span.end_frame / fps).toFixed(1)} s`;
}
