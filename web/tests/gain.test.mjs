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

test("track volume slider and gain helpers", async (context) => {
  const server = await createServer({
    appType: "custom",
    optimizeDeps: { noDiscovery: true },
    server: { middlewareMode: true },
  });
  context.after(() => server.close());
  const g = await server.ssrLoadModule("/src/lib/gain.ts");
  const { default: Timeline } = await server.ssrLoadModule("/src/components/Timeline.tsx");

  // Value -> volume: gain 0..2 is 0..200 %, the media element is capped at 1.
  assert.equal(g.gainToPercent(undefined), 100);
  assert.equal(g.gainToPercent(1.35), 135);
  assert.equal(g.percentToGain(250), 2);
  assert.equal(g.percentToGain(-5), 0);
  assert.equal(g.percentToGain(50), 0.5);
  assert.equal(g.gainToElementVolume(undefined), 1);
  assert.equal(g.gainToElementVolume(0.4), 0.4);
  assert.equal(g.gainToElementVolume(2), 1);
  assert.equal(g.gainToElementVolume(-1), 0);

  const timeline = (over = {}, soundTrack = track, audioTracks = [audio]) =>
    renderToStaticMarkup(
      React.createElement(Timeline, {
        rows: [
          { lane: "[sfx] Door", label: "Door event", start_frame: 0, end_frame: 30, kind: "sfx", sound_event_id: "e1", sound_track_id: "t1" },
        ],
        soundTracks: [soundTrack],
        audioTracks,
        frame: 0,
        frameCount: 120,
        onSelectFrame() {},
        selectedTrackId: "t1",
        onSelectTrack() {},
        soloTrackId: null,
        onToggleSolo() {},
        appRunning: false,
        onSetTrackGain() {},
        ...over,
      }),
    );
  const slider = (html) => html.match(/<input[^>]*aria-label="Door volume"[^>]*>/)?.[0] ?? "";

  let html = timeline({}, { ...track, gain: 1.35 });
  assert.match(slider(html), /max="200"/);
  assert.match(slider(html), /min="0"/);
  assert.match(slider(html), /step="5"/);
  assert.match(slider(html), /type="range"/);
  assert.match(slider(html), /value="135"/);
  assert.ok(!/disabled/.test(slider(html)));
  assert.match(html, /<output>135%<\/output>/);
  // No stored gain (old project) reads 100 %.
  assert.match(slider(timeline()), /value="100"/);
  // Disabled while the pipeline runs, without audio, or without a handler; the same in Edit audio mode markup.
  assert.match(slider(timeline({ appRunning: true })), /disabled=""/);
  assert.match(slider(timeline({}, track, [])), /disabled=""/);
  assert.match(slider(timeline({ onSetTrackGain: undefined })), /disabled=""/);
  // Play and Solo stay next to it.
  assert.match(html, /aria-label="Play Door"/);
  assert.match(html, /aria-label="Solo Door"/);
});
