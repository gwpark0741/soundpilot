import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

const base = {
  status: "idle",
  stage: null,
  error: null,
  version: 1,
  asset_version: 1,
  updated_at: "",
  video: { video_id: "v", frame_count: 213, fps: 30, duration_sec: 7.1, width: 1280, height: 720 },
  timeline_rows: [],
  audio_tracks: [],
  audio_events: [],
  track_targets: {},
};

test("TopBar shows the input badge, status line and a single primary button", async (context) => {
  const server = await createServer({
    appType: "custom",
    optimizeDeps: { noDiscovery: true },
    server: { middlewareMode: true },
  });
  context.after(() => server.close());
  const { default: TopBar } = await server.ssrLoadModule("/src/components/TopBar.tsx");
  const render = (props) =>
    renderToStaticMarkup(
      createElement(TopBar, { hasAudio: true, state: base, canGenerate: true, onOpen() {}, onGenerate() {}, ...props }),
    );
  const primaries = (html) => (html.match(/class="btn primary"/g) ?? []).length;

  const idle = render({});
  assert.match(idle, /Original audio/);
  assert.match(idle, /Open video/);
  assert.match(idle, /role="status"/);
  assert.match(idle, /<button[^>]*class="btn primary"[^>]*>Generate audio</);
  assert.equal(primaries(idle), 1);

  assert.match(render({ hasAudio: false }), /Silent video/);
  assert.doesNotMatch(render({ hasAudio: null }), /Original audio|Silent video/);

  const running = render({ state: { ...base, status: "running", stage: "generating audio 2/4" } });
  assert.match(running, /<button[^>]*disabled[^>]*>Generating…</);
  assert.match(running, /Generating audio 2 of 4/);
  assert.match(running, /role="progressbar"[^>]*aria-valuenow="2"[^>]*aria-valuemax="4"|aria-valuemax="4"[^>]*aria-valuenow="2"/);
  assert.match(running, /width:50%/);
  assert.equal(primaries(running), 1);

  const spinning = render({ state: { ...base, status: "running", stage: "analyze scenes" } });
  assert.match(spinning, /role="progressbar"/);
  assert.doesNotMatch(spinning, /aria-valuenow/);

  const done = render({ state: { ...base, status: "complete", audio_tracks: [{}, {}] } });
  assert.match(done, /Done\. 2 tracks ready to play\./);

  const failed = render({ state: { ...base, status: "failed", error: "Tools server unreachable." } });
  assert.match(failed, /Tools server unreachable\./);

  assert.match(render({ canGenerate: false }), /<button[^>]*disabled[^>]*>Generate audio</);
});
