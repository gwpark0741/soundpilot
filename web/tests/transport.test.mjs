import assert from "node:assert/strict";
import test from "node:test";
import { createServer } from "vite";

// Applies an action to a tiny model of the player: stem preview and video playing flags.
function apply(action, st) {
  if (action === "stop-preview") return { previewPlaying: false, videoPlaying: false };
  if (action === "pause-video") return { ...st, videoPlaying: false };
  if (action === "play-solo") return { previewPlaying: true, videoPlaying: true };
  return { previewPlaying: false, videoPlaying: true };
}

test("main play button: three presses toggle cleanly with Solo on and off", async (context) => {
  const server = await createServer({
    appType: "custom",
    optimizeDeps: { noDiscovery: true },
    server: { middlewareMode: true },
  });
  context.after(() => server.close());
  const { transportAction } = await server.ssrLoadModule("/src/lib/selection.ts");

  for (const [soloTrackId, play] of [["t1", "play-solo"], [null, "play-mix"]]) {
    let st = { previewPlaying: false, videoPlaying: false };
    const seen = [];
    for (let press = 0; press < 3; press += 1) {
      const action = transportAction({ ...st, soloTrackId });
      seen.push(action);
      st = apply(action, st);
    }
    const stop = soloTrackId ? "stop-preview" : "pause-video";
    assert.deepEqual(seen, [play, stop, play], `solo=${soloTrackId}`);
  }
  // Lane Play (stem preview running, no solo) is stopped by the main button, not turned into the mix.
  assert.equal(
    transportAction({ previewPlaying: true, videoPlaying: true, soloTrackId: null }),
    "stop-preview",
  );
});
