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
  assert.match(m, />Re-separate</);
  assert.doesNotMatch(m, /Generate instead|Generates with/);
  assert.match(m, /Heavy boots on snow/);
  assert.match(m, /role="switch"/);
  assert.match(m, /aria-checked="false"/);
  assert.match(m, /Include rest of original audio/);

  // Model select: current model preselected; TTS only for speech, Separate only with original audio.
  const options = (m) => [...m.matchAll(/<option value="(\w+)"( selected="")?>([^<]*)</g)].map((x) => [x[1], x[3], !!x[2]]);
  assert.deepEqual(options(html({})), [["selsync", "SelVA w/ SelSync", false], ["t2a", "ElevenLabs", false], ["separate", "Separate", true]]);
  assert.deepEqual(options(html({ track: track({ generation_model: "selsync" }) })).map((o) => o[0]), ["selsync", "t2a", "separate"]);
  assert.deepEqual(options(html({ track: track({ generation_model: "selsync" }), hasAudio: false })).map((o) => o[0]), ["selsync", "t2a"]);
  const speech = options(html({ track: track({ generation_model: "tts", track_type: "speech" }) }));
  assert.deepEqual(speech.map((o) => o[0]), ["selsync", "t2a", "tts", "separate"]);
  assert.deepEqual(speech.find((o) => o[2]), ["tts", "ElevenLabs TTS", true]);

  // selsync track, audio video: Regenerate + Re-separate
  m = html({
    track: track({ generation_model: "selsync" }),
    artifact: { origin: "generated", seed: 42 },
  });
  assert.match(m, /SelVA w\/ SelSync/);
  assert.match(m, /Seed 42/);
  assert.match(m, />Regenerate track</);
  assert.doesNotMatch(m, /Re-separate|Generate instead/);

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
  m = html({
    status: {
      state: "failed",
      error: "SelSync returned silence in the event spans. Regenerate to try another seed or switch the model.",
    },
  });
  assert.match(m, /SelSync returned silence/);
  assert.doesNotMatch(m, /Previous audio kept\./);
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
  assert.equal((m.match(/<button[^>]*disabled/g) ?? []).length >= 1, true);

  // event-level track: track Regenerate (+ Re-separate with audio), editable per-event descriptions, no track description
  const events = [{ sound_event_id: "e1", sound_track_id: "t1", start_frame_index: 0, end_frame_index: 9, description: "Hello", spoken_text: "Hi there" }];
  m = html({ track: track({ generation_model: "tts", track_type: "speech", label: "Voice" }), events });
  assert.match(m, /Hi there/);
  assert.match(m, /Hello/);
  assert.equal((m.match(/Description<\/label>/g) ?? []).length, 1);  // the event's, not the track's
  assert.equal((m.match(/>Regenerate track</g) ?? []).length, 1);
  assert.equal((m.match(/>Regenerate this span</g) ?? []).length, 1);
  assert.doesNotMatch(m, /Re-separate/);
  m = html({ track: track({ generation_model: "t2a" }), events, hasAudio: false });
  assert.equal((m.match(/>Regenerate this span</g) ?? []).length, 1);
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
  assert.match(m, /disabled=""[^>]*>Regenerate this span<\/button>/);  // the event row
  assert.doesNotMatch(m.replace(/<button[^>]*disabled[^>]*>Regenerate this span<\/button>/g, ""), /disabled=""[^>]*>Regenerate with new spans/);
  assert.match(m, />Discard edits</);
  m = html({ pendingSpans: true });  // separated track: its own method is the labelled one
  assert.match(m, />Regenerate with new spans</);
  assert.doesNotMatch(m, /Span edits are pending/);  // track-level tracks have no event rows to block
  // edits of another track only: nothing is blocked, Discard stays available
  m = html({ track: track({ generation_model: "selsync" }), hasUnsavedEdits: true, onDiscardEdits() {} });
  assert.match(m, />Regenerate track</);
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

  // Trim to spans / Undo trim only for tracks with audio, by state.
  const trim = { onTrim() {}, onUndoTrim() {} };
  m = html({ ...trim });
  assert.match(m, />Trim to spans</);
  assert.doesNotMatch(m, /Undo trim/);
  assert.doesNotMatch(html({}), /Trim to spans/);  // no handler, no button
  assert.doesNotMatch(html({ ...trim, artifact: undefined }), /Trim to spans/);  // no audio yet
  m = html({ ...trim, pendingSpans: true });  // pending edits: both ways to apply them
  assert.match(m, />Regenerate with new spans</);
  assert.match(m, />Trim to spans</);
  m = html({ ...trim, track: track({ clip_segments: [[0, 1]] }) });  // trimmed: Undo instead
  assert.match(m, />Undo trim</);
  assert.doesNotMatch(m, /Trim to spans/);
  m = html({ ...trim, track: track({ clip_segments: [[0, 1]] }), pendingSpans: true });
  assert.match(m, />Trim to spans</);
  assert.match(m, />Undo trim</);
  // blocked like the other track actions, with the reason attached
  m = html({ ...trim, blockedReason: "Generation is running. Actions are unavailable until it finishes." });
  assert.match(m, /aria-describedby="[^"]+"[^>]*disabled=""[^>]*>Trim to spans<|disabled=""[^>]*aria-describedby="[^"]+"[^>]*>Trim to spans</);

  // Model-dependent UI: the select is local state, so the picked model is passed as initialModel here.
  const evs = [
    { sound_event_id: "e2", sound_track_id: "t1", start_frame_index: 50, end_frame_index: 60, description: "Later", spoken_text: null },
    { sound_event_id: "e1", sound_track_id: "t1", start_frame_index: 5, end_frame_index: 20, description: "Earliest", spoken_text: null },
  ];
  const { mainLabel, initialDescription } = await server.ssrLoadModule("/src/components/SelectedTrackPanel.tsx");
  assert.equal(mainLabel("selsync", "selsync", false), "Regenerate track");
  assert.equal(mainLabel("t2a", "t2a", true), "Regenerate with new spans");
  assert.equal(mainLabel("separate", "separate", false), "Re-separate");
  assert.equal(mainLabel("separate", "separate", true), "Regenerate with new spans");
  assert.equal(mainLabel("t2a", "selsync", false), "Regenerate with SelVA w/ SelSync");
  assert.equal(mainLabel("selsync", "tts", false), "Regenerate with ElevenLabs TTS");
  assert.equal(mainLabel("t2a", "separate", false), "Separate");
  assert.equal(initialDescription(track({ generation_model: "t2a", description: "" }), undefined, evs), "Earliest");
  assert.equal(initialDescription(track({ generation_model: "t2a", description: "Own" }), undefined, evs), "Own");
  assert.equal(initialDescription(track({ generation_model: "selsync", description: "" }), undefined, evs), "");

  // event-level track, SelSync picked: no span rows, track description from the earliest span, timing note
  const ev = { track: track({ generation_model: "t2a", description: "" }), artifact: undefined, events: evs };
  m = html({ ...ev, initialModel: "selsync" });
  assert.match(m, />Regenerate with SelVA w\/ SelSync</);
  assert.doesNotMatch(m, /Regenerate this span/);
  assert.match(m, /Track description<\/label>/);
  assert.match(m, />Earliest<\/textarea>/);
  assert.match(m, /Spans are used as timing only; span descriptions are not used\./);
  // SelSync track, ElevenLabs picked: span rows with their descriptions, disabled, with the reason
  m = html({ track: track({ generation_model: "selsync" }), events: evs, initialModel: "t2a" });
  assert.match(m, />Regenerate with ElevenLabs</);
  assert.equal((m.match(/disabled=""[^>]*>Regenerate this span</g) ?? []).length, 2);
  assert.match(m, /Later/);
  assert.match(m, /Per-span regeneration uses the current model/);
  assert.doesNotMatch(m, /Spans are used as timing only/);
  // same model selected: span rows are enabled
  m = html({ track: track({ generation_model: "t2a" }), events: evs });
  assert.equal((m.match(/disabled=""[^>]*>Regenerate this span</g) ?? []).length, 0);
  // Separate picked on a generated track
  assert.match(html({ track: track({ generation_model: "selsync" }), initialModel: "separate" }), />Separate<\/button>/);
});
