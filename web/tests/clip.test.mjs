import assert from "node:assert/strict";
import test from "node:test";
import { createServer } from "vite";

test("clip geometry and drag clamping", async (context) => {
  const server = await createServer({
    appType: "custom",
    optimizeDeps: { noDiscovery: true },
    server: { middlewareMode: true },
  });
  context.after(() => server.close());
  const c = await server.ssrLoadModule("/src/lib/clip.ts");
  const n = await server.ssrLoadModule("/src/lib/newTrack.ts");

  // Defaults: the whole stem, unshifted.
  assert.deepEqual(c.resolveClip({}, 4), { start: 0, end: 4, offset: 0 });
  assert.deepEqual(c.resolveClip({ clip_start_sec: 1, clip_end_sec: 2.5, clip_offset_sec: 0.5 }, 4), {
    start: 1,
    end: 2.5,
    offset: 0.5,
  });
  assert.ok(c.isFullClip({ start: 0, end: 4, offset: 0 }, 4));
  assert.ok(!c.isFullClip({ start: 0, end: 4, offset: 0.1 }, 4));
  // The artifact duration is rounded to 1 ms, so a clip a hair short of it is still the full extent.
  assert.ok(c.isFullClip({ start: 0, end: 9.766666, offset: 0 }, 9.767));
  assert.ok(!c.isFullClip({ start: 0, end: 9.7, offset: 0 }, 9.767));

  // Placed at start + offset on a 4 s timeline.
  assert.deepEqual(c.clipPlacement({ start: 1, end: 2.5, offset: 0.5 }, 4), { left: 37.5, width: 37.5 });

  // Peaks cover the stem; the clip shows its own slice.
  const peaks = Array.from({ length: 40 }, (_, i) => i / 40);
  assert.deepEqual(c.clipPeaks(peaks, { start: 1, end: 2, offset: 3 }, 4), peaks.slice(10, 20));
  assert.deepEqual(c.clipPeaks(peaks, { start: 0, end: 4, offset: 0 }, 4), peaks);
  assert.deepEqual(c.clipPeaks([], { start: 0, end: 1, offset: 0 }, 4), []);

  const full = { start: 0, end: 4, offset: 0 };
  const drag = (mode, clip, delta) => c.dragClip(mode, clip, delta, 4, 4, 30);
  // Trims keep the other edge; the right edge cannot pass the stem/video end, nor the left one the start.
  assert.deepEqual(drag("trim-right", full, -1), { start: 0, end: 3, offset: 0 });
  assert.deepEqual(drag("trim-right", full, 2), full);
  assert.deepEqual(drag("trim-left", full, 1), { start: 1, end: 4, offset: 0 });
  assert.deepEqual(drag("trim-left", full, -1), full);
  // Minimum length.
  assert.deepEqual(drag("trim-left", full, 9), { start: 3.9, end: 4, offset: 0 });
  assert.deepEqual(drag("trim-right", { start: 1, end: 2, offset: 0 }, -9), { start: 1, end: 1.1, offset: 0 });
  // A trimmed clip can be dragged back out to the full extent.
  assert.deepEqual(drag("trim-left", { start: 1, end: 4, offset: 0 }, -5), full);
  // Moves keep the length and stay inside the video.
  const mid = { start: 1, end: 2, offset: 0 };
  assert.deepEqual(drag("move", mid, 1), { start: 1, end: 2, offset: 1 });
  assert.deepEqual(drag("move", mid, 9), { start: 1, end: 2, offset: 2 });
  assert.deepEqual(drag("move", mid, -9), { start: 1, end: 2, offset: -1 });
  // A moved clip cannot be trimmed past the video edges.
  assert.deepEqual(drag("trim-right", { start: 1, end: 2, offset: 1.5 }, 9), { start: 1, end: 2.5, offset: 1.5 });
  assert.deepEqual(drag("trim-left", { start: 1, end: 2, offset: -0.5 }, -9), { start: 0.5, end: 2, offset: -0.5 });
  // Frame snapping: 1/30 s steps.
  assert.deepEqual(drag("move", mid, 0.05), { start: 1, end: 2, offset: 0.0667 });

  // New-track draft span: 2 s from the playhead for every model, clamped to the video.
  assert.deepEqual(n.newTrackSpan(40, 120, 30), { start_frame: 40, end_frame: 100 });
  assert.deepEqual(n.newTrackSpan(100, 120, 30), { start_frame: 100, end_frame: 120 });
  assert.deepEqual(n.newTrackSpan(500, 120, 30), { start_frame: 119, end_frame: 120 });
});
