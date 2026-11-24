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
    "Edit audio is off while span edits are pending. Apply them with Regenerate with new spans or Trim to spans on the edited track, or discard them.",
  );
  assert.equal(
    regenerateBlockedReason(false, "running"),
    "Generation is running. Actions are unavailable until it finishes.",
  );
  assert.equal(
    regenerateBlockedReason(false, "running", "Generating audio 2/4"),
    "Generating 2 of 4. Actions are unavailable until it finishes.",
  );
  assert.equal(
    regenerateBlockedReason(false, "running", "track 3 of 5"),
    "Generating 3 of 5. Actions are unavailable until it finishes.",
  );
  assert.equal(regenerateBlockedReason(false, "complete"), null);
});
