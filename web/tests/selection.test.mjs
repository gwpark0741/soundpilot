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
  for (const model of ["selsync", "t2a", "tts"]) {
    assert.deepEqual(s.trackActions(model, false, "sfx"), ["regenerate"]);
    assert.deepEqual(s.trackActions(model, true, "sfx"), ["regenerate", "re-separate"]);
  }
  assert.deepEqual(s.trackActions("tts", true, "speech"), ["regenerate", "re-separate"]);
  const tr = (id, label, frames) => ({ scene_track_id: id, label, points: frames.map((f) => ({ frame_index: f })) });
  const win = [tr("a", "red samurai sword", [1]), tr("b", "sword", [5])];
  assert.equal(s.fallbackMatchLabel(win, 5, ["a", "b"], ["b"]), "sword");
  assert.equal(s.fallbackMatchLabel(win, 1, ["a", "b"], ["b"]), null);
  assert.equal(s.fallbackMatchLabel(win, 9, ["a", "b"], ["b"]), null);
  assert.equal(s.actionMethod("re-separate"), "separate");
  assert.equal(s.actionMethod("generate-instead"), "generate");
  assert.equal(s.actionMethod("regenerate"), "generate");

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
  assert.equal(s.objectColor(8), "#4cc9f0", "palette cycles");
  for (let i = 0; i < 40; i++) assert.notEqual(s.objectColor(i), s.NONE_COLOR);

  assert.equal(s.methodLabel("separate"), "Separated");
  assert.equal(s.methodLabel("selsync"), "SelVA w/ SelSync");
  assert.equal(s.methodLabel("tts"), "TTS");
  assert.equal(s.methodLabel("t2a"), "ElevenLabs");
  assert.equal(s.methodLabel("v2a"), "Hunyuan");

  // generationLabel mirrors the backend's generate-mode routing (agent_model unless a rule overrides)
  const t = (track_type, agent_model, generation_model = "separate") => ({ track_type, agent_model, generation_model });
  assert.equal(s.generationLabel(t("speech", "v2a")), "TTS");
  assert.equal(s.generationLabel(t("ambience", "v2a")), "ElevenLabs");
  assert.equal(s.generationLabel(t("sfx", "v2a"), "scene_global"), "ElevenLabs");
  assert.equal(s.generationLabel(t("sfx", "v2a"), "offscreen_unknown"), "ElevenLabs");
  assert.equal(s.generationLabel(t("sfx", "v2a"), "visible_object"), "SelVA w/ SelSync");
  assert.equal(s.generationLabel(t("sfx", "t2a")), "ElevenLabs");
  assert.equal(s.generationLabel(t("sfx", "tts")), "TTS");
  // old assets without agent_model: filled from generation_model, else from the track type
  assert.equal(s.generationLabel(t("sfx", null, "v2a")), "SelVA w/ SelSync");
  assert.equal(s.generationLabel(t("sfx", undefined, "t2a")), "ElevenLabs");
  assert.equal(s.generationLabel(t("sfx", null, "separate")), "SelVA w/ SelSync");
});

test("reconcileSelection resets a stale id; soloToggleAction restarts playback only when playing", async (context) => {
  const server = await createServer({
    appType: "custom",
    optimizeDeps: { noDiscovery: true },
    server: { middlewareMode: true },
  });
  context.after(() => server.close());
  const { reconcileSelection, soloToggleAction } = await server.ssrLoadModule("/src/lib/selection.ts");

  assert.equal(reconcileSelection(null, ["a", "b"]), "a");
  assert.equal(reconcileSelection("b", ["a", "b"]), "b");
  assert.equal(reconcileSelection("old", ["a", "b"]), "a", "stale id from the previous video");
  assert.equal(reconcileSelection("old", []), "old", "no tracks yet: nothing to select");

  const idle = { previewPlaying: false, videoPlaying: false };
  assert.equal(soloToggleAction({ ...idle, soloTrackId: "t1" }), null);
  assert.equal(soloToggleAction({ ...idle, soloTrackId: null }), null);
  assert.equal(soloToggleAction({ previewPlaying: false, videoPlaying: true, soloTrackId: "t1" }), "play-solo");
  assert.equal(soloToggleAction({ previewPlaying: true, videoPlaying: true, soloTrackId: "t1" }), "play-solo");
  assert.equal(soloToggleAction({ previewPlaying: true, videoPlaying: true, soloTrackId: null }), "play-mix");
  assert.equal(soloToggleAction({ previewPlaying: false, videoPlaying: true, soloTrackId: null }), null);
});
