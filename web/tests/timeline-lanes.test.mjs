import assert from "node:assert/strict";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

const soundTracks = [
  { sound_track_id: "t-done", track_type: "sfx", label: "Footsteps", generation_model: "separate" },
  { sound_track_id: "t-fail", track_type: "sfx", label: "Impact", generation_model: "selsync" },
  { sound_track_id: "t-none", track_type: "sfx", label: "Door slam", generation_model: "selsync" },
];

function render(Timeline, overrides = {}) {
  const rows = [
    { lane: "scene", label: "Scene A", start_frame: 0, end_frame: 30, kind: "scene" },
    { lane: "track: player", label: "player", start_frame: 0, end_frame: 30, kind: "track" },
    { lane: "visual: contact", label: "Contact", start_frame: 5, end_frame: 9, kind: "contact" },
    ...soundTracks.map((track, index) => ({
      lane: `[${track.track_type}] ${track.label}`,
      label: `${track.label} event`,
      start_frame: index * 10,
      end_frame: index * 10 + 5,
      kind: track.track_type,
      sound_event_id: `event-${index}`,
      sound_track_id: track.sound_track_id,
      generation_model: track.generation_model,
    })),
  ];
  const audioTracks = soundTracks.slice(0, 2).map((track) => ({
    sound_track_id: track.sound_track_id,
    track_label: track.label,
    track_type: track.track_type,
    path: `${track.sound_track_id}.wav`,
    duration_sec: 1,
    event_count: 1,
    waveform_peaks: [0.2, 0.9, 0.4],
  }));
  return renderToStaticMarkup(
    React.createElement(Timeline, {
      rows,
      soundTracks,
      audioTracks,
      audioEvents: [],
      assetVersion: 1,
      frame: 0,
      frameCount: 30,
      onSelectFrame() {},
      selectedTrackId: "t-fail",
      onSelectTrack() {},
      soloTrackId: null,
      onToggleSolo() {},
      trackStatus: { "t-done": { state: "done" }, "t-fail": { state: "failed" } },
      appRunning: false,
      trackColors: { "t-done": "#4cc9f0", "t-fail": "#ff7ab6", "t-none": "#9aa3b2" },
      ...overrides,
    }),
  );
}

test("timeline renders the track list: sound lanes only, status, selection, Play/Solo", async (context) => {
  const server = await createServer({
    appType: "custom",
    optimizeDeps: { noDiscovery: true },
    server: { middlewareMode: true },
  });
  context.after(() => server.close());
  const { default: Timeline } = await server.ssrLoadModule("/src/components/Timeline.tsx");
  const html = render(Timeline);
  const lanes = html.split(/<div class="lane(?: sel)?"/).slice(1);

  assert.equal(lanes.length, 3, "only the sound tracks get lanes");
  assert.ok(!html.includes("Scene A") && !html.includes("Contact"));
  assert.ok(!html.includes("Scenes") && !html.includes("+ Sound Track"), "no lane toggles or create form");

  const byName = (name) => lanes.find((lane) => lane.includes(name));
  assert.ok(byName("Impact").includes('aria-current="true"'));
  assert.ok(byName("Footsteps").includes('aria-current="false"'));
  assert.ok(byName("Impact").includes("Failed"));
  assert.ok(!byName("Footsteps").includes("Done"), "Done is hidden");
  assert.ok(byName("Footsteps").includes("Separated"));
  assert.ok(byName("Impact").includes("SelVA w/ SelSync"));

  // Play/Solo: enabled with audio, disabled without.
  assert.ok(!/<button[^>]*aria-label="Play Footsteps"[^>]*disabled/.test(byName("Footsteps")));
  const noAudio = byName("Door slam");
  assert.ok(/aria-label="Play Door slam"[^>]*disabled=""|disabled=""[^>]*aria-label="Play Door slam"/.test(noAudio));
  assert.ok(noAudio.includes('title="Generate audio first."'));
  assert.ok(/aria-pressed="false"[^>]*aria-label="Solo Door slam"|aria-label="Solo Door slam"[^>]*aria-pressed="false"/.test(noAudio));
  assert.ok(html.includes('tabindex="-1"'), "event bars are out of the tab order");
});

test("solo state shows aria-pressed true", async (context) => {
  const server = await createServer({
    appType: "custom",
    optimizeDeps: { noDiscovery: true },
    server: { middlewareMode: true },
  });
  context.after(() => server.close());
  const { default: Timeline } = await server.ssrLoadModule("/src/components/Timeline.tsx");
  const html = render(Timeline, { soloTrackId: "t-done" });
  assert.match(html, /aria-pressed="true"[^>]*aria-label="Solo Footsteps"|aria-label="Solo Footsteps"[^>]*aria-pressed="true"/);
});
