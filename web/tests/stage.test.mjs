import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

test("Stage shows the highlight chip and the time readout", async (context) => {
  const server = await createServer({
    appType: "custom",
    optimizeDeps: { noDiscovery: true },
    server: { middlewareMode: true },
  });
  context.after(() => server.close());
  const { default: Stage } = await server.ssrLoadModule("/src/components/Stage.tsx");
  const props = {
    videoSrc: "/api/video",
    video: { video_id: "v", frame_count: 213, fps: 30, duration_sec: 7.1, width: 1280, height: 720 },
    frame: 36,
    isPlaying: false,
    onTogglePlay() {},
    onSelectFrame() {},
    tracks: [],
    highlightSceneTrackIds: ["0:1"],
    highlightColor: "#4cc9f0",
    highlightLabel: "Player Character",
    videoRef: { current: null },
  };
  const html = renderToStaticMarkup(createElement(Stage, props));
  assert.match(html, /Highlighting Player Character/);
  assert.match(html, /1\.20 s \/ 7\.10 s/);

  const none = renderToStaticMarkup(
    createElement(Stage, {
      ...props,
      highlightSceneTrackIds: [],
      highlightColor: "#9aa3b2",
      highlightLabel: "Scene Global Environment has no object mask",
    }),
  );
  assert.match(none, /Scene Global Environment has no object mask/);
  assert.doesNotMatch(none, /Highlighting/);
});
