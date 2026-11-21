import { useEffect, useMemo, useRef } from "react";
import { rasterizeMasks } from "../lib/rle";
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
// Masks are rasterized at half the 1280x720 source size: enough for a smooth outline,
// a quarter of the per-frame pixel work.
const MASK_FILL_ALPHA = 0.4;
const MASK_W = 640;
const MASK_H = 360;

// One offscreen canvas/ImageData reused for every frame (no per-frame allocation).
let maskCanvas: HTMLCanvasElement | null = null;
let maskImage: ImageData | null = null;
let maskPixels: Uint32Array | null = null;
let maskKey = "";

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
    // Assigning width/height reallocates the backing store, so only do it when the size changes.
    const pxWidth = Math.max(1, Math.round(rect.width * ratio));
    const pxHeight = Math.max(1, Math.round(rect.height * ratio));
    if (canvas.width !== pxWidth || canvas.height !== pxHeight) {
      canvas.width = pxWidth;
      canvas.height = pxHeight;
    }
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

    const rles = points.flatMap((point) => (point.mask_rle ? [point.mask_rle] : []));
    if (rles.length > 0) {
      context.drawImage(
        paintMasks(rles, highlightColor),
        offsetX,
        offsetY,
        sourceWidth * scale,
        sourceHeight * scale,
      );
    }

    for (const point of points) {
      if (!point.mask_rle && point.bbox_xyxy) {
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

// All masks of the frame in one image: translucent fill, 2px (=4 source px) colour edge,
// and a dark halo ring just outside it. Pixels are written as packed little-endian ABGR.
function paintMasks(rles: string[], color: string): HTMLCanvasElement {
  if (!maskCanvas || !maskImage || !maskPixels) {
    maskCanvas = document.createElement("canvas");
    maskCanvas.width = MASK_W;
    maskCanvas.height = MASK_H;
    maskImage = new ImageData(MASK_W, MASK_H);
    maskPixels = new Uint32Array(maskImage.data.buffer);
  }
  const key = `${color}|${rles.join("|")}`;
  if (key === maskKey) {
    return maskCanvas;
  }
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(color.slice(i, i + 2), 16));
  const rgb = ((b << 16) | (g << 8) | r) >>> 0;
  const FILL = (rgb | (Math.round(255 * MASK_FILL_ALPHA) << 24)) >>> 0;
  const EDGE = (rgb | (255 << 24)) >>> 0;
  const HALO = (217 << 24) >>> 0; // black, alpha 0.85
  const m = rasterizeMasks(rles, MASK_W, MASK_H);
  const px = maskPixels;
  const last = MASK_W * MASK_H;
  for (let y = 0; y < MASK_H; y++) {
    for (let x = 0; x < MASK_W; x++) {
      const i = y * MASK_W + x;
      const left = x > 0 ? m[i - 1] : 0;
      const right = x < MASK_W - 1 ? m[i + 1] : 0;
      const up = i >= MASK_W ? m[i - MASK_W] : 0;
      const down = i + MASK_W < last ? m[i + MASK_W] : 0;
      if (m[i]) {
        // Edge is 2 mask px (= 4 source px, ~3 CSS px as in the mockup's 3px stroke).
        px[i] =
          left && right && up && down &&
          (x > 1 ? m[i - 2] : 0) && (x < MASK_W - 2 ? m[i + 2] : 0) &&
          (i >= 2 * MASK_W ? m[i - 2 * MASK_W] : 0) && (i + 2 * MASK_W < last ? m[i + 2 * MASK_W] : 0)
            ? FILL
            : EDGE;
      } else {
        px[i] = left | right | up | down ? HALO : 0;
      }
    }
  }
  maskCanvas.getContext("2d")?.putImageData(maskImage, 0, 0);
  maskKey = key;
  return maskCanvas;
}
