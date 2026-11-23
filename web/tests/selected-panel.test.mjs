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

  // separated track tells which model "Generate instead" will use
  assert.match(html({ track: track({ agent_model: "v2a" }) }), /Generates with SelVA w\/ SelSync/);
  assert.match(html({ track: track({ agent_model: "v2a" }), sourceType: "scene_global" }), /Generates with ElevenLabs</);
  assert.match(html({ track: track({ agent_model: "tts", track_type: "speech" }) }), /Generates with ElevenLabs TTS/);

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
  m = html({
    status: {
      state: "failed",
      error: "Audyssey tools server unreachable at http://127.0.0.1:8700: [Errno 111] Connection refused",
    },
  });
  assert.match(m, /Failed/);
  assert.match(m, /Audyssey tools server unreachable\. Check the tools server, then press Regenerate\./);
  assert.doesNotMatch(m, /127\.0\.0\.1|Errno/);
  assert.match(m, /Previous audio kept\./);
  m = html({ status: { state: "failed", error: "Audyssey /generate failed (500): boom" } });
  assert.match(m, /Audyssey \/generate failed \(500\): boom\. Check the tools server, then press Regenerate\./);

  // running / blocked
  m = html({
    status: { state: "running" },
    appRunning: true,
    blockedReason: "Generating 2 of 4. Actions are unavailable until it finishes.",
  });
  assert.match(m, /Generating…/);
  assert.match(m, /Generating 2 of 4\. Actions are unavailable until it finishes\./);
  assert.equal((m.match(/<button[^>]*disabled/g) ?? []).length >= 3, true);

  // event-level track: track Regenerate (+ Re-separate with audio), editable per-event descriptions, no track description
  const events = [{ sound_event_id: "e1", sound_track_id: "t1", start_frame_index: 0, end_frame_index: 9, description: "Hello", spoken_text: "Hi there" }];
  m = html({ track: track({ generation_model: "tts", track_type: "speech", label: "Voice" }), events });
  assert.match(m, /Hi there/);
  assert.match(m, /Hello/);
  assert.equal((m.match(/Description<\/label>/g) ?? []).length, 1);  // the event's, not the track's
  assert.ok((m.match(/>Regenerate</g) ?? []).length === 2);  // track + event
  assert.match(m, />Re-separate</);
  m = html({ track: track({ generation_model: "t2a" }), events, hasAudio: false });
  assert.ok((m.match(/>Regenerate</g) ?? []).length === 2);
  assert.doesNotMatch(m, /Re-separate/);
  // track-level tracks keep their own description
  assert.equal((html({ track: track({ generation_model: "selsync" }) }).match(/Description<\/label>/g) ?? []).length, 1);

  // unsaved edits block Regenerate: Discard edits is offered next to the reason, not primary
  const edits = "Span edits are pending. Regenerate the edited tracks or discard the edits first.";
  m = html({ blockedReason: edits, hasUnsavedEdits: true, onDiscardEdits() {} });
  assert.match(m, />Discard edits</);
  assert.doesNotMatch(m, /btn primary/);
  // pending span edits of this track: the track action is labelled for them, event Regenerate is off
  m = html({ track: track({ generation_model: "t2a" }), events: [{ sound_event_id: "e1", description: "Hi", spoken_text: null }], pendingSpans: true, hasUnsavedEdits: true, onDiscardEdits() {} });
  assert.match(m, />Regenerate with new spans</);
  assert.match(m, /Span edits are pending\. Regenerate the track to apply them\./);
  assert.match(m, /disabled=""[^>]*>Regenerate<\/button>/);  // the event row
  assert.doesNotMatch(m.replace(/<button[^>]*disabled[^>]*>Regenerate<\/button>/g, ""), /disabled=""[^>]*>Regenerate with new spans/);
  assert.match(m, />Discard edits</);
  m = html({ pendingSpans: true });  // separated track: its own method is the labelled one
  assert.match(m, />Regenerate with new spans</);
  assert.match(m, />Generate instead</);
  assert.doesNotMatch(m, /Span edits are pending/);  // track-level tracks have no event rows to block
  // edits of another track only: nothing is blocked, Discard stays available
  m = html({ track: track({ generation_model: "selsync" }), hasUnsavedEdits: true, onDiscardEdits() {} });
  assert.match(m, />Regenerate</);
  assert.doesNotMatch(m, /disabled=""/);
  assert.match(m, />Discard edits</);
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
