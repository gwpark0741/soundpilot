import type { TimelineRow } from "../types";

export interface SceneSummary {
  index: number;
  start_frame: number;
  end_frame: number;
  objects: { label: string; color?: string }[];
  events: { object: string; type: string; description: string; start_frame: number }[];
}

// Scene -> objects tracked in it and visual events starting in it, from the timeline rows.
export function summarizeScenes(rows: TimelineRow[], sceneTrackColors: Record<string, string>): SceneSummary[] {
  const scenes = rows
    .filter((row) => row.kind === "scene")
    .sort((a, b) => a.start_frame - b.start_frame)
    .map((row, index): SceneSummary => ({
      index,
      start_frame: row.start_frame,
      end_frame: row.end_frame,
      objects: [],
      events: [],
    }));
  const sceneAt = (frame: number) =>
    scenes.find((s) => frame >= s.start_frame && frame < s.end_frame) ?? scenes[scenes.length - 1];
  for (const row of rows) {
    const scene = scenes.length ? sceneAt(row.start_frame) : undefined;
    if (!scene) continue;
    if (row.kind === "track") {
      if (!scene.objects.some((o) => o.label === row.label)) {
        scene.objects.push({ label: row.label, color: row.scene_track_id ? sceneTrackColors[row.scene_track_id] : undefined });
      }
    } else if (row.lane.startsWith("visual: ")) {
      const [type, ...rest] = row.label.split(": ");
      scene.events.push({
        object: row.lane.slice("visual: ".length),
        type,
        description: rest.join(": "),
        start_frame: row.start_frame,
      });
    }
  }
  for (const scene of scenes) scene.events.sort((a, b) => a.start_frame - b.start_frame);
  return scenes;
}
