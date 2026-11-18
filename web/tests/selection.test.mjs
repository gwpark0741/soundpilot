import assert from "node:assert/strict";
import test from "node:test";
import { createServer } from "vite";

test("selection helpers", async (context) => {
  const server = await createServer({
    appType: "custom",
    optimizeDeps: { noDiscovery: true },
    server: { middlewareMode: true },
  });
  context.after(() => server.close());
  const s = await server.ssrLoadModule("/src/lib/selection.ts");

  assert.deepEqual(s.trackActions("separate", true, "sfx"), ["re-separate", "generate-instead"]);
  assert.deepEqual(s.trackActions("selsync", false, "sfx"), ["regenerate"]);
  assert.deepEqual(s.trackActions("selsync", true, "sfx"), ["regenerate", "re-separate"]);
  assert.deepEqual(s.trackActions("selsync", true, "speech"), []);
  assert.deepEqual(s.trackActions("tts", false, "speech"), []);
  assert.deepEqual(s.trackActions("t2a", true, "sfx"), []);

  assert.equal(s.statusView(undefined, false).label, null);
  assert.equal(s.statusView({ state: "done" }, false).label, "Done");
  assert.equal(s.statusView({ state: "running" }, true).label, "Generating…");
  assert.equal(s.statusView(undefined, true).label, "Queued");
  assert.equal(s.statusView({ state: "failed", error: "x" }, false).tone, "err");
  assert.equal(s.statusView({ state: "failed", error: "x" }, false).label, "Failed");

  const targets = { a: ["1", "2"] };
  assert.deepEqual(s.highlightFor(null, targets), []);
  assert.deepEqual(s.highlightFor("a", targets), ["1", "2"]);
  assert.deepEqual(s.highlightFor("zzz", targets), []);

  assert.equal(s.objectColor(0), "#4cc9f0");
  assert.equal(s.objectColor(1), "#ff7ab6");
  assert.equal(s.objectColor(-1), "#9aa3b2");
  assert.equal(s.objectColor(99), "#9aa3b2");

  assert.equal(s.methodLabel("separate"), "Separated");
  assert.equal(s.methodLabel("selsync"), "SelVA w/ SelSync");
  assert.equal(s.methodLabel("tts"), "TTS");
  assert.equal(s.methodLabel("t2a"), "ElevenLabs");
  assert.equal(s.methodLabel("v2a"), "Hunyuan");
});
