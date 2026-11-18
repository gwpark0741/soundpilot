import type { RefObject } from "react";
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
  highlightLabel,
  videoRef,
  maxFrame = Math.max(0, video.frame_count - 1),
  onVideoSync,
  onVideoPlayState,
}: StageProps) {
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
      <div className="video-frame">
        <video
          ref={videoRef}
          src={videoSrc}
          onPause={() => onVideoPlayState?.(false)}
          onPlay={() => onVideoPlayState?.(true)}
          onSeeked={onVideoSync}
          onTimeUpdate={onVideoSync}
        />
        <TrackingOverlay
          video={video}
          tracks={tracks}
          frame={frame}
          highlightSceneTrackIds={highlightSceneTrackIds}
          highlightColor={highlightColor}
        />
        {chipText ? (
          <p className="stage-chip">
            <span
              aria-hidden="true"
              className={hasMask ? "stage-sw" : "stage-sw none"}
              style={hasMask ? { background: highlightColor } : undefined}
            />
            {chipText}
          </p>
        ) : null}
      </div>
      <div className="transport">
        <button
          aria-label={isPlaying ? "Pause video" : "Play video"}
          className="toggle"
          onClick={onTogglePlay}
          type="button"
        >
          {isPlaying ? "Pause" : "Play"}
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
