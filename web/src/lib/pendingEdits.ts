import type { TimelineRow } from "../types";

export interface SpanEdit {
  start_frame: number;
  end_frame: number;
}

// A removed box keeps its last span and its track (the row itself is gone from the timeline).
export interface PendingEdit extends SpanEdit {
  deleted?: true;
  sound_track_id?: string;
}

// Event ids that have unsent span edits (a new span or a removal).
export type PendingSpans = Record<string, PendingEdit>;

export interface EventSpan extends SpanEdit {
  sound_event_id: string;
  deleted?: true;
}

// Puts the pending spans onto fresh server rows and drops the removed ones. Edits of events that no longer
// exist (deleted track, other audio method) are dropped; `pending` is the same object when nothing was dropped.
export function reapplyPending(
  rows: TimelineRow[],
  pending: PendingSpans,
): { rows: TimelineRow[]; pending: PendingSpans } {
  const ids = new Set(rows.map((row) => row.sound_event_id));
  const kept = Object.entries(pending).filter(([id]) => ids.has(id));
  const next = kept.length === Object.keys(pending).length ? pending : Object.fromEntries(kept);
  return {
    rows: kept.length
      ? rows.flatMap((row) => {
          const edit = row.sound_event_id ? next[row.sound_event_id] : undefined;
          if (edit?.deleted) return [];
          return [edit ? { ...row, start_frame: edit.start_frame, end_frame: edit.end_frame } : row];
        })
      : rows,
    pending: next,
  };
}

// The pending edits of one track's events, in the shape the per-track regenerate sends.
export function trackEventSpans(rows: TimelineRow[], pending: PendingSpans, trackId: string): EventSpan[] {
  const moved = rows.flatMap((row) => {
    const edit = row.sound_event_id ? pending[row.sound_event_id] : undefined;
    return edit && !edit.deleted && row.sound_track_id === trackId
      ? [{ sound_event_id: row.sound_event_id!, start_frame: edit.start_frame, end_frame: edit.end_frame }]
      : [];
  });
  const removed = Object.entries(pending).flatMap(([id, edit]) =>
    edit.deleted && edit.sound_track_id === trackId
      ? [{ sound_event_id: id, start_frame: edit.start_frame, end_frame: edit.end_frame, deleted: true as const }]
      : [],
  );
  return [...moved, ...removed];
}

// Marks the row's event as removed. A track keeps at least one box, so the last one is refused (null).
export function removeEvent(rows: TimelineRow[], pending: PendingSpans, eventId: string): PendingSpans | null {
  const row = rows.find((item) => item.sound_event_id === eventId);
  if (!row?.sound_track_id || rows.filter((item) => item.sound_track_id === row.sound_track_id && item.sound_event_id).length <= 1) {
    return null;
  }
  return {
    ...pending,
    [eventId]: { start_frame: row.start_frame, end_frame: row.end_frame, deleted: true, sound_track_id: row.sound_track_id },
  };
}

export function isRemoved(pending: PendingSpans, eventId: string): boolean {
  return pending[eventId]?.deleted === true;
}

export function withoutEvents(pending: PendingSpans, sent: EventSpan[]): PendingSpans {
  const ids = new Set(sent.map((event) => event.sound_event_id));
  return Object.fromEntries(Object.entries(pending).filter(([id]) => !ids.has(id)));
}
