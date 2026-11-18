const MARGIN = 60;

// Whether the tracking window must be (re)fetched for this frame. A window clamped at 0 or maxFrame
// can never hold MARGIN frames of slack on that side, so those edges count as covered.
export function trackWindowNeedsFetch(s: {
  window: { start_frame: number; end_frame: number; assetVersion: number } | null;
  frame: number;
  maxFrame: number;
  assetVersion: number;
}): boolean {
  const w = s.window;
  if (!w || w.assetVersion !== s.assetVersion) return true;
  const startOk = w.start_frame <= 0 || s.frame >= w.start_frame + MARGIN;
  const endOk = w.end_frame >= s.maxFrame || s.frame <= w.end_frame - MARGIN;
  return !(startOk && endOk);
}
