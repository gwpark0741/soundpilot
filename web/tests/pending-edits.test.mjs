import assert from "node:assert/strict";
import test from "node:test";
import { createServer } from "vite";

test("pending span edits are re-applied onto fresh rows and sent per track", async (context) => {
  const server = await createServer({ appType: "custom", optimizeDeps: { noDiscovery: true }, server: { middlewareMode: true } });
  context.after(() => server.close());
  const { reapplyPending, trackEventSpans, withoutEvents } = await server.ssrLoadModule("/src/lib/pendingEdits.ts");

  const row = (event, track, start, end) => ({ lane: "l", label: event, kind: "sfx", sound_event_id: event, sound_track_id: track, start_frame: start, end_frame: end });
  const rows = [row("e1", "t1", 0, 30), row("e2", "t1", 40, 60), row("e3", "t2", 0, 10), { lane: "scene", label: "s", kind: "scene", start_frame: 0, end_frame: 99 }];
  const pending = { e2: { start_frame: 50, end_frame: 80 }, e3: { start_frame: 5, end_frame: 20 } };

  // Fresh server rows get the edits back; untouched rows keep their identity.
  const next = reapplyPending(rows, pending);
  assert.equal(next.pending, pending);
  assert.deepEqual(next.rows.map((r) => [r.start_frame, r.end_frame]), [[0, 30], [50, 80], [5, 20], [0, 99]]);
  assert.equal(next.rows[0], rows[0]);
  assert.equal(next.rows[3], rows[3]);
  assert.equal(rows[1].start_frame, 40);  // input untouched

  // Edits of events that are gone (deleted track) are dropped.
  const gone = reapplyPending(rows.filter((r) => r.sound_track_id !== "t2"), pending);
  assert.deepEqual(gone.pending, { e2: pending.e2 });
  assert.deepEqual(gone.rows.map((r) => r.start_frame), [0, 50, 0]);
  assert.deepEqual(reapplyPending([], pending).pending, {});
  const none = reapplyPending(rows, {});
  assert.deepEqual(none.rows, rows);

  // Only the track's own edits are sent; sending them clears just those.
  const sent = trackEventSpans(rows, pending, "t1");
  assert.deepEqual(sent, [{ sound_event_id: "e2", start_frame: 50, end_frame: 80 }]);
  assert.deepEqual(trackEventSpans(rows, pending, "nope"), []);
  assert.deepEqual(withoutEvents(pending, sent), { e3: pending.e3 });
  assert.deepEqual(pending.e2, { start_frame: 50, end_frame: 80 });
});

test("removing a box is a pending edit that is re-applied, sent as deleted and refused for the last box", async (context) => {
  const server = await createServer({ appType: "custom", optimizeDeps: { noDiscovery: true }, server: { middlewareMode: true } });
  context.after(() => server.close());
  const { reapplyPending, trackEventSpans, removeEvent, isRemoved, withoutEvents } = await server.ssrLoadModule("/src/lib/pendingEdits.ts");

  const row = (event, track, start, end) => ({ lane: "l", label: event, kind: "sfx", sound_event_id: event, sound_track_id: track, start_frame: start, end_frame: end });
  const rows = [row("e1", "t1", 0, 30), row("e2", "t1", 40, 60), row("e3", "t2", 0, 10)];

  // The second box goes; the track keeps its first box, which may then be resized over the removed range.
  const pending = removeEvent(rows, {}, "e2");
  assert.deepEqual(pending, { e2: { start_frame: 40, end_frame: 60, deleted: true, sound_track_id: "t1" } });
  assert.ok(isRemoved(pending, "e2"));
  assert.ok(!isRemoved(pending, "e1"));
  const left = reapplyPending(rows, { ...pending, e1: { start_frame: 0, end_frame: 60 } });
  assert.deepEqual(left.rows.map((r) => [r.sound_event_id, r.end_frame]), [["e1", 60], ["e3", 10]]);

  // Removals travel with the track's other edits, flagged; other tracks are not touched.
  assert.deepEqual(trackEventSpans(rows.filter((r) => r.sound_event_id !== "e2"), { ...pending, e1: { start_frame: 0, end_frame: 60 } }, "t1"), [
    { sound_event_id: "e1", start_frame: 0, end_frame: 60 },
    { sound_event_id: "e2", start_frame: 40, end_frame: 60, deleted: true },
  ]);
  assert.deepEqual(trackEventSpans(rows, pending, "t2"), []);
  assert.deepEqual(withoutEvents(pending, trackEventSpans(rows, pending, "t1")), {});

  // A removal of an event the server no longer has is dropped.
  assert.deepEqual(reapplyPending(rows.filter((r) => r.sound_event_id !== "e2"), pending).pending, {});

  // The only box of a track cannot be removed (also after its sibling was).
  assert.equal(removeEvent(rows, {}, "e3"), null);
  assert.equal(removeEvent(rows.filter((r) => r.sound_event_id !== "e2"), pending, "e1"), null);
  assert.equal(removeEvent(rows, {}, "nope"), null);
});
