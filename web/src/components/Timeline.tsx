import { memo, useEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent } from "react";
import Icon, { STATUS_ICON } from "./Icon";
import type { ObjectLane } from "../lib/objects";
import { clipPeaks, clipPlacement, dragClip, isFullClip, resolveClip, type Clip, type ClipDrag } from "../lib/clip";
import { methodLabel, objectColor, statusView } from "../lib/selection";
import { dragSpan, drawSpan, minSpanFrames, removeSpan, sortSpans, type Span, type SpanDrag } from "../lib/span";
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
  // Why timeline edits that change the audio (clips, new tracks) are unavailable right now, if they are.
  editBlockedReason?: string | null;
  // Why adding a track is unavailable right now (a running job), if it is.
  trackBlockedReason?: string | null;
  // Commits one finished clip drag; the timeline keeps showing the dragged clip until it settles.
  onEditClip?: (soundTrackId: string, clip: Clip) => Promise<void> | void;
  onAddTrack?: () => void;
  // The spans of the new track being drafted, drawn on their own lane below the sound lanes.
  draftSpans?: Span[];
  onDraftSpansChange?: (spans: Span[]) => void;
}

type EditMode = "spans" | "audio";
const EDIT_MODES: { mode: EditMode; label: string }[] = [
  { mode: "spans", label: "Edit spans" },
  { mode: "audio", label: "Edit audio" },
];

interface ActiveClipDrag {
  soundTrackId: string;
  mode: ClipDrag;
  startX: number;
  laneWidth: number;
  clip: Clip;
  latest: Clip;
  stemDuration: number;
}

interface LaneGroup {
  soundTrack: SoundTrack;
  audioTrack?: AudioTrackArtifact;
  rows: TimelineRow[];
}

interface ActiveDraftDrag {
  mode: SpanDrag | "draw";
  index: number;
  startX: number;
  laneLeft: number;
  laneWidth: number;
  spans: Span[];
  latest: Span[];
}

interface ActiveDrag {
  soundEventId: string;
  mode: SpanDrag;
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
  editBlockedReason = null,
  trackBlockedReason = null,
  onEditClip,
  onAddTrack,
  draftSpans,
  onDraftSpansChange,
}: TimelineProps) {
  const [editMode, setEditMode] = useState<EditMode>("spans");
  const [clipDraft, setClipDraft] = useState<{ soundTrackId: string; clip: Clip } | null>(null);
  const clipDragRef = useRef<ActiveClipDrag | null>(null);
  // null = automatic: open while running or before any sound lane exists; the toggle overrides.
  const [objectsOpen, setObjectsOpen] = useState<boolean | null>(null);
  const showObjects = objectsOpen ?? (appRunning || soundTracks.length === 0);
  const dragRef = useRef<ActiveDrag | null>(null);
  const draftDragRef = useRef<ActiveDraftDrag | null>(null);
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

  // The window listeners are added once; they read the latest props from here.
  const latestRef = useRef({ onEditSoundEvent, onDraftSpansChange, timelineEndFrame, fps });
  latestRef.current = { onEditSoundEvent, onDraftSpansChange, timelineEndFrame, fps };

  useEffect(() => {
    function handlePointerMove(event: globalThis.PointerEvent) {
      const { onEditSoundEvent, onDraftSpansChange, timelineEndFrame, fps } = latestRef.current;
      const framesAt = (pixels: number, laneWidth: number) =>
        Math.round((pixels / Math.max(laneWidth, 1)) * timelineEndFrame);
      const draft = draftDragRef.current;
      if (draft && onDraftSpansChange) {
        const min = minSpanFrames(fps);
        draft.latest =
          draft.mode === "draw"
            ? drawSpan(
                draft.spans,
                framesAt(draft.startX - draft.laneLeft, draft.laneWidth),
                framesAt(event.clientX - draft.laneLeft, draft.laneWidth),
                timelineEndFrame,
                min,
              )
            : draft.spans.map((span, index) =>
                index === draft.index
                  ? dragSpan(draft.mode as SpanDrag, span, framesAt(event.clientX - draft.startX, draft.laneWidth), timelineEndFrame, min)
                  : span,
              );
        onDraftSpansChange(draft.latest);
        return;
      }
      const drag = dragRef.current;
      if (!drag || !onEditSoundEvent) {
        return;
      }
      const next = dragSpan(
        drag.mode,
        { start_frame: drag.startFrame, end_frame: drag.endFrame },
        framesAt(event.clientX - drag.startX, drag.laneWidth),
        timelineEndFrame,
      );
      onEditSoundEvent(drag.soundEventId, next.start_frame, next.end_frame);
    }

    function handlePointerUp() {
      dragRef.current = null;
      const draft = draftDragRef.current;
      draftDragRef.current = null;
      // Spans stay in drag order while one is being drawn or moved; they are put in time order when it ends.
      if (draft && draft.latest !== draft.spans) {
        latestRef.current.onDraftSpansChange?.(sortSpans(draft.latest));
      }
    }

    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", handlePointerUp);
    window.addEventListener("pointercancel", handlePointerUp);
    return () => {
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);
      window.removeEventListener("pointercancel", handlePointerUp);
    };
  }, []);

  function selectFrame(event: PointerEvent<HTMLElement>) {
    if (maxPlaybackFrame <= 0) {
      return;
    }
    const rect = event.currentTarget.getBoundingClientRect();
    const ratio = clamp((event.clientX - rect.left) / rect.width, 0, 1);
    onSelectFrame(Math.round(ratio * maxPlaybackFrame));
  }

  const videoDuration = frameCount / fps;
  const timelineSec = timelineEndFrame / fps;

  function startClipDrag(event: PointerEvent<HTMLElement>, soundTrackId: string, clip: Clip, stemDuration: number) {
    const lane = event.currentTarget.closest(".lane-track");
    if (!(lane instanceof HTMLElement)) {
      return;
    }
    const handle = event.target instanceof HTMLElement ? event.target.dataset.handle : undefined;
    clipDragRef.current = {
      soundTrackId,
      mode: handle === "left" ? "trim-left" : handle === "right" ? "trim-right" : "move",
      startX: event.clientX,
      laneWidth: lane.getBoundingClientRect().width,
      clip,
      latest: clip,
      stemDuration,
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
    event.preventDefault();
    event.stopPropagation();
  }

  function moveClipDrag(event: PointerEvent<HTMLElement>) {
    const drag = clipDragRef.current;
    if (!drag) {
      return;
    }
    const deltaSec = ((event.clientX - drag.startX) / Math.max(drag.laneWidth, 1)) * timelineSec;
    drag.latest = dragClip(drag.mode, drag.clip, deltaSec, drag.stemDuration, videoDuration, fps);
    setClipDraft({ soundTrackId: drag.soundTrackId, clip: drag.latest });
    event.stopPropagation(); // the lane would otherwise seek the playhead
  }

  // One request per drag, on release; the draft stays until the saved asset has replaced it.
  async function endClipDrag(commit: boolean) {
    const drag = clipDragRef.current;
    clipDragRef.current = null;
    if (!drag || !commit || JSON.stringify(drag.latest) === JSON.stringify(drag.clip)) {
      setClipDraft(null);
      return;
    }
    try {
      await onEditClip?.(drag.soundTrackId, drag.latest);
    } catch {
      // The caller reports the failure; the clip snaps back to the saved one.
    } finally {
      setClipDraft(null);
    }
  }

  // mode "draw" starts on the empty lane, the others on a span's body or edge handle.
  function startDraftDrag(event: PointerEvent<HTMLElement>, mode: SpanDrag | "draw", index = -1) {
    const lane = event.currentTarget.closest(".lane-track");
    if (!(lane instanceof HTMLElement) || !draftSpans) {
      return;
    }
    const rect = lane.getBoundingClientRect();
    const handle = event.target instanceof HTMLElement ? event.target.dataset.handle : undefined;
    draftDragRef.current = {
      mode: mode === "draw" ? mode : handle === "left" ? "resize-left" : handle === "right" ? "resize-right" : "move",
      index,
      startX: event.clientX,
      laneLeft: rect.left,
      laneWidth: rect.width,
      spans: draftSpans,
      latest: draftSpans,
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
    event.preventDefault();
    event.stopPropagation();
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
        <div className="lane-label-sp edit-mode">
          <div className="modes" role="radiogroup" aria-label="Timeline edit mode">
            {EDIT_MODES.map(({ mode, label }) => {
              const blocked = mode === "audio" && editBlockedReason !== null;
              return (
                <button
                  aria-checked={editMode === mode}
                  className="btn sm"
                  disabled={blocked}
                  key={mode}
                  onClick={() => setEditMode(mode)}
                  role="radio"
                  title={blocked ? (editBlockedReason ?? undefined) : undefined}
                  type="button"
                >
                  {label}
                </button>
              );
            })}
          </div>
        </div>
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
                      const style = barStyle(bar, timelineEndFrame);
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
                    onClick={(event) => {
                      event.stopPropagation(); // the lane would otherwise select this track
                      onPlayTrackAudio?.(id, clampedFrame);
                    }}
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
                    onClick={(event) => {
                      event.stopPropagation();
                      onToggleSolo(id);
                    }}
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
                {audioTrack ? (
                  <ClipBlock
                    audioTrack={audioTrack}
                    clip={
                      clipDraft?.soundTrackId === id
                        ? clipDraft.clip
                        : resolveClip(soundTrack, audioTrack.duration_sec)
                    }
                    editable={editMode === "audio" && !!onEditClip && editBlockedReason === null}
                    interactive={editMode === "audio"}
                    onPointerDown={(event, clip) => startClipDrag(event, id, clip, audioTrack.duration_sec)}
                    onPointerMove={moveClipDrag}
                    onPointerUp={() => void endClipDrag(true)}
                    onPointerCancel={() => void endClipDrag(false)}
                    timelineSec={timelineSec}
                  />
                ) : editMode === "audio" ? (
                  <span className="clip-none">No audio yet</span>
                ) : null}
                {(editMode === "spans" ? laneRows : []).map((row, index) => {
                  const bar = barStyle(row, timelineEndFrame);
                  const editable = !!row.sound_event_id && !!onEditSoundEvent;
                  return (
                    <button
                      aria-label={`Edit ${row.label} event, frames ${row.start_frame} to ${row.end_frame}`}
                      className="ev"
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
      {draftSpans ? (
        <div className="lane draft">
          <div className="lane-head">
            <span className="lh-name">
              <span aria-hidden="true" className="sw sw-none" />
              New track
            </span>
          </div>
          <div
            aria-label="New track spans"
            className="lane-track"
            onPointerDown={(event) => startDraftDrag(event, "draw")}
            role="group"
            title="Drag to add a span"
          >
            {draftSpans.map((span, index) => {
              const bar = barStyle(span, timelineEndFrame);
              return (
                <button
                  aria-label={`New track span ${index + 1}, frames ${span.start_frame} to ${span.end_frame}`}
                  className="ev"
                  key={index}
                  onDoubleClick={() => onDraftSpansChange?.(removeSpan(draftSpans, index))}
                  onPointerDown={(event) => startDraftDrag(event, "move", index)}
                  style={{ left: `${bar.left}%`, width: `${bar.width}%` }}
                  tabIndex={-1}
                  title="Drag to move; drag an edge to resize; double-click to remove"
                  type="button"
                >
                  <span aria-hidden="true" className="ev-handle ev-handle-left" data-handle="left" />
                  <span aria-hidden="true" className="ev-handle ev-handle-right" data-handle="right" />
                </button>
              );
            })}
            <span className="playhead" style={{ left: playheadLeft }} />
          </div>
        </div>
      ) : null}
      {onAddTrack ? (
        <div className="tl-add">
          <button
            className="btn sm"
            disabled={trackBlockedReason !== null}
            onClick={() => {
              setEditMode("spans"); // the draft lane is edited like spans, not like audio clips
              onAddTrack();
            }}
            title={trackBlockedReason ?? undefined}
            type="button"
          >
            Add track
          </button>
        </div>
      ) : null}
    </section>
  );
}

interface ClipBlockProps {
  audioTrack: AudioTrackArtifact;
  clip: Clip;
  timelineSec: number;
  // Edit-audio mode: the clip is a draggable block; otherwise it is only the lane's background waveform.
  interactive: boolean;
  editable: boolean;
  onPointerDown: (event: PointerEvent<HTMLElement>, clip: Clip) => void;
  onPointerMove: (event: PointerEvent<HTMLElement>) => void;
  onPointerUp: () => void;
  onPointerCancel: () => void;
}

// The track's audio as placed on the timeline: the raw peaks cut to the clip, drawn where it plays.
function ClipBlock({ audioTrack, clip, timelineSec, interactive, editable, ...handlers }: ClipBlockProps) {
  const place = clipPlacement(clip, timelineSec);
  const style = { left: `${place.left}%`, width: `${place.width}%` };
  // Stable while the clip and the stem are, so Waveform is not rebuilt at video frame rate.
  const peaks = useMemo(
    () => clipPeaks(audioTrack.waveform_peaks, clip, audioTrack.duration_sec),
    [audioTrack.waveform_peaks, audioTrack.duration_sec, clip.start, clip.end],
  );
  const wave = <Waveform peaks={peaks} />;
  if (!interactive) {
    return (
      <div aria-hidden="true" className="clip-wave" style={style}>
        {wave}
      </div>
    );
  }
  const trimmed = !isFullClip(clip, audioTrack.duration_sec);
  return (
    <div
      aria-label={`${audioTrack.track_label} audio clip, ${clip.start.toFixed(2)} to ${clip.end.toFixed(2)} s, starts at ${(clip.start + clip.offset).toFixed(2)} s`}
      className={`clip${editable ? "" : " locked"}${trimmed ? " trimmed" : ""}`}
      onPointerCancel={editable ? handlers.onPointerCancel : undefined}
      onPointerDown={editable ? (event) => handlers.onPointerDown(event, clip) : undefined}
      onPointerMove={editable ? handlers.onPointerMove : undefined}
      onPointerUp={editable ? handlers.onPointerUp : undefined}
      role="group"
      style={style}
      title={editable ? "Drag to move; drag an edge to trim" : undefined}
    >
      {wave}
      {editable ? (
        <>
          <span aria-hidden="true" className="clip-h clip-h-left" data-handle="left" />
          <span aria-hidden="true" className="clip-h clip-h-right" data-handle="right" />
        </>
      ) : null}
    </div>
  );
}

const Waveform = memo(function Waveform({ peaks }: { peaks: number[] }) {
  const polygon = useMemo(() => {
    if (peaks.length === 0) {
      return null;
    }
    const sampled = peaks.length > 384 ? samplePeaks(peaks, 384) : peaks;
    const top = sampled.map((peak, index) => `${index},${(12 - clamp(peak, 0, 1) * 11.5).toFixed(1)}`);
    const bottom = sampled
      .map((peak, index) => `${index},${(12 + clamp(peak, 0, 1) * 11.5).toFixed(1)}`)
      .reverse();
    return { points: [...top, ...bottom].join(" "), width: Math.max(sampled.length - 1, 1) };
  }, [peaks]);
  if (!polygon) {
    return null;
  }
  return (
    <svg
      aria-hidden="true"
      className="wave"
      preserveAspectRatio="none"
      viewBox={`0 0 ${polygon.width} 24`}
    >
      <polygon points={polygon.points} />
    </svg>
  );
});

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
  row: { start_frame: number; end_frame: number },
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
