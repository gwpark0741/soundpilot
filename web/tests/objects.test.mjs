import assert from "node:assert/strict";
import test from "node:test";
import { createServer } from "vite";

test("object colours: keyed by visual object, cycle, masked never gray", async (context) => {
  const server = await createServer({ appType: "custom", optimizeDeps: { noDiscovery: true }, server: { middlewareMode: true } });
  context.after(() => server.close());
  const { buildObjects } = await server.ssrLoadModule("/src/lib/objects.ts");
  const { NONE_COLOR, objectColor } = await server.ssrLoadModule("/src/lib/selection.ts");

  const labels = ["A", "b", "C", "D", "E", "F", "G", "H", "I", "J"];
  const rows = labels.map((label, i) => ({
    kind: "track", lane: "tracking", label, scene_track_id: `t${i}`, start_frame: i, end_frame: i + 5,
  }));
  rows.push({ kind: "track", lane: "tracking", label: " B ", scene_track_id: "t10", start_frame: 50, end_frame: 60 });
  const asset = {
    visual_identity_layer: { visual_objects: [{ visual_object_id: "v1", label: "Samurai", presences: [{ scene_track_id: "t0" }] }] },
    sound_timeline: { sound_sources: [{ sound_source_id: "s1", label: "x", visual_object_id: "v1" }, { sound_source_id: "s2", label: "j" }] },
  };
  const o = buildObjects(rows, asset);
  assert.equal(o.lanes.length, 10, "t10 shares the lane of b (lowercased/trimmed)");
  assert.equal(o.lanes[1].bars.length, 2);
  assert.equal(o.sceneTrackColors.t10, o.sceneTrackColors.t1);
  assert.equal(o.lanes[8].color, objectColor(8));
  assert.equal(o.lanes[0].label, "Samurai", "visual identity label wins over the tracking label");
  assert.ok(o.lanes.every((l) => l.color !== NONE_COLOR));

  const track = (id, source) => ({ sound_track_id: id, sound_source_id: source });
  assert.equal(o.soundTrackColor(track("a", "s1"), { a: ["t0"] }), o.lanes[0].color, "via visual_object_id");
  assert.equal(o.soundTrackColor(track("b", "s2"), { b: ["t9"] }), o.lanes[9].color, "via source label");
  assert.equal(o.soundTrackColor(track("c", null), { c: ["t3"] }), o.sceneTrackColors.t3, "fallback to scene tracks");
  assert.equal(o.soundTrackColor(track("d", "s1"), { d: [] }), NONE_COLOR, "no mask");
});
