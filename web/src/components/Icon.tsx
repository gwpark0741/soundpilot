// Icon paths from the approved mockup (16x16, stroke style set by `.ic` in styles.css).
const PATHS = {
  play: <path d="M5.75 3.5v9l7-4.5z" />,
  // Not in the mockup (it only shows the paused state); same 16px grid and stroke-free fill as play.
  pause: <path d="M4.5 3.5h2.5v9H4.5zM9 3.5h2.5v9H9z" />,
  check: <path d="M3 8.5l3.2 3.2L13 4.8" />,
  x: (
    <>
      <circle cx="8" cy="8" r="6" />
      <path d="M5.8 5.8l4.4 4.4M10.2 5.8l-4.4 4.4" />
    </>
  ),
  clock: (
    <>
      <circle cx="8" cy="8" r="6" />
      <path d="M8 4.8V8l2.2 1.4" />
    </>
  ),
  spin: <path d="M8 2a6 6 0 1 0 6 6" />,
  info: (
    <>
      <circle cx="8" cy="8" r="6" />
      <path d="M8 7.2V11M8 5v.1" />
    </>
  ),
};

export type IconName = keyof typeof PATHS;

// Icon for each statusView tone (see lib/selection.ts).
export const STATUS_ICON: Record<"ok" | "run" | "wait" | "err", IconName> = {
  ok: "check",
  run: "spin",
  wait: "clock",
  err: "x",
};

export default function Icon({ name }: { name: IconName }) {
  return (
    <svg aria-hidden="true" className={name === "spin" ? "ic spin" : "ic"} height="16" viewBox="0 0 16 16" width="16">
      {PATHS[name]}
    </svg>
  );
}
