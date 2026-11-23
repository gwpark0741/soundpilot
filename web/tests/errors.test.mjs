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
  const { errorDetail, failure, trackFailureText } = await server.ssrLoadModule("/src/lib/errors.ts");
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

  const next = "Check the tools server, then press Regenerate.";
  assert.equal(
    trackFailureText("Audyssey tools server unreachable at http://127.0.0.1:8700: [Errno 111] Connection refused"),
    `Audyssey tools server unreachable. ${next}`,
  );
  assert.equal(
    trackFailureText("Audyssey /generate failed (500): boom"),
    `Audyssey /generate failed (500): boom. ${next}`,
  );
  // Not a tools-server failure: the detail alone, no tools-server hint.
  assert.equal(trackFailureText("원본 오디오가 없어 분리할 수 없습니다. 생성으로 바꾸세요."), "원본 오디오가 없어 분리할 수 없습니다. 생성으로 바꾸세요.");
  assert.equal(trackFailureText("boom"), "boom.");
  assert.equal(trackFailureText(null), `Generation failed. ${next}`);
  assert.equal(trackFailureText("x".repeat(500)), `${"x".repeat(160)}…`);
});
