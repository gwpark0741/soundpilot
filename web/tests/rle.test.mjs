import assert from "node:assert/strict";
import test from "node:test";
import { createServer } from "vite";

// Fixture: samples/sample.video-asset.json, scene 0, scene_track 0, first point (frame 43).
// Expected values computed with v2a_inspect.media_utils.decode_coco_rle.
const RLE = JSON.stringify({"encoding": "coco_rle", "size": [720, 1280], "counts": "lWgb04\\f00N4N002N00>B02N00000000000000000002N0000000002N0000002L202F802L20ljd8"});
const EXPECTED = { width: 1280, height: 720, area: 604, x0: 852, x1: 885, y0: 186, y1: 211 };

test("decodeCocoRle matches the Python decoder", async (context) => {
  const server = await createServer({
    appType: "custom",
    optimizeDeps: { noDiscovery: true },
    server: { middlewareMode: true },
  });
  context.after(() => server.close());
  const { decodeCocoRle } = await server.ssrLoadModule("/src/lib/rle.ts");
  const { width, height, data } = decodeCocoRle(RLE);
  assert.equal(width, EXPECTED.width);
  assert.equal(height, EXPECTED.height);
  assert.equal(data.length, width * height);
  let area = 0, x0 = width, x1 = -1, y0 = height, y1 = -1;
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      if (!data[y * width + x]) continue;
      area++;
      x0 = Math.min(x0, x); x1 = Math.max(x1, x);
      y0 = Math.min(y0, y); y1 = Math.max(y1, y);
    }
  }
  assert.deepEqual({ area, x0, x1, y0, y1 }, { area: EXPECTED.area, x0: EXPECTED.x0, x1: EXPECTED.x1, y0: EXPECTED.y0, y1: EXPECTED.y1 });
});
