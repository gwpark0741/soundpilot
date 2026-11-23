import { useRef, type RefObject } from "react";
import Icon from "./Icon";
import TrackingOverlay from "./TrackingOverlay";
import type { TrackWindowTrack, VideoSummary } from "../types";

interface StageProps {
  videoSrc: string;
  video: VideoSummary;
  frame: number;
  isPlaying: boolean;
  onTogglePlay: () => void;
  onSelectFrame: (frame: number) => void;
  tracks: TrackWindowTrack[];
  highlightSceneTrackIds: string[];
  highlightColor: string;
  // No sound track selected: the overlay draws every tracked object in its own colour.
  showAllObjects?: boolean;
  sceneTrackColors?: Record<string, string>;
  // Source label, or the full "<source> has no object mask" text when there is nothing to highlight.
  highlightLabel: string | null;
  videoRef: RefObject<HTMLVideoElement | null>;
  maxFrame?: number;
  onVideoSync?: () => void;
  onVideoPlayState?: (playing: boolean) => void;
}

const NO_MASK_SUFFIX = "has no object mask";

export default function Stage({
  videoSrc,
  video,
  frame,
  isPlaying,
  onTogglePlay,
  onSelectFrame,
  tracks,
  highlightSceneTrackIds,
  highlightColor,
  showAllObjects = false,
  sceneTrackColors = {},
  highlightLabel,
  videoRef,
  maxFrame = Math.max(0, video.frame_count - 1),
  onVideoSync,
  onVideoPlayState,
}: StageProps) {
  // A new src reloads the element: remember where it was so it can pick up from there.
  const resumeRef = useRef<{ time: number; paused: boolean } | null>(null);
  const shownSrcRef = useRef(videoSrc);
  if (shownSrcRef.current !== videoSrc) {
    shownSrcRef.current = videoSrc;
    const element = videoRef.current;
    resumeRef.current = element ? { time: element.currentTime, paused: element.paused } : null;
  }
  // The reload resets the element to 0 and pauses it without a pause event.
  const syncPlayState = () => onVideoPlayState?.(!videoRef.current?.paused);
  const syncFrame = () => {
    if (!resumeRef.current) {
      onVideoSync?.();
    }
  };
  const resume = () => {
    const element = videoRef.current;
    const saved = resumeRef.current;
    resumeRef.current = null;
    if (element && saved) {
      element.currentTime = saved.time;
      if (!saved.paused) {
        element.play().catch(() => {});
      }
    }
    syncPlayState();
  };
  const fps = video.fps || 30;
  const timeText = `${(frame / fps).toFixed(2)} s / ${video.duration_sec.toFixed(2)} s`;
  const hasMask = highlightSceneTrackIds.length > 0;
  const chipText =
    highlightLabel === null
      ? null
      : highlightLabel.endsWith(NO_MASK_SUFFIX)
        ? highlightLabel
        : `Highlighting ${highlightLabel}`;

  return (
    <>
      <div className="stage-view">
        <div className="video-box">
          <video
            ref={videoRef}
            src={videoSrc}
            onEmptied={syncPlayState}
            onLoadedMetadata={resume}
            onPause={() => onVideoPlayState?.(false)}
            onPlay={() => onVideoPlayState?.(true)}
            onSeeked={syncFrame}
            onTimeUpdate={syncFrame}
          />
          <TrackingOverlay
            video={video}
            tracks={tracks}
            frame={frame}
            highlightSceneTrackIds={showAllObjects ? null : highlightSceneTrackIds}
            highlightColor={highlightColor}
            sceneTrackColors={sceneTrackColors}
          />
          {chipText ? (
            <p className="chip">
              <span
                aria-hidden="true"
                className={hasMask ? "sw" : "sw sw-none"}
                style={hasMask ? { background: highlightColor } : undefined}
              />
              {chipText}
            </p>
          ) : null}
        </div>
      </div>
      <div className="transport">
        <button
          aria-label={isPlaying ? "Pause video" : "Play video"}
          className="btn ico"
          onClick={onTogglePlay}
          type="button"
        >
          <Icon name={isPlaying ? "pause" : "play"} />
        </button>
        <input
          aria-label="Frame"
          aria-valuetext={`Frame ${frame}, ${(frame / fps).toFixed(2)} seconds`}
          max={maxFrame}
          min="0"
          onChange={(event) => onSelectFrame(Number(event.target.value))}
          step="1"
          type="range"
          value={frame}
        />
        <span className="time">{timeText}</span>
      </div>
    </>
  );
}
