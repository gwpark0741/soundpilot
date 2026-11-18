import { useEffect, useMemo, useRef } from "react";
import { decodeCocoRle } from "../lib/rle";
import type { TrackWindowTrack, VideoSummary } from "../types";

interface TrackingOverlayProps {
  video: VideoSummary;
  tracks: TrackWindowTrack[];
  frame: number;
  highlightSceneTrackIds: string[];
  highlightColor: string;
}

type Point = TrackWindowTrack["points"][number];

const SOURCE_WIDTH = 1280;
const SOURCE_HEIGHT = 720;
const MASK_FILL_ALPHA = 0.3;
const HALO_BLUR = 3;
const CACHE_LIMIT = 8;

// Decoded masks keyed by the RLE string. Each is ~1280x720 pixels, so keep only a few
// recent ones (consecutive frames rarely share a mask; a full window would be GBs).
const maskCache = new Map<string, { fill: HTMLCanvasElement; edge: HTMLCanvasElement }>();

export default function TrackingOverlay({
  video,
  tracks,
  frame,
  highlightSceneTrackIds,
  highlightColor,
}: TrackingOverlayProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const pointIndex = useMemo(
    () => indexTrackPoints(tracks, highlightSceneTrackIds),
    [tracks, highlightSceneTrackIds],
  );

  useEffect(() => {
    const canvas = canvasRef.current;
    const context = canvas?.getContext("2d");
    if (!canvas || !context) {
      return;
    }

    const rect = canvas.getBoundingClientRect();
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.round(rect.width * ratio));
    canvas.height = Math.max(1, Math.round(rect.height * ratio));
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, rect.width, rect.height);

    const points = pointIndex.get(frame) ?? [];
    if (points.length === 0) {
      return;
    }

    const sourceWidth = video.width ?? SOURCE_WIDTH;
    const sourceHeight = video.height ?? SOURCE_HEIGHT;
    const scale = Math.min(rect.width / sourceWidth, rect.height / sourceHeight);
    const offsetX = (rect.width - sourceWidth * scale) / 2;
    const offsetY = (rect.height - sourceHeight * scale) / 2;

    for (const point of points) {
      if (point.mask_rle) {
        const mask = maskCanvases(point.mask_rle, highlightColor);
        const w = sourceWidth * scale;
        const h = sourceHeight * scale;
        context.drawImage(mask.fill, offsetX, offsetY, w, h);
        context.save();
        context.shadowColor = "#000";
        context.shadowBlur = HALO_BLUR;
        context.drawImage(mask.edge, offsetX, offsetY, w, h);
        context.restore();
      } else if (point.bbox_xyxy) {
        const [x1, y1, x2, y2] = point.bbox_xyxy;
        const left = offsetX + x1 * scale;
        const top = offsetY + y1 * scale;
        const width = Math.max(1, (x2 - x1) * scale);
        const height = Math.max(1, (y2 - y1) * scale);
        context.lineWidth = 4;
        context.strokeStyle = "#000";
        context.strokeRect(left, top, width, height);
        context.lineWidth = 2;
        context.strokeStyle = highlightColor;
        context.strokeRect(left, top, width, height);
      }
    }
  }, [frame, highlightColor, pointIndex, video.height, video.width]);

  return <canvas aria-hidden="true" className="tracking-overlay" ref={canvasRef} />;
}

function indexTrackPoints(tracks: TrackWindowTrack[], sceneTrackIds: string[]): Map<number, Point[]> {
  const index = new Map<number, Point[]>();
  for (const track of tracks) {
    if (!sceneTrackIds.includes(track.scene_track_id)) {
      continue;
    }
    for (const point of track.points) {
      const framePoints = index.get(point.frame_index) ?? [];
      framePoints.push(point);
      index.set(point.frame_index, framePoints);
    }
  }
  return index;
}

// Two mask-sized canvases: translucent fill, and a 2px colour edge (drawn with a dark shadow as halo).
function maskCanvases(rle: string, color: string) {
  const key = `${color}|${rle}`;
  const hit = maskCache.get(key);
  if (hit) {
    maskCache.delete(key);
    maskCache.set(key, hit);
    return hit;
  }
  const { width, height, data } = decodeCocoRle(rle);
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(color.slice(i, i + 2), 16));
  const fill = new ImageData(width, height);
  const edge = new ImageData(width, height);
  const inside = (x: number, y: number) =>
    x >= 0 && y >= 0 && x < width && y < height && data[y * width + x] === 1;
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      if (!data[y * width + x]) {
        continue;
      }
      const o = (y * width + x) * 4;
      fill.data.set([r, g, b, Math.round(255 * MASK_FILL_ALPHA)], o);
      if (!inside(x - 2, y) || !inside(x + 2, y) || !inside(x, y - 2) || !inside(x, y + 2)) {
        edge.data.set([r, g, b, 255], o);
      }
    }
  }
  const entry = { fill: toCanvas(fill), edge: toCanvas(edge) };
  maskCache.set(key, entry);
  if (maskCache.size > CACHE_LIMIT) {
    maskCache.delete(maskCache.keys().next().value as string);
  }
  return entry;
}

function toCanvas(image: ImageData): HTMLCanvasElement {
  const canvas = document.createElement("canvas");
  canvas.width = image.width;
  canvas.height = image.height;
  canvas.getContext("2d")?.putImageData(image, 0, 0);
  return canvas;
}
