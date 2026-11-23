import type { SceneSummary } from "../lib/analysis";

interface AnalysisPanelProps {
  scenes: SceneSummary[];
  fps: number;
  frame: number;
  soundTrackCount: number;
  onSelectFrame: (frame: number) => void;
}

// Shown when no sound track is selected: what video analysis found, scene by scene.
export default function AnalysisPanel({ scenes, fps, frame, soundTrackCount, onSelectFrame }: AnalysisPanelProps) {
  const seconds = (f: number) => (f / fps).toFixed(1);
  const objectCount = new Set(scenes.flatMap((s) => s.objects.map((o) => o.label))).size;
  const eventCount = scenes.reduce((n, s) => n + s.events.length, 0);
  return (
    <section className="side" aria-label="Analysis">
      <h2>Analysis</h2>
      <p className="an-sum">
        {scenes.length} scenes, {objectCount} objects, {eventCount} visual events, {soundTrackCount} sound tracks
      </p>
      {scenes.map((scene) => {
        const current = frame >= scene.start_frame && frame < scene.end_frame;
        return (
          <details className={`detail an-scene${current ? " an-cur" : ""}`} key={scene.index} open={current}>
            <summary>
              <button type="button" className="an-seek" onClick={() => onSelectFrame(scene.start_frame)}>
                Scene {scene.index + 1}
              </button>
              <span className="an-time">
                {seconds(scene.start_frame)}–{seconds(scene.end_frame)} s
              </span>
            </summary>
            {scene.objects.length ? (
              <ul className="an-list">
                {scene.objects.map((o) => (
                  <li key={o.label}>
                    <span className="sw" style={o.color ? { background: o.color } : undefined} aria-hidden="true" />
                    {o.label}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="an-empty">No tracked objects</p>
            )}
            {scene.events.length ? (
              <ul className="an-list">
                {scene.events.map((e, i) => (
                  <li key={i}>
                    <button type="button" className="an-seek" title={e.description} onClick={() => onSelectFrame(e.start_frame)}>
                      {seconds(e.start_frame)} s
                    </button>
                    <span>
                      {e.type.replace(/_/g, " ")}: {e.object}
                    </span>
                  </li>
                ))}
              </ul>
            ) : null}
          </details>
        );
      })}
    </section>
  );
}
