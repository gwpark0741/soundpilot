import assert from "node:assert/strict";
import test from "node:test";
import { createServer } from "vite";

test("errorDetail unwraps the backend detail; failure adds what and next", async (context) => {
  const server = await createServer({
    appType: "custom",
    optimizeDeps: { noDiscovery: true },
    server: { middlewareMode: true },
  });
  context.after(() => server.close());
  const { errorDetail, failure } = await server.ssrLoadModule("/src/lib/errors.ts");
  assert.equal(errorDetail(new Error('{"detail":"Pipeline is running"}')), "Pipeline is running");
  assert.equal(errorDetail(new Error('{"detail":[{"msg":"Field required"},{"msg":"Bad file"}]}')), "Field required Bad file");
  assert.equal(errorDetail(new Error("plain text")), "plain text");
  assert.equal(errorDetail("oops"), "oops");
  assert.equal(
    failure("Could not load the project.", new Error('{"detail":"Invalid asset"}'), "Pick another JSON file."),
    "Could not load the project. Invalid asset. Pick another JSON file.",
  );
  const long = failure("Failed.", new Error("x".repeat(500)), "Retry.");
  assert.equal(long, `Failed. ${"x".repeat(160)}… Retry.`);
});
