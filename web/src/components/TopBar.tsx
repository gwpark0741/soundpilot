import Icon, { type IconName } from "./Icon";
import type { AssetResponse } from "../types";

interface TopBarProps {
  hasAudio: boolean | null;
  state: AssetResponse;
  // Detail of a failed action (e.g. an upload) that the open dialog may not be showing.
  submitError?: string | null;
  canGenerate: boolean;
  audioMode: "generate" | "separate";
  onSetMode: (mode: "generate" | "separate") => void;
  onOpen: () => void;
  onGenerate: () => void;
}

type Kind = "done" | "run" | "fail";
interface StatusView {
  kind: Kind;
  icon: IconName;
  text: string;
  // Only while running: null = no "i/n" in the stage text, so an indeterminate bar.
  progress?: { now: number; max: number } | null;
}

function statusView(
  state: AssetResponse,
  hasAudio: boolean | null,
  submitError: string | null,
  action: string,
): StatusView {
  if (state.status === "running") {
    const progress = state.stage?.match(/(\d+)\s*(?:\/|of)\s*(\d+)/);
    const text = state.stage
      ? state.stage.replace(/(\d+)\s*\/\s*(\d+)/, "$1 of $2").replace(/^./, (c) => c.toUpperCase())
      : "Working";
    return {
      kind: "run",
      icon: "spin",
      text,
      progress: progress ? { now: Number(progress[1]), max: Number(progress[2]) } : null,
    };
  }
  if (submitError) return { kind: "fail", icon: "x", text: submitError };
  if (state.status === "failed") {
    return { kind: "fail", icon: "x", text: state.error || "Something went wrong. Try again." };
  }
  const tracks = state.audio_tracks.length;
  if (state.status === "complete" && tracks > 0) {
    return {
      kind: "done",
      icon: "check",
      text: `Done. ${tracks} ${tracks === 1 ? "track" : "tracks"} ${hasAudio ? "ready to play" : "generated"}.`,
    };
  }
  return {
    kind: "done",
    icon: "info",
    text: state.video ? `Ready. Press ${action}.` : "Open a video to start.",
  };
}

const MODES = [
  { mode: "generate", label: "Generate" },
  { mode: "separate", label: "Separate" },
] as const;

export default function TopBar({
  hasAudio,
  state,
  submitError = null,
  canGenerate,
  audioMode,
  onSetMode,
  onOpen,
  onGenerate,
}: TopBarProps) {
  const running = state.status === "running";
  const action = audioMode === "separate" ? "Separate audio" : "Generate audio";
  const view = statusView(state, hasAudio, submitError, action);
  return (
    <header className="top">
      <div className="brand">
        <h1>V2A Inspect</h1>
        {hasAudio === null ? null : <span className="in-badge">{hasAudio ? "Original audio" : "Silent video"}</span>}
      </div>
      <div className="top-btns">
        <button className="btn" type="button" onClick={onOpen}>
          Open video
        </button>
      </div>
      <div className={`status st-k-${view.kind}`} role="status">
        <Icon name={view.icon} />
        <div className="status-t">
          <span>{view.text}</span>
          {view.progress === null ? (
            <div className="bar indeterminate" role="progressbar" aria-label="Progress">
              <i />
            </div>
          ) : view.progress ? (
            <div
              className="bar"
              role="progressbar"
              aria-label="Generation progress"
              aria-valuemin={0}
              aria-valuemax={view.progress.max}
              aria-valuenow={view.progress.now}
            >
              <i style={{ width: `${Math.min(100, (view.progress.now / view.progress.max) * 100)}%` }} />
            </div>
          ) : null}
        </div>
      </div>
      <div className="modes" role="radiogroup" aria-label="Audio method">
        {MODES.map(({ mode, label }) => {
          const noAudio = mode === "separate" && hasAudio === false;
          return (
            <button
              key={mode}
              type="button"
              role="radio"
              aria-checked={audioMode === mode}
              className="btn"
              disabled={running || noAudio}
              title={noAudio ? "No original audio to separate." : undefined}
              onClick={() => onSetMode(mode)}
            >
              {label}
            </button>
          );
        })}
      </div>
      <button className="btn primary" type="button" disabled={running || !canGenerate} onClick={onGenerate}>
        {running ? "Generating…" : action}
      </button>
    </header>
  );
}
