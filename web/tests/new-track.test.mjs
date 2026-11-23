import assert from "node:assert/strict";
import test from "node:test";
import { createServer } from "vite";

test("draft span geometry: default, draw, move, resize, remove", async (context) => {
  const server = await createServer({ appType: "custom", optimizeDeps: { noDiscovery: true }, server: { middlewareMode: true } });
  context.after(() => server.close());
  const { dragSpan, drawSpan, removeSpan, sortSpans, formatSpan, minSpanFrames } = await server.ssrLoadModule("/src/lib/span.ts");
  const { newTrackSpan } = await server.ssrLoadModule("/src/lib/newTrack.ts");
  const span = (start_frame, end_frame) => ({ start_frame, end_frame });

  // Default span: 2 s from the playhead, clamped to the video.
  assert.deepEqual(newTrackSpan(30, 300, 30), span(30, 90));
  assert.deepEqual(newTrackSpan(280, 300, 30), span(280, 300));
  assert.deepEqual(newTrackSpan(500, 300, 30), span(299, 300));
  assert.deepEqual(newTrackSpan(-5, 300, 30), span(0, 60));

  // About 0.1 s minimum.
  assert.equal(minSpanFrames(30), 3);
  assert.equal(minSpanFrames(8), 1);

  // Draw: either direction, clamped, appended; too short draws nothing.
  const base = [span(0, 30)];
  assert.deepEqual(drawSpan(base, 60, 90, 300, 3), [span(0, 30), span(60, 90)]);
  assert.deepEqual(drawSpan(base, 90, 60, 300, 3), [span(0, 30), span(60, 90)]);
  assert.deepEqual(drawSpan(base, -20, 40, 300, 3), [span(0, 30), span(0, 40)]);
  assert.deepEqual(drawSpan(base, 280, 400, 300, 3), [span(0, 30), span(280, 300)]);
  assert.equal(drawSpan(base, 60, 62, 300, 3), base);
  assert.equal(drawSpan(base, 60, 60, 300, 3), base);
  assert.deepEqual(base, [span(0, 30)]);

  // Move keeps the length and stays inside [0, limit].
  assert.deepEqual(dragSpan("move", span(60, 90), 20, 300, 3), span(80, 110));
  assert.deepEqual(dragSpan("move", span(60, 90), -100, 300, 3), span(0, 30));
  assert.deepEqual(dragSpan("move", span(60, 90), 500, 300, 3), span(270, 300));

  // Resize keeps the other edge and the minimum length.
  assert.deepEqual(dragSpan("resize-left", span(60, 90), -10, 300, 3), span(50, 90));
  assert.deepEqual(dragSpan("resize-left", span(60, 90), -100, 300, 3), span(0, 90));
  assert.deepEqual(dragSpan("resize-left", span(60, 90), 100, 300, 3), span(87, 90));
  assert.deepEqual(dragSpan("resize-right", span(60, 90), 10, 300, 3), span(60, 100));
  assert.deepEqual(dragSpan("resize-right", span(60, 90), -100, 300, 3), span(60, 63));
  assert.deepEqual(dragSpan("resize-right", span(60, 90), 500, 300, 3), span(60, 300));
  // The timeline's event boxes use the one-frame minimum.
  assert.deepEqual(dragSpan("resize-left", span(60, 90), 100, 300), span(89, 90));

  // Remove and order.
  assert.deepEqual(removeSpan([span(0, 3), span(5, 9), span(20, 30)], 1), [span(0, 3), span(20, 30)]);
  assert.deepEqual(sortSpans([span(50, 60), span(0, 30), span(0, 10)]), [span(0, 10), span(0, 30), span(50, 60)]);
  assert.equal(formatSpan(span(69, 123), 30), "2.3–4.1 s");
});
