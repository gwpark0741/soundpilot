import assert from "node:assert/strict";
import test from "node:test";
import { createServer } from "vite";

test("trackWindowNeedsFetch: edges clamped at 0/maxFrame count as covered", async (context) => {
  const server = await createServer({
    appType: "custom",
    optimizeDeps: { noDiscovery: true },
    server: { middlewareMode: true },
  });
  context.after(() => server.close());
  const { trackWindowNeedsFetch } = await server.ssrLoadModule("/src/lib/trackWindow.ts");

  const maxFrame = 899;
  const need = (window, frame, assetVersion = 1) =>
    trackWindowNeedsFetch({ window, frame, maxFrame, assetVersion });
  const win = (start_frame, end_frame, assetVersion = 1) => ({ start_frame, end_frame, assetVersion });

  assert.equal(need(null, 0), true, "nothing fetched yet");
  // Frame 0: window [0, 300] is exactly what a fetch returns; it must not be refetched.
  assert.equal(need(win(0, 300), 0), false);
  assert.equal(need(win(0, 300), 30), false);
  assert.equal(need(win(0, 300), 250), true, "near the window's open end: slide it");
  // Near the end of the video: window [600, 899].
  assert.equal(need(win(600, 899), 899), false);
  assert.equal(need(win(600, 899), 880), false);
  assert.equal(need(win(600, 899), 620), true, "near the window's start");
  // Mid-video: window [200, 800].
  assert.equal(need(win(200, 800), 500), false);
  assert.equal(need(win(200, 800), 250), true);
  assert.equal(need(win(200, 800), 760), true);
  // Short video: the window covers everything.
  assert.equal(trackWindowNeedsFetch({ window: win(0, 40), frame: 20, maxFrame: 40, assetVersion: 1 }), false);
  // Asset version change refetches, even at frame 0.
  assert.equal(need(win(0, 300, 1), 0, 2), true);
});
