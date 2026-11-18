// Backend errors arrive as the raw response body, e.g. '{"detail":"Pipeline is running"}'.
export function errorDetail(error: unknown): string {
  const raw = error instanceof Error ? error.message : String(error);
  try {
    const detail = JSON.parse(raw)?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return detail.map((item) => item?.msg ?? String(item)).join(" ");
  } catch {
    // Not JSON: show the text as is.
  }
  return raw;
}

// "What failed" + the server's detail + "what to do next", as one sentence group.
// Long details (e.g. pydantic validation dumps) are cut so the message stays readable.
const MAX_DETAIL = 160;

export function failure(what: string, error: unknown, next: string): string {
  let detail = errorDetail(error).trim();
  if (detail.length > MAX_DETAIL) detail = `${detail.slice(0, MAX_DETAIL).trimEnd()}…`;
  return [what, detail && (/[.!?…]$/.test(detail) ? detail : `${detail}.`), next].filter(Boolean).join(" ");
}

// Failure text for a track's panel: a short human cause, then the next step (mockup copy).
// The raw backend message (host, port, errno) never reaches the screen.
export function trackFailureText(raw: string | null | undefined): string {
  const detail = (raw ?? "").trim();
  const next = "Check the tools server, then press Regenerate.";
  if (/unreachable/i.test(detail)) return `Audyssey tools server unreachable. ${next}`;
  if (!detail) return `Generation failed. ${next}`;
  const short = detail.length > MAX_DETAIL ? `${detail.slice(0, MAX_DETAIL).trimEnd()}…` : detail;
  return `${/[.!?…]$/.test(short) ? short : `${short}.`} ${next}`;
}
