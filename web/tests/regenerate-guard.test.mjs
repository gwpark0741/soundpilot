import assert from "node:assert/strict";
import test from "node:test";
import { createServer } from "vite";

test("regenerateBlockedReason blocks unsaved edits and running jobs", async (context) => {
  const server = await createServer({
    appType: "custom",
    optimizeDeps: { noDiscovery: true },
    server: { middlewareMode: true },
  });
  context.after(() => server.close());
  const { regenerateBlockedReason } = await server.ssrLoadModule(
    "/src/components/VideoEditor.tsx",
  );
  assert.equal(
    regenerateBlockedReason(true, "complete"),
    "Save Changes first. Regenerating now would discard your other unsaved edits.",
  );
  assert.equal(
    regenerateBlockedReason(false, "running"),
    "Wait for the current run to finish.",
  );
  assert.equal(regenerateBlockedReason(false, "complete"), null);
});
