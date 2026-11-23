import assert from "node:assert/strict";
import test from "node:test";
import { createServer } from "vite";

test("summarizeScenes groups objects and visual events by scene", async (context) => {
  const server = await createServer({
    appType: "custom",
    optimizeDeps: { noDiscovery: true },
    server: { middlewareMode: true },
  });
  context.after(() => server.close());
  const { summarizeScenes } = await server.ssrLoadModule("/src/lib/analysis.ts");

  const rows = [
    { lane: "scenes", label: "scene 1", start_frame: 60, end_frame: 120, kind: "scene" },
    { lane: "scenes", label: "scene 0", start_frame: 0, end_frame: 60, kind: "scene" },
    { lane: "tracking", label: "samurai", scene_track_id: "t1", start_frame: 0, end_frame: 59, kind: "track" },
    { lane: "tracking", label: "samurai", scene_track_id: "t2", start_frame: 5, end_frame: 50, kind: "track" },
    { lane: "tracking", label: "sword", scene_track_id: "t3", start_frame: 61, end_frame: 120, kind: "track" },
    { lane: "visual: sword #1", label: "contact: blade hits: armor", start_frame: 90, end_frame: 92, kind: "contact" },
    { lane: "visual: sword #1", label: "appearance: enters", start_frame: 61, end_frame: 62, kind: "appearance" },
    { lane: "[sfx] Clash", label: "clash", start_frame: 90, end_frame: 92, kind: "sfx" },
  ];
  const scenes = summarizeScenes(rows, { t1: "#f00", t3: "#0f0" });

  assert.deepEqual(scenes.map((s) => [s.index, s.start_frame]), [[0, 0], [1, 60]]);
  assert.deepEqual(scenes[0].objects, [{ label: "samurai", color: "#f00" }]);
  assert.deepEqual(scenes[0].events, []);
  assert.deepEqual(scenes[1].objects, [{ label: "sword", color: "#0f0" }]);
  assert.deepEqual(
    scenes[1].events.map((e) => [e.type, e.object, e.description, e.start_frame]),
    [["appearance", "sword #1", "enters", 61], ["contact", "sword #1", "blade hits: armor", 90]],
  );
});
