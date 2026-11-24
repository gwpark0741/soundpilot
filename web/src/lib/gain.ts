// Track gain is 0..2 (1 = unchanged); the slider shows it as 0..200 %.
export const GAIN_MAX_PERCENT = 200;
export const GAIN_STEP_PERCENT = 5;

export function gainToPercent(gain: number | null | undefined): number {
  return Math.round((gain ?? 1) * 100);
}

export function percentToGain(percent: number): number {
  return Math.min(Math.max(percent, 0), GAIN_MAX_PERCENT) / 100;
}

// HTMLMediaElement.volume is 0..1: above 100 % the browser preview stays at full volume; the mix uses the real gain.
export function gainToElementVolume(gain: number | null | undefined): number {
  return Math.min(Math.max(gain ?? 1, 0), 1);
}
