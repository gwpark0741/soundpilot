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
