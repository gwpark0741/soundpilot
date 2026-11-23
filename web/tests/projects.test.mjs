import assert from "node:assert/strict";
import test from "node:test";
import { createServer } from "vite";

test("projectMeta lists duration, audio method and track count; unfinished projects say so", async (context) => {
  const server = await createServer({
    appType: "custom",
    optimizeDeps: { noDiscovery: true },
    server: { middlewareMode: true },
  });
  context.after(() => server.close());
  const { projectMeta } = await server.ssrLoadModule("/src/lib/projects.ts");
  const base = {
    id: "p", name: "clip.mp4", created: "2026-11-23T01:00:00+00:00", updated: "2026-11-23T01:00:00+00:00",
    duration_sec: 65, audio_mode: "separate", scenes: 2, objects: 3, tracks: 1, has_preview: true, status: "complete",
  };
  assert.ok(projectMeta(base).endsWith("1:05 · Separate · 1 track"));
  assert.ok(projectMeta({ ...base, tracks: 4, audio_mode: "generate" }).endsWith("Generate · 4 tracks"));
  assert.ok(projectMeta({ ...base, status: "failed" }).endsWith("Analysis did not finish"));
  assert.ok(projectMeta({ ...base, status: "running" }).endsWith("Analyzing"));
});
