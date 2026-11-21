import { objectColor } from "./selection";
import type { SoundTrack, TimelineRow, VideoAsset } from "../types";

export interface ObjectLane {
  key: string;
  label: string;
  color: string;
  bars: { start_frame: number; end_frame: number }[];
}

interface VisualObjectLike {
  visual_object_id: string;
  label?: string | null;
  presences?: { scene_track_id?: string | null }[];
}

export const objectKey = (label?: string | null): string => (label ?? "").trim().toLowerCase();

// One lane per visual object (tracking rows grouped by object label); colour = palette slot by first appearance.
// The same key (lowercased label) colours the overlay, the Objects lanes and the sound lanes.
export function buildObjects(rows: TimelineRow[], asset: VideoAsset | null) {
  const objects = (asset?.visual_identity_layer?.visual_objects ?? []) as VisualObjectLike[];
  const labelByTrack = new Map<string, string>();
  for (const o of objects) {
    for (const p of o.presences ?? []) {
      if (o.label && p.scene_track_id) labelByTrack.set(p.scene_track_id, o.label);
    }
  }
  const lanes = new Map<string, ObjectLane>();
  const sceneTrackColors: Record<string, string> = {};
  for (const row of rows) {
    if (row.kind !== "track") continue;
    const label = (row.scene_track_id && labelByTrack.get(row.scene_track_id)) || row.label;
    const key = objectKey(label);
    let lane = lanes.get(key);
    if (!lane) {
      lane = { key, label, color: objectColor(lanes.size), bars: [] };
      lanes.set(key, lane);
    }
    lane.bars.push({ start_frame: row.start_frame, end_frame: row.end_frame });
    if (row.scene_track_id) sceneTrackColors[row.scene_track_id] = lane.color;
  }
  const colorOfLabel = (label?: string | null) => lanes.get(objectKey(label))?.color;

  // Sound track -> source -> visual object label; fallback: the scene tracks it targets. Masked tracks are never gray.
  function soundTrackColor(track: SoundTrack, targets: Record<string, string[]>): string {
    const scene = targets[track.sound_track_id] ?? [];
    if (scene.length === 0) return objectColor(-1);
    const source = asset?.sound_timeline?.sound_sources.find((s) => s.sound_source_id === track.sound_source_id);
    const object = source?.visual_object_id
      ? objects.find((o) => o.visual_object_id === source.visual_object_id)
      : undefined;
    return (
      colorOfLabel(object?.label ?? source?.label) ??
      scene.map((id) => sceneTrackColors[id]).find(Boolean) ??
      objectColor(0)
    );
  }
  return { lanes: [...lanes.values()], sceneTrackColors, soundTrackColor };
}
