import assert from "node:assert/strict";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

const track = { sound_track_id: "t1", track_type: "sfx", label: "Door", generation_model: "selsync" };
const audio = {
  sound_track_id: "t1",
  track_label: "Door",
  track_type: "sfx",
  path: "t1.wav",
  duration_sec: 4,
  event_count: 1,
  waveform_peaks: Array.from({ length: 40 }, () => 0.5),
};

test("timeline edit mode, add track and delete track controls", async (context) => {
  const server = await createServer({
    appType: "custom",
    optimizeDeps: { noDiscovery: true },
    server: { middlewareMode: true },
  });
  context.after(() => server.close());
  const { default: Timeline } = await server.ssrLoadModule("/src/components/Timeline.tsx");
  const { default: Panel } = await server.ssrLoadModule("/src/components/SelectedTrackPanel.tsx");
  const { default: NewTrackPanel } = await server.ssrLoadModule("/src/components/NewTrackPanel.tsx");
  const { default: DeleteTrackDialog } = await server.ssrLoadModule("/src/components/DeleteTrackDialog.tsx");

  const timeline = (over = {}) =>
    renderToStaticMarkup(
      React.createElement(Timeline, {
        rows: [
          { lane: "[sfx] Door", label: "Door event", start_frame: 0, end_frame: 30, kind: "sfx", sound_event_id: "e1", sound_track_id: "t1" },
        ],
        soundTracks: [{ ...track, clip_start_sec: 1, clip_end_sec: 2, clip_offset_sec: 1 }],
        audioTracks: [audio],
        frame: 0,
        frameCount: 120,
        onSelectFrame() {},
        selectedTrackId: "t1",
        onSelectTrack() {},
        soloTrackId: null,
        onToggleSolo() {},
        appRunning: false,
        ...over,
      }),
    );

  // Default "Edit spans": event boxes stay, the background waveform is the placed clip (2 s..3 s of 4 s).
  let html = timeline({ onEditClip() {}, onAddTrack() {} });
  assert.match(html, /role="radio"[^>]*aria-checked="true"[^>]*>Edit spans|aria-checked="true"[^>]*role="radio"[^>]*>Edit spans/);
  assert.match(html, /left:50%;width:25%/);
  assert.ok(html.includes('class="ev"') && !/class="clip[ "]/.test(html));
  assert.match(html, /<button[^>]*>Add track<\/button>/);

  // Blocked: "Edit audio" and "Add track" are disabled with the reason as tooltip.
  html = timeline({ editBlockedReason: "Generation is running.", trackBlockedReason: "Generation is running.", onAddTrack() {} });
  assert.match(html, /disabled=""[^>]*title="Generation is running\."[^>]*>Edit audio/);
  assert.match(html, /disabled=""[^>]*title="Generation is running\."[^>]*>Add track/);
  // Pending span edits block Edit audio but not Add track.
  html = timeline({ editBlockedReason: "Unsaved timeline edits.", onAddTrack() {} });
  assert.match(html, /disabled=""[^>]*title="Unsaved timeline edits\."[^>]*>Edit audio/);
  assert.match(html, /<button class="btn sm" type="button">Add track/);
  assert.ok(!timeline().includes(">Add track<"), "no Add track without a handler");

  // Delete track: only with a handler, disabled while blocked.
  const panelProps = {
    track,
    artifact: undefined,
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
  const panel = (over) => renderToStaticMarkup(React.createElement(Panel, { ...panelProps, ...over }));
  assert.ok(!panel({}).includes("Delete track"));
  assert.match(panel({ onDeleteTrack() {} }), /<button[^>]*class="btn sm danger"[^>]*>Delete track<\/button>/);
  assert.match(panel({ onDeleteTrack() {}, blockedReason: "Busy." }), /disabled=""[^>]*>Delete track|class="btn sm danger"[^>]*disabled=""/);

  // Dialogs.
  const del = renderToStaticMarkup(
    React.createElement(DeleteTrackDialog, { open: false, label: "Door", onClose() {}, onConfirm() {} }),
  );
  assert.match(del, /removed with its events and audio\. This cannot be undone\./);
  assert.match(del, /Cancel/);
  assert.match(del, /Delete<\/button>/);

  // Draft lane: only while drafting, below the sound lanes, one box per span.
  assert.ok(!timeline().includes("New track"));
  html = timeline({ onAddTrack() {}, draftSpans: [{ start_frame: 30, end_frame: 90 }, { start_frame: 100, end_frame: 110 }], onDraftSpansChange() {} });
  assert.match(html, /<div class="lane draft">.*New track/);
  assert.ok(html.indexOf("Door event") < html.indexOf("New track spans"), "below the last sound lane");
  assert.equal((html.match(/New track span \d/g) ?? []).length, 2);
  assert.match(html, /left:25%;width:50%/);
  assert.match(html, /double-click to remove/);

  // New track panel: fields, spans with remove buttons, hint, Cancel / Create.
  const panelNew = (over) =>
    renderToStaticMarkup(
      React.createElement(NewTrackPanel, {
        spans: [{ start_frame: 69, end_frame: 123 }],
        fps: 30,
        hasAudio: true,
        blockedReason: null,
        onRemoveSpan() {},
        onCancel() {},
        onCreate: async () => {},
        ...over,
      }),
    );
  let np = panelNew({});
  assert.match(np, /Name/);
  assert.match(np, /Description/);
  assert.match(np, /Kokoro \(speech\)/);
  assert.match(np, /2\.3–4\.1 s/);
  assert.match(np, /aria-label="Remove span 2\.3–4\.1 s"/);
  assert.match(np, /Drag on the New track lane to add a span\. Drag a span to move it, or its edges to resize\./);
  assert.match(np, />Cancel<\/button>/);
  assert.ok(!np.includes("Spoken text"), "spoken text only for Kokoro");
  assert.match(np, /<button[^>]*disabled=""[^>]*>Create and generate/, "empty name and description");
  assert.match(panelNew({ hasAudio: false }), /<option value="separate" disabled=""/);
  assert.match(panelNew({ hasAudio: false }), /no original audio/);
  assert.match(panelNew({ spans: [] }), /<button[^>]*disabled=""[^>]*>Create and generate/);
  assert.match(panelNew({ blockedReason: "Generation is running." }), /Generation is running\./);
});
