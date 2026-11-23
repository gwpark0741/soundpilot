import type { TimelineRow } from "../types";

export interface SpanEdit {
  start_frame: number;
  end_frame: number;
}

// Event ids that have unsent span edits, with their edited span.
export type PendingSpans = Record<string, SpanEdit>;

export interface EventSpan extends SpanEdit {
  sound_event_id: string;
}

// Puts the pending spans onto fresh server rows. Edits of events that no longer exist (deleted track,
// other audio method) are dropped; `pending` is the same object when nothing was dropped.
export function reapplyPending(
  rows: TimelineRow[],
  pending: PendingSpans,
): { rows: TimelineRow[]; pending: PendingSpans } {
  const ids = new Set(rows.map((row) => row.sound_event_id));
  const kept = Object.entries(pending).filter(([id]) => ids.has(id));
  const next = kept.length === Object.keys(pending).length ? pending : Object.fromEntries(kept);
  return {
    rows: kept.length
      ? rows.map((row) => {
          const edit = row.sound_event_id ? next[row.sound_event_id] : undefined;
          return edit ? { ...row, start_frame: edit.start_frame, end_frame: edit.end_frame } : row;
        })
      : rows,
    pending: next,
  };
}

// The pending edits of one track's events, in the shape the per-track regenerate sends.
export function trackEventSpans(rows: TimelineRow[], pending: PendingSpans, trackId: string): EventSpan[] {
  return rows.flatMap((row) => {
    const edit = row.sound_event_id ? pending[row.sound_event_id] : undefined;
    return edit && row.sound_track_id === trackId ? [{ sound_event_id: row.sound_event_id!, ...edit }] : [];
  });
}

export function withoutEvents(pending: PendingSpans, sent: EventSpan[]): PendingSpans {
  const ids = new Set(sent.map((event) => event.sound_event_id));
  return Object.fromEntries(Object.entries(pending).filter(([id]) => !ids.has(id)));
}
