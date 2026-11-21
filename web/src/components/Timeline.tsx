import { useEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent } from "react";
import Icon, { STATUS_ICON } from "./Icon";
import type { ObjectLane } from "../lib/objects";
import { methodLabel, objectColor, statusView } from "../lib/selection";
import type {
  AudioTrackArtifact,
  SoundTrack,
  TimelineRow,
  TrackStatus,
} from "../types";

interface TimelineProps {
  rows: TimelineRow[];
  soundTracks: SoundTrack[];
  audioTracks: AudioTrackArtifact[];
  frame: number;
  frameCount: number;
  fps?: number;
  onSelectFrame: (frame: number) => void;
  onPlayTrackAudio?: (soundTrackId: string, startFrame: number) => void;
  onEditSoundEvent?: (
    soundEventId: string,
    startFrame: number,
    endFrame: number,
  ) => void;
  selectedTrackId: string | null;
  onSelectTrack: (trackId: string) => void;
  soloTrackId: string | null;
  onToggleSolo: (trackId: string) => void;
  trackStatus?: Record<string, TrackStatus>;
  appRunning: boolean;
  trackColors?: Record<string, string>;
  objectLanes?: ObjectLane[];
}

interface LaneGroup {
  soundTrack: SoundTrack;
  audioTrack?: AudioTrackArtifact;
  rows: TimelineRow[];
}

interface ActiveDrag {
  soundEventId: string;
  mode: "move" | "resize-left" | "resize-right";
  startX: number;
  laneWidth: number;
  startFrame: number;
  endFrame: number;
}

const NONE_COLOR = objectColor(-1);
const NO_AUDIO_REASON = "Generate audio first.";

export default function Timeline({
  rows,
  soundTracks,
  audioTracks,
  frame,
  frameCount,
  fps = 30,
  onSelectFrame,
  onPlayTrackAudio,
  onEditSoundEvent,
  selectedTrackId,
  onSelectTrack,
  soloTrackId,
  onToggleSolo,
  trackStatus = {},
  appRunning,
  trackColors = {},
  objectLanes = [],
}: TimelineProps) {
  // null = automatic: open while running or before any sound lane exists; the toggle overrides.
  const [objectsOpen, setObjectsOpen] = useState<boolean | null>(null);
  const showObjects = objectsOpen ?? (appRunning || soundTracks.length === 0);
  const dragRef = useRef<ActiveDrag | null>(null);
  const audioByTrack = useMemo(
    () => new Map(audioTracks.map((item) => [item.sound_track_id, item])),
    [audioTracks],
  );
  const lanes = groupRows(rows, soundTracks, audioByTrack);
  const timelineEndFrame = Math.max(frameCount, maxRowEnd(rows), 1);
  const maxPlaybackFrame = Math.max(0, timelineEndFrame - 1);
  const clampedFrame = clamp(frame, 0, maxPlaybackFrame);
  const playheadLeft = `${(clampedFrame / Math.max(maxPlaybackFrame, 1)) * 100}%`;
  const tickStep = Math.max(1, Math.ceil(timelineEndFrame / fps / 8));
  const ticks: number[] = [];
  for (let second = 0; second * fps < timelineEndFrame; second += tickStep) {
    ticks.push(second);
  }

  useEffect(() => {
    function handlePointerMove(event: globalThis.PointerEvent) {
      const drag = dragRef.current;
      if (!drag || !onEditSoundEvent) {
        return;
      }
      const deltaFrames = Math.round(
        ((event.clientX - drag.startX) / Math.max(drag.laneWidth, 1)) *
          timelineEndFrame,
      );
      const length = Math.max(1, drag.endFrame - drag.startFrame);
      let nextStart = drag.startFrame;
      let nextEnd = drag.endFrame;

      if (drag.mode === "resize-left") {
        nextStart = clamp(drag.startFrame + deltaFrames, 0, drag.endFrame - 1);
      } else if (drag.mode === "resize-right") {
        nextEnd = clamp(drag.endFrame + deltaFrames, drag.startFrame + 1, timelineEndFrame);
      } else {
        nextStart = clamp(
          drag.startFrame + deltaFrames,
          0,
          Math.max(0, timelineEndFrame - length),
        );
        nextEnd = nextStart + length;
      }

      onEditSoundEvent(drag.soundEventId, nextStart, nextEnd);
    }

    function handlePointerUp() {
      dragRef.current = null;
    }

    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", handlePointerUp);
    window.addEventListener("pointercancel", handlePointerUp);
    return () => {
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);
      window.removeEventListener("pointercancel", handlePointerUp);
    };
  }, [onEditSoundEvent, timelineEndFrame]);

  function selectFrame(event: PointerEvent<HTMLElement>) {
    if (maxPlaybackFrame <= 0) {
      return;
    }
    const rect = event.currentTarget.getBoundingClientRect();
    const ratio = clamp((event.clientX - rect.left) / rect.width, 0, 1);
    onSelectFrame(Math.round(ratio * maxPlaybackFrame));
  }

  function startSoundEventEdit(
    event: PointerEvent<HTMLElement>,
    row: TimelineRow,
  ) {
    if (!row.sound_event_id || !onEditSoundEvent) {
      return;
    }
    const lane = event.currentTarget.closest(".lane-track");
    if (!(lane instanceof HTMLElement)) {
      return;
    }
    const target = event.target instanceof HTMLElement ? event.target : null;
    const handle = target?.dataset.handle;
    dragRef.current = {
      soundEventId: row.sound_event_id,
      mode:
        handle === "left"
          ? "resize-left"
          : handle === "right"
            ? "resize-right"
            : "move",
      startX: event.clientX,
      laneWidth: lane.getBoundingClientRect().width,
      startFrame: row.start_frame,
      endFrame: row.end_frame,
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
    event.preventDefault();
    event.stopPropagation();
  }

  return (
    <section className="tl panel" aria-label="Timeline">
      <div className="lane ruler">
        <div className="lane-label-sp" />
        <div
          className="lane-track"
          onPointerDown={selectFrame}
          onPointerMove={(event) => {
            if (event.buttons === 1) {
              selectFrame(event);
            }
          }}
        >
          {ticks.map((second, index) => {
            const left = ((second * fps) / timelineEndFrame) * 100;
            // A tick near the right edge hangs to its left so its label stays inside the ruler.
            return (
              <span className={`tick t${index}${left > 92 ? " end" : ""}`} key={second} style={{ left: `${left}%` }}>
                {second} s
              </span>
            );
          })}
          <span className="playhead" style={{ left: playheadLeft }} />
        </div>
      </div>
      {objectLanes.length > 0 ? (
        <>
          <button
            aria-expanded={showObjects}
            className="og-toggle"
            onClick={() => setObjectsOpen(!showObjects)}
            type="button"
          >
            <span aria-hidden="true" className="og-caret">{showObjects ? "▾" : "▸"}</span>
            Objects
            <span className="og-count">{objectLanes.length}</span>
          </button>
          {showObjects
            ? objectLanes.map((object) => (
                <div className="lane obj" key={object.key}>
                  <div className="lane-head">
                    <span className="lh-name">
                      <span aria-hidden="true" className="sw" style={{ background: object.color }} />
                      {object.label}
                    </span>
                  </div>
                  <div className="lane-track" onPointerDown={selectFrame}>
                    {object.bars.map((bar, index) => {
                      const style = barStyle(
                        { ...bar, lane: object.key, label: object.label, kind: "track" },
                        timelineEndFrame,
                      );
                      return (
                        <span
                          className="ob"
                          key={`${bar.start_frame}-${bar.end_frame}-${index}`}
                          style={{ left: `${style.left}%`, width: `${style.width}%`, background: object.color }}
                          title={`${object.label}: ${bar.start_frame}-${bar.end_frame}`}
                        />
                      );
                    })}
                    <span className="playhead" style={{ left: playheadLeft }} />
                  </div>
                </div>
              ))
            : null}
        </>
      ) : null}
      {lanes.length === 0 && objectLanes.length === 0 ? (
        <div className="empty-lane">Pipeline lanes will appear here.</div>
      ) : (
        lanes.map(({ soundTrack, audioTrack, rows: laneRows }) => {
          const id = soundTrack.sound_track_id;
          const selected = id === selectedTrackId;
          const color = trackColors[id] ?? NONE_COLOR;
          const none = color === NONE_COLOR;
          const status = statusView(trackStatus[id], appRunning);
          const method = methodLabel(soundTrack.generation_model);
          const noAudioTitle = audioTrack ? undefined : NO_AUDIO_REASON;
          return (
            <div
              className={selected ? "lane sel" : "lane"}
              data-obj={none ? "none" : "object"}
              key={id}
              onClick={() => onSelectTrack(id)}
              style={{ "--oc": color } as CSSProperties}
            >
              <div className="lane-head">
                <button
                  aria-current={selected ? "true" : "false"}
                  className="lh-main"
                  type="button"
                >
                  <span className="lh-name">
                    <span
                      aria-hidden="true"
                      className={none ? "sw sw-none" : "sw"}
                      style={none ? undefined : { background: color }}
                    />
                    {soundTrack.label}
                  </span>
                  <span className="lh-meta">
                    <span
                      className={
                        soundTrack.generation_model === "separate"
                          ? "badge badge-sep"
                          : "badge badge-gen"
                      }
                    >
                      {method}
                    </span>
                    {status.label && status.label !== "Done" ? (
                      <span
                        className={`st st-${
                          status.tone === "run" ? "run" : status.tone === "wait" ? "queue" : "fail"
                        }`}
                      >
                        <Icon name={STATUS_ICON[status.tone ?? "err"]} />
                        {status.label}
                      </span>
                    ) : null}
                  </span>
                </button>
                <div className="ctl">
                  <button
                    aria-label={`Play ${soundTrack.label}`}
                    className="btn sm ico"
                    disabled={!audioTrack}
                    onClick={() => onPlayTrackAudio?.(id, clampedFrame)}
                    title={noAudioTitle}
                    type="button"
                  >
                    <Icon name="play" />
                  </button>
                  <button
                    aria-label={`Solo ${soundTrack.label}`}
                    aria-pressed={soloTrackId === id}
                    className="btn sm tog"
                    disabled={!audioTrack}
                    onClick={() => onToggleSolo(id)}
                    title={noAudioTitle}
                    type="button"
                  >
                    Solo
                  </button>
                </div>
              </div>
              <div
                className="lane-track"
                onPointerDown={selectFrame}
                onPointerMove={(event) => {
                  if (event.buttons === 1 && !dragRef.current) {
                    selectFrame(event);
                  }
                }}
              >
                {audioTrack ? <Waveform peaks={audioTrack.waveform_peaks} /> : null}
                {laneRows.map((row, index) => {
                  const bar = barStyle(row, timelineEndFrame);
                  const editable = !!row.sound_event_id && !!onEditSoundEvent;
                  return (
                    <button
                      aria-label={`Edit ${row.label} event, frames ${row.start_frame} to ${row.end_frame}`}
                      className={bar.width >= 50 ? "ev long" : "ev"}
                      key={row.sound_event_id ?? `${id}-${row.start_frame}-${row.end_frame}-${index}`}
                      onPointerDown={editable ? (event) => startSoundEventEdit(event, row) : undefined}
                      style={{ left: `${bar.left}%`, width: `${bar.width}%` }}
                      tabIndex={-1}
                      title={`${row.label}: ${row.start_frame}-${row.end_frame}`}
                      type="button"
                    >
                      {editable ? (
                        <>
                          <span aria-hidden="true" className="ev-handle ev-handle-left" data-handle="left" />
                          <span aria-hidden="true" className="ev-handle ev-handle-right" data-handle="right" />
                        </>
                      ) : null}
                    </button>
                  );
                })}
                <span className="playhead" style={{ left: playheadLeft }} />
              </div>
            </div>
          );
        })
      )}
    </section>
  );
}

function Waveform({ peaks }: { peaks: number[] }) {
  if (peaks.length === 0) {
    return null;
  }
  const sampled = peaks.length > 384 ? samplePeaks(peaks, 384) : peaks;
  const top = sampled.map((peak, index) => `${index},${(12 - clamp(peak, 0, 1) * 11.5).toFixed(1)}`);
  const bottom = sampled
    .map((peak, index) => `${index},${(12 + clamp(peak, 0, 1) * 11.5).toFixed(1)}`)
    .reverse();
  return (
    <svg
      aria-hidden="true"
      className="wave"
      preserveAspectRatio="none"
      viewBox={`0 0 ${Math.max(sampled.length - 1, 1)} 24`}
    >
      <polygon points={[...top, ...bottom].join(" ")} />
    </svg>
  );
}

function groupRows(
  rows: TimelineRow[],
  soundTracks: SoundTrack[],
  audioByTrack: Map<string, AudioTrackArtifact>,
): LaneGroup[] {
  return soundTracks.map((soundTrack) => {
    const lane = soundLane(soundTrack);
    return {
      soundTrack,
      audioTrack: audioByTrack.get(soundTrack.sound_track_id),
      rows: rows.filter(
        (row) =>
          isSoundRow(row) &&
          (row.sound_track_id === soundTrack.sound_track_id || row.lane === lane),
      ),
    };
  });
}

function soundLane(track: SoundTrack): string {
  return `[${track.track_type}] ${track.label}`;
}

function isSoundRow(row: TimelineRow): boolean {
  return row.kind !== "scene" && row.kind !== "track" && !row.lane.startsWith("visual: ");
}

function maxRowEnd(rows: TimelineRow[]): number {
  return rows.reduce((maxEnd, row) => Math.max(maxEnd, row.end_frame), 0);
}

function barStyle(
  row: TimelineRow,
  timelineEndFrame: number,
): { left: number; width: number } {
  const start = clamp(row.start_frame, 0, timelineEndFrame);
  const end = clamp(row.end_frame, start, timelineEndFrame);
  const left = (start / timelineEndFrame) * 100;
  const rawWidth = ((end - start) / timelineEndFrame) * 100;
  const width = Math.min(100 - left, Math.max(0.12, rawWidth));
  return { left, width };
}

function samplePeaks(peaks: number[], targetCount: number): number[] {
  if (peaks.length <= targetCount) {
    return peaks;
  }
  const sampled: number[] = [];
  for (let index = 0; index < targetCount; index += 1) {
    const start = Math.floor((index / targetCount) * peaks.length);
    const end = Math.max(start + 1, Math.floor(((index + 1) / targetCount) * peaks.length));
    sampled.push(Math.max(...peaks.slice(start, end)));
  }
  return sampled;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}
