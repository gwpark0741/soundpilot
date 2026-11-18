import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

test("SelectedTrackPanel renders per-state markup", async (context) => {
  const server = await createServer({
    appType: "custom",
    optimizeDeps: { noDiscovery: true },
    server: { middlewareMode: true },
  });
  context.after(() => server.close());
  const { default: Panel } = await server.ssrLoadModule("/src/components/SelectedTrackPanel.tsx");

  const track = (over = {}) => ({
    sound_track_id: "t1",
    track_type: "sfx",
    label: "Player Footsteps",
    generation_model: "separate",
    description: "Heavy boots",
    ...over,
  });
  const base = {
    track: track(),
    artifact: { origin: "separated", attempt: 2, description: "Heavy boots on snow" },
    status: { state: "done" },
    appRunning: false,
    blockedReason: null,
    hasAudio: true,
    residualEnabled: false,
    objectColor: "#4cc9f0",
    events: [],
    onRegenerate() {},
    onSetResidual() {},
    onRegenerateEvent() {},
  };
  const html = (over) => renderToStaticMarkup(createElement(Panel, { ...base, ...over }));

  // separated track
  let m = html({});
  assert.match(m, /Player Footsteps/);
  assert.match(m, /Separated/);
  assert.match(m, /Attempt 2/);
  assert.match(m, /Re-separate/);
  assert.match(m, /Generate instead/);
  assert.match(m, /Heavy boots on snow/);
  assert.match(m, /role="switch"/);
  assert.match(m, /aria-checked="false"/);
  assert.match(m, /Include rest of original audio/);

  // selsync track, audio video: Regenerate + Re-separate
  m = html({
    track: track({ generation_model: "selsync" }),
    artifact: { origin: "generated", seed: 42 },
  });
  assert.match(m, /SelVA w\/ SelSync/);
  assert.match(m, /Seed 42/);
  assert.match(m, />Regenerate</);
  assert.match(m, />Re-separate</);
  assert.doesNotMatch(m, /Generate instead/);

  // silent video: no residual switch, Regenerate only
  m = html({ track: track({ generation_model: "selsync" }), hasAudio: false });
  assert.doesNotMatch(m, /role="switch"/);
  assert.doesNotMatch(m, /Re-separate/);

  // failed
  m = html({ status: { state: "failed", error: "Tools server unreachable." } });
  assert.match(m, /Failed/);
  assert.match(m, /Tools server unreachable\./);
  assert.match(m, /Previous audio kept\./);

  // running / blocked
  m = html({
    status: { state: "running" },
    appRunning: true,
    blockedReason: "Generating 2 of 4. Actions are unavailable until it finishes.",
  });
  assert.match(m, /Generating…/);
  assert.match(m, /Generating 2 of 4\. Actions are unavailable until it finishes\./);
  assert.equal((m.match(/<button[^>]*disabled/g) ?? []).length >= 3, true);

  // event-level track lists events with Regenerate
  m = html({
    track: track({ generation_model: "tts", track_type: "speech", label: "Voice" }),
    events: [{ sound_event_id: "e1", sound_track_id: "t1", start_frame_index: 0, end_frame_index: 9, description: "Hello", spoken_text: "Hi there" }],
  });
  assert.match(m, /Hi there/);
  assert.match(m, />Regenerate</);
  assert.doesNotMatch(m, /Re-separate/);

  // unsaved edits block Regenerate: Discard edits is offered next to the reason, not primary
  const edits = "Unsaved timeline edits. Generate audio to apply them or discard.";
  m = html({ blockedReason: edits, hasUnsavedEdits: true, onDiscardEdits() {} });
  assert.match(m, />Discard edits</);
  assert.doesNotMatch(m, /btn primary/);
  // not when the block is a running generation, or when there is nothing to discard
  m = html({ blockedReason: "Generating 2 of 4. Actions are unavailable until it finishes.", appRunning: true, hasUnsavedEdits: false, onDiscardEdits() {} });
  assert.doesNotMatch(m, /Discard edits/);
  m = html({});
  assert.doesNotMatch(m, /Discard edits/);

  // a failed action shows its message in .err under the actions
  m = html({ error: "Could not regenerate the track. Pipeline is running. Press Regenerate to try again." });
  assert.match(m, /<div class="err" role="alert">.*Pipeline is running\./);
  assert.ok(m.indexOf('class="actions"') < m.indexOf('role="alert"'));
  assert.doesNotMatch(html({}), /role="alert"/);
});
