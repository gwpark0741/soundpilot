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

test("rasterizeMasks samples the union of masks at reduced size", async (context) => {
  const server = await createServer({
    appType: "custom",
    optimizeDeps: { noDiscovery: true },
    server: { middlewareMode: true },
  });
  context.after(() => server.close());
  const { decodeCocoRle, rasterizeMasks } = await server.ssrLoadModule("/src/lib/rle.ts");
  // identity size == decode
  const full = rasterizeMasks([RLE], 1280, 720);
  assert.deepEqual(full, decodeCocoRle(RLE).data);
  // half size == decode sampled at the (2x, 2y) pixels
  const { data } = decodeCocoRle(RLE);
  const half = rasterizeMasks([RLE], 640, 360);
  let area = 0;
  for (let y = 0; y < 360; y++) {
    for (let x = 0; x < 640; x++) {
      const want = data[(2 * y + 1) * 1280 + 2 * x + 1]; // (t+0.5)*2 floors to 2t+1
      assert.equal(half[y * 640 + x], want);
      area += half[y * 640 + x];
    }
  }
  assert.ok(area > 100 && area < 200, `half-res area ${area}`);
  // union of two masks
  const other = JSON.stringify({ size: [720, 1280], counts: "<<<<<<" }); // small valid runs in the top-left corner
  const both = rasterizeMasks([RLE, other], 640, 360);
  const o = rasterizeMasks([other], 640, 360);
  for (let i = 0; i < both.length; i++) assert.equal(both[i], half[i] | o[i]);
  assert.equal(rasterizeMasks([], 4, 4).length, 16);
});
