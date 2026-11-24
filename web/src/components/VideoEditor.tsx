import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import NewTrackPanel from "./NewTrackPanel";
import OpenVideoDialog from "./OpenVideoDialog";
import Timeline from "./Timeline";
import SelectedTrackPanel from "./SelectedTrackPanel";
import AnalysisPanel from "./AnalysisPanel";
import { summarizeScenes } from "../lib/analysis";
import Stage from "./Stage";
import TopBar from "./TopBar";
import { fetchAssetForExport, fetchTrackingWindow, type NewTrack } from "../api";
import { isFullClip, type Clip } from "../lib/clip";
import { gainToElementVolume } from "../lib/gain";
import { newTrackSpan } from "../lib/newTrack";
import { removeSpan, type Span } from "../lib/span";
import {
  isRemoved,
  reapplyPending,
  removeEvent,
  trackEventSpans,
  withoutEvents,
  type EventSpan,
  type PendingSpans,
} from "../lib/pendingEdits";
import { buildObjects } from "../lib/objects";
import { trackWindowNeedsFetch } from "../lib/trackWindow";
import { fallbackMatchLabel, highlightFor, objectColor, reconcileSelection, soloToggleAction, transportAction } from "../lib/selection";
import type {
  AssetResponse,
  AudioTrackArtifact,
  SoundEvent,
  TimelineRow,
  TrackWindowResponse,
  VideoAsset,
} from "../types";

interface VideoEditorProps {
  state: AssetResponse;
  submitError: string | null;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onImport: (event: FormEvent<HTMLFormElement>) => void;
  onClean: () => Promise<void> | void;
  onOpenProject: (id: string) => Promise<void> | void;
  onDeleteProject: (id: string) => Promise<void> | void;
  onResetSoundTimeline: () => void;
  onGenerateAudio?: (serverUrl: string, draftAsset: VideoAsset | null) => Promise<void> | void;
  onRegenerateEventAudio?: (
    soundEventId: string,
    description: string,
    spokenText: string | null,
    serverUrl: string | null,
  ) => Promise<void> | void;
  onRegenerateTrack?: (
    trackId: string,
    method: "separate" | "generate",
    description: string | null,
    events?: EventSpan[],
    model?: "selsync" | "t2a" | "tts",
  ) => Promise<void> | void;
  onSetAudioMode?: (mode: "generate" | "separate") => Promise<void> | void;
  onSetResidual?: (enabled: boolean) => Promise<void> | void;
  onSetTrackClip?: (trackId: string, clip: Clip) => Promise<void> | void;
  onSetTrackGain?: (trackId: string, gain: number) => Promise<void> | void;
  // Keeps the track's audio only inside its span boxes; the events are the track's pending span edits.
  onTrimTrack?: (trackId: string, events?: EventSpan[]) => Promise<void> | void;
  onUndoTrim?: (trackId: string) => Promise<void> | void;
  onDeleteTrack?: (trackId: string) => Promise<void> | void;
  // Adds the track and starts generating its audio.
  onCreateTrack?: (track: NewTrack) => Promise<string | void> | void;
}

export function regenerateBlockedReason(
  hasTimelineEdits: boolean,
  status: string,
  stage?: string | null,
): string | null {
  if (hasTimelineEdits) {
    return "Edit audio is off while span edits are pending. Apply them with Regenerate with new spans or Trim to spans on the edited track, or discard them.";
  }
  if (status === "running") {
    const progress = stage?.match(/(\d+)\s*(?:\/|of)\s*(\d+)/);
    return progress
      ? `Generating ${progress[1]} of ${progress[2]}. Actions are unavailable until it finishes.`
      : "Generation is running. Actions are unavailable until it finishes.";
  }
  return null;
}

export default function VideoEditor({
  state,
  submitError,
  onSubmit,
  onImport,
  onClean,
  onOpenProject,
  onDeleteProject,
  onResetSoundTimeline,
  onGenerateAudio,
  onRegenerateEventAudio,
  onRegenerateTrack,
  onSetAudioMode,
  onSetResidual,
  onSetTrackClip,
  onSetTrackGain,
  onTrimTrack,
  onUndoTrim,
  onDeleteTrack,
  onCreateTrack,
}: VideoEditorProps) {
  const [frame, setFrame] = useState(0);
  const [selectedTrackId, setSelectedTrackId] = useState<string | null>(null);
  const [soloTrackId, setSoloTrackId] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  // Spans of the new track being drafted (local only); null when there is no draft.
  const [draftSpans, setDraftSpans] = useState<Span[] | null>(null);
  // A track just added from the panel is selected as soon as it shows up in the timeline.
  const [pendingSelect, setPendingSelect] = useState<string | null>(null);
  const [serverUrl, setServerUrl] = useState("");
  const [panelError, setPanelError] = useState<string | null>(null);
  // submitError as it was when the dialog opened: it came from a non-dialog action, so don't show it there.
  const [staleDialogError, setStaleDialogError] = useState<string | null>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [trackWindow, setTrackWindow] = useState<TrackWindowResponse | null>(null);
  const [timelineRows, setTimelineRows] = useState<TimelineRow[]>(state.timeline_rows);
  const [baseAsset, setBaseAsset] = useState<VideoAsset | null>(null);
  // The preview URL is set together with the asset it came from, so it never names a file the asset lacks.
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [draftAsset, setDraftAsset] = useState<VideoAsset | null>(null);
  // Span edits not sent to the server yet, per event; the timeline rows already show them.
  const [pendingSpans, setPendingSpans] = useState<PendingSpans>({});
  const hasTimelineEdits = Object.keys(pendingSpans).length > 0;
  const [reloadKey, setReloadKey] = useState(0);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const audioPreviewRef = useRef<HTMLAudioElement | null>(null);
  const audioPreviewMutedRestoreRef = useRef<boolean | null>(null);
  const audioCacheRef = useRef<Map<string, HTMLAudioElement>>(new Map());
  const animationRef = useRef<number | null>(null);
  const trackWindowRequestRef = useRef<string | null>(null);
  const trackWindowAssetRef = useRef<{ assetVersion: number; trackingCount: number } | null>(null);
  const video = state.video;
  const fps = video?.fps || 30;
  const timelineFrameCount = Math.max(video?.frame_count ?? 0, inferFrameCount(timelineRows));
  const maxFrame = Math.max(0, timelineFrameCount - 1);
  const selectedFrame = clamp(frame, 0, maxFrame);
  const soundTracks = useMemo(
    () => draftAsset?.sound_timeline?.sound_tracks ?? [],
    [draftAsset],
  );
  const soundSources = draftAsset?.sound_timeline?.sound_sources;
  const selectedTrack = soundTracks.find((track) => track.sound_track_id === selectedTrackId);
  const highlightSceneTrackIds = highlightFor(selectedTrackId, state.track_targets);
  const objects = useMemo(() => buildObjects(timelineRows, draftAsset), [timelineRows, draftAsset]);
  const sceneSummaries = useMemo(
    () => summarizeScenes(timelineRows, objects.sceneTrackColors),
    [timelineRows, objects.sceneTrackColors],
  );
  const trackColors: Record<string, string> = {};
  for (const track of soundTracks) {
    trackColors[track.sound_track_id] = objects.soundTrackColor(track, state.track_targets);
  }
  const highlightColor = (selectedTrackId && trackColors[selectedTrackId]) || objectColor(-1);
  const selectedSourceLabel = soundSources?.find(
    (source) => source.sound_source_id === selectedTrack?.sound_source_id,
  )?.label;
  const matchedByName = fallbackMatchLabel(
    trackWindow?.tracks ?? [],
    selectedFrame,
    highlightSceneTrackIds,
    (selectedTrackId && state.track_target_fallbacks?.[selectedTrackId]) || [],
  );
  const highlightLabel = selectedTrack
    ? highlightSceneTrackIds.length > 0
      ? matchedByName
        ? `${matchedByName} (matched by name)`
        : (selectedSourceLabel ?? selectedTrack.label)
      : `${selectedSourceLabel ?? selectedTrack.label} has no object mask`
    : null;
  // Pending span edits do not change the generated audio: its waveforms and Play/Solo stay until Generate.
  const audioTrackArtifacts = useMemo<AudioTrackArtifact[]>(
    () => baseAsset?.sound_track_audio_artifacts ?? state.audio_tracks ?? [],
    [baseAsset, state.audio_tracks],
  );
  // Scrubbing fires many selectFrame calls: the video gets at most one seek per animation frame, none while
  // it is still seeking, and the last target is applied on `seeked`.
  const seekTargetRef = useRef<number | null>(null);
  const seekRafRef = useRef<number | null>(null);
  function flushSeek() {
    seekRafRef.current = null;
    const videoElement = videoRef.current;
    if (!videoElement || seekTargetRef.current === null || videoElement.seeking) {
      return;
    }
    videoElement.currentTime = seekTargetRef.current;
    seekTargetRef.current = null;
  }
  const selectFrame = useCallback(
    (nextFrame: number) => {
      stopPreviewAudio(true);
      const next = clamp(nextFrame, 0, maxFrame);
      setFrame(next);
      seekTargetRef.current = next / fps;
      if (seekRafRef.current === null) {
        seekRafRef.current = window.requestAnimationFrame(flushSeek);
      }
    },
    [fps, maxFrame],
  );

  const pendingSpansRef = useRef(pendingSpans);
  pendingSpansRef.current = pendingSpans;

  // Keyed on the store version only: the same version arrives again with a new rows array after every action
  // (the action's own refresh plus the SSE one). asset_version is too coarse, since track status and added
  // tracks change the asset without bumping it.
  // Span edits only live in the rows and the pending map, so the reload may replace the asset; the pending
  // edits of tracks that were not regenerated are put back on top of the server rows.
  useEffect(() => {
    const next = reapplyPending(state.timeline_rows, pendingSpansRef.current);
    setTimelineRows(next.rows);
    if (next.pending !== pendingSpansRef.current) {
      setPendingSpans(next.pending);
    }
    let alive = true;
    const assetVersion = state.asset_version;
    void fetchAssetForExport()
      .then((asset) => {
        if (!alive) {
          return;
        }
        setBaseAsset(asset);
        setPreviewUrl(
          asset?.synthesized_video_path ? `/api/synthesized-video?asset_version=${assetVersion}` : null,
        );
        setDraftAsset(asset ? cloneAsset(asset) : null);
      })
      .catch(() => {
        if (alive) {
          setBaseAsset(null);
          setPreviewUrl(null);
          setDraftAsset(null);
        }
      });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.version, reloadKey]);

  // A new run clears the asset (and a new video replaces it): drop everything tied to the old project.
  const videoId = video?.video_id;
  useEffect(() => {
    stopPreviewAudio(true);
    setFrame(0);
    setSelectedTrackId(null);
    setSoloTrackId(null);
    setDraftSpans(null);
    setTrackWindow(null);
    trackWindowRequestRef.current = null;
    trackWindowAssetRef.current = null;
    setTimelineRows(state.timeline_rows);
    setBaseAsset(null);
    setPreviewUrl(null);
    setDraftAsset(null);
    setPendingSpans({});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [videoId]);

  useEffect(() => {
    return () => {
      if (seekRafRef.current !== null) {
        window.cancelAnimationFrame(seekRafRef.current);
      }
      stopPreviewAudio(true);
      for (const audio of audioCacheRef.current.values()) {
        audio.pause();
      }
      audioCacheRef.current.clear();
    };
  }, []);

  // New audio files: a playing stem preview is stale too, so stop it, not just the cached elements.
  useEffect(() => {
    stopPreviewAudio(false);
    for (const audio of audioCacheRef.current.values()) {
      audio.pause();
    }
    audioCacheRef.current.clear();
  }, [state.asset_version]);

  useEffect(() => {
    if (frame !== selectedFrame) {
      setFrame(selectedFrame);
    }
  }, [frame, selectedFrame]);

  useEffect(() => {
    setPanelError(null);
  }, [selectedTrackId]);

  useEffect(() => {
    if (submitError === null) {
      setStaleDialogError(null);
    }
  }, [submitError]);

  // A new video, loaded project or rerun replaces the tracks: drop ids that no longer exist.
  useEffect(() => {
    const ids = soundTracks.map((t) => t.sound_track_id);
    if (pendingSelect && ids.includes(pendingSelect)) {
      setSelectedTrackId(pendingSelect);
      setPendingSelect(null);
      return;
    }
    setSelectedTrackId((current) => reconcileSelection(current, ids));
    setSoloTrackId((current) =>
      current && !soundTracks.some((t) => t.sound_track_id === current) ? null : current,
    );
  }, [selectedTrackId, soundTracks, pendingSelect]);

  // Tracking rows are published mid-run without an asset_version bump, so their count is part of the key.
  const trackingCount = state.timeline_rows.filter((row) => row.kind === "track").length;
  useEffect(() => {
    if (!video || trackingCount === 0) {
      return;
    }
    const assetVersion = state.asset_version;
    const fetched =
      trackWindow && trackWindowAssetRef.current
        ? { ...trackWindow, ...trackWindowAssetRef.current }
        : null;
    if (!trackWindowNeedsFetch({ window: fetched, frame: selectedFrame, maxFrame, assetVersion, trackingCount })) {
      return;
    }
    const startFrame = Math.max(0, selectedFrame - 300);
    const endFrame = Math.min(maxFrame, selectedFrame + 300);
    const requestKey = `${assetVersion}:${trackingCount}:${startFrame}:${endFrame}`;
    if (trackWindowRequestRef.current === requestKey) {
      return;
    }
    trackWindowRequestRef.current = requestKey;
    void fetchTrackingWindow(startFrame, endFrame)
      .then((window) => {
        trackWindowAssetRef.current = { assetVersion, trackingCount };
        setTrackWindow(window);
      })
      .catch(() => {
        // Allow a retry; a successful key stays so the same window is not requested twice.
        trackWindowRequestRef.current = null;
      });
  }, [maxFrame, selectedFrame, state.asset_version, trackingCount, trackWindow, video]);

  useEffect(() => {
    if (!isPlaying) {
      if (animationRef.current !== null) {
        window.cancelAnimationFrame(animationRef.current);
        animationRef.current = null;
      }
      return;
    }
    const sync = () => {
      const videoElement = videoRef.current;
      if (videoElement) {
        setFrame(clamp(Math.round(videoElement.currentTime * fps), 0, maxFrame));
      }
      animationRef.current = window.requestAnimationFrame(sync);
    };
    animationRef.current = window.requestAnimationFrame(sync);
    return () => {
      if (animationRef.current !== null) {
        window.cancelAnimationFrame(animationRef.current);
        animationRef.current = null;
      }
    };
  }, [fps, isPlaying, maxFrame]);

  function syncFrameFromVideo() {
    const video = videoRef.current;
    if (!video) {
      return;
    }
    if (seekTargetRef.current !== null) {
      // A scrub target is waiting for the previous seek to finish.
      flushSeek();
      return;
    }
    if (!video.paused) {
      return; // playing: the animation-frame loop above keeps the frame in sync
    }
    setFrame(clamp(Math.round(video.currentTime * fps), 0, maxFrame));
  }

  function stopPreviewAudio(pauseVideo: boolean) {
    const audio = audioPreviewRef.current;
    if (audio) {
      audio.onended = null;
      audio.onerror = null;
      audio.pause();
      audioPreviewRef.current = null;
    }

    const videoElement = videoRef.current;
    if (videoElement && audioPreviewMutedRestoreRef.current !== null) {
      videoElement.muted = audioPreviewMutedRestoreRef.current;
      if (pauseVideo) {
        videoElement.pause();
      }
    }
    audioPreviewMutedRestoreRef.current = null;
  }

  function trackAudioUrl(soundTrackId: string): string {
    return `/api/audio/tracks/${soundTrackId}?asset_version=${state.asset_version}`;
  }

  function preloadAudio(url: string): HTMLAudioElement {
    let audio = audioCacheRef.current.get(url);
    if (!audio) {
      audio = new Audio(url);
      audio.preload = "auto";
      audioCacheRef.current.set(url, audio);
      audio.load();
    }
    return audio;
  }

  function playSyncedAudio(url: string, audioStartTime: number, videoStartFrame: number) {
    const videoElement = videoRef.current;
    stopPreviewAudio(true);

    const nextFrame = clamp(videoStartFrame, 0, maxFrame);
    const videoStartTime = nextFrame / fps;
    setFrame(nextFrame);

    seekTargetRef.current = null; // the explicit seek below replaces a pending scrub target
    const audio = preloadAudio(url);
    audio.pause();
    try {
      audio.currentTime = Math.max(0, audioStartTime);
    } catch {
      audio.load();
    }
    audioPreviewRef.current = audio;

    if (videoElement) {
      audioPreviewMutedRestoreRef.current = videoElement.muted;
      videoElement.muted = true;
      videoElement.currentTime = videoStartTime;
    }

    const cleanup = () => {
      if (audioPreviewRef.current !== audio) {
        return;
      }
      audio.onended = null;
      audio.onerror = null;
      audio.pause();
      audioPreviewRef.current = null;
      if (videoElement && audioPreviewMutedRestoreRef.current !== null) {
        videoElement.muted = audioPreviewMutedRestoreRef.current;
        videoElement.pause();
      }
      audioPreviewMutedRestoreRef.current = null;
    };

    audio.onended = cleanup;
    audio.onerror = cleanup;

    // A quick second press pauses these elements again, which rejects the pending play() with AbortError;
    // that is not a failure, and cleanup() would stop the newer playback that reuses the cached element.
    const onPlayError = (error: unknown) => {
      if (!(error instanceof DOMException && error.name === "AbortError")) {
        cleanup();
      }
    };
    if (videoElement) {
      void videoElement.play().catch(onPlayError);
    }
    void audio.play().catch(onPlayError);
  }

  // Play/Solo run the raw stem, so the track's gain is applied to the element (capped at 1 by the browser).
  function playTrackSynced(soundTrackId: string, audioStartTime: number, videoStartFrame: number) {
    const url = trackAudioUrl(soundTrackId);
    const gain = soundTracks.find((t) => t.sound_track_id === soundTrackId)?.gain;
    preloadAudio(url).volume = gainToElementVolume(gain);
    playSyncedAudio(url, audioStartTime, videoStartFrame);
  }

  function playTrackAudio(soundTrackId: string, startFrame: number) {
    playTrackSynced(soundTrackId, startFrame / fps, startFrame);
  }

  // Slider drag: the cached element (possibly playing right now) follows the slider before the mix is rebuilt.
  function previewTrackGain(soundTrackId: string, gain: number) {
    const audio = audioCacheRef.current.get(trackAudioUrl(soundTrackId));
    if (audio) {
      audio.volume = gainToElementVolume(gain);
    }
  }

  // Panel actions report a failure next to the control; App already put a summary in the top line.
  function runPanelAction(action: () => Promise<void> | void) {
    setPanelError(null);
    void Promise.resolve()
      .then(action)
      .catch((error) => setPanelError(error instanceof Error ? error.message : String(error)));
  }

  async function regenerateEventFromPanel(
    soundEventId: string,
    description: string,
    spokenText: string | null,
  ) {
    const eventTrackId = timelineRows.find((row) => row.sound_event_id === soundEventId)?.sound_track_id ?? "";
    if (
      !onRegenerateEventAudio ||
      regenerateBlockedReason(false, state.status, state.stage) ||
      trackEventSpans(timelineRows, pendingSpans, eventTrackId).length > 0
    ) {
      return;
    }
    setPanelError(null);
    try {
      await onRegenerateEventAudio(soundEventId, description, spokenText, serverUrl.trim() || null);
    } catch (error) {
      setPanelError(error instanceof Error ? error.message : String(error));
      return;
    }
    submitEditSoundEventDetails(soundEventId, description, spokenText);
  }

  // Solo only counts while that track still has audio.
  function effectiveSolo(id: string | null): string | null {
    return id && audioTrackArtifacts.some((a) => a.sound_track_id === id) ? id : null;
  }

  function playerState() {
    const video = videoRef.current;
    return {
      previewPlaying: !!audioPreviewRef.current && !audioPreviewRef.current.paused,
      videoPlaying: !!video && !video.paused,
    };
  }

  // Solo changes what plays: if something is playing, restart from the current frame with the new choice.
  function toggleSolo(id: string) {
    const next = soloTrackId === id ? null : id;
    setSoloTrackId(next);
    const action = soloToggleAction({ ...playerState(), soloTrackId: effectiveSolo(next) });
    const video = videoRef.current;
    if (!action || !video) {
      return;
    }
    const currentFrame = clamp(Math.round(video.currentTime * fps), 0, maxFrame);
    if (action === "play-solo" && next) {
      playTrackSynced(next, currentFrame / fps, currentFrame);
    } else {
      stopPreviewAudio(true);
      video.play().catch(() => {});
    }
  }

  function togglePlayback() {
    const video = videoRef.current;
    const action = transportAction({
      ...playerState(),
      soloTrackId: effectiveSolo(soloTrackId),
    });
    if (action === "stop-preview") {
      stopPreviewAudio(true);
    } else if (action === "pause-video") {
      video?.pause();
    } else if (action === "play-solo" && soloTrackId) {
      playTrackSynced(soloTrackId, selectedFrame / fps, selectedFrame);
    } else if (video) {
      stopPreviewAudio(true);
      video.play().catch(() => {});
    }
  }

  // The latest rows live in a ref so the callback below keeps its identity while a drag updates them.
  const timelineRowsRef = useRef(timelineRows);
  timelineRowsRef.current = timelineRows;
  // Per tick only the rows change; applyTimelineEdits(draftAsset, timelineRows, pendingSpans) puts them into the asset at Generate/export.
  const editSoundEventTimestamp = useCallback(
    (soundEventId: string, startFrame: number, endFrame: number) => {
      const nextStart = clamp(startFrame, 0, Math.max(0, maxFrame));
      const nextEnd = clamp(endFrame, nextStart + 1, timelineFrameCount || 1);
      const current = timelineRowsRef.current.find((row) => row.sound_event_id === soundEventId);
      if (current && current.start_frame === nextStart && current.end_frame === nextEnd) {
        return;
      }
      setTimelineRows((currentRows) =>
        currentRows.map((row) => {
          if (row.sound_event_id !== soundEventId) {
            return row;
          }
          return {
            ...row,
            start_frame: nextStart,
            end_frame: nextEnd,
          };
        }),
      );
      setPendingSpans((pending) => ({ ...pending, [soundEventId]: { start_frame: nextStart, end_frame: nextEnd } }));
    },
    [maxFrame, timelineFrameCount],
  );

  // Removing a box is a pending edit like a move: the row leaves the timeline, the server drops the event
  // when the track's edits are applied. The last box of a track stays.
  const removeSoundEvent = useCallback((soundEventId: string) => {
    const next = removeEvent(timelineRowsRef.current, pendingSpansRef.current, soundEventId);
    if (!next) {
      return;
    }
    setTimelineRows((currentRows) => currentRows.filter((row) => row.sound_event_id !== soundEventId));
    setPendingSpans(next);
  }, []);

  function resetTimestampEdits() {
    setTimelineRows(state.timeline_rows);
    setDraftAsset(baseAsset ? cloneAsset(baseAsset) : null);
    setPendingSpans({});
  }

  function submitEditSoundEventDetails(
    soundEventId: string,
    description: string,
    spokenText: string | null,
  ): boolean {
    const row = timelineRows.find((item) => item.sound_event_id === soundEventId);
    const track = soundTracks.find(
      (item) => item.sound_track_id === row?.sound_track_id,
    );
    const nextDescription = description.trim();
    const nextSpokenText =
      track?.track_type === "speech" ? spokenText?.trim() || null : null;
    if (
      !row ||
      !nextDescription ||
      (track?.track_type === "speech" && !nextSpokenText)
    ) {
      return false;
    }

    setDraftAsset((currentAsset) =>
      currentAsset
        ? updateSoundEventInAsset(currentAsset, soundEventId, (event) => ({
            ...event,
            description: nextDescription,
            spoken_text: nextSpokenText,
          }))
        : currentAsset,
    );
    setTimelineRows((currentRows) =>
      currentRows.map((item) =>
        item.sound_event_id === soundEventId
          ? {
              ...item,
              label: nextDescription,
              spoken_text: nextSpokenText,
            }
          : item,
      ),
    );
    return true;
  }

  // Trim/move commit from the timeline: the draft shows the new clip until the saved asset replaces it.
  async function commitClip(trackId: string, clip: Clip) {
    if (!onSetTrackClip || regenerateBlockedReason(hasTimelineEdits, state.status, state.stage)) {
      return;
    }
    setPanelError(null);
    try {
      await onSetTrackClip(trackId, clip);
    } catch (error) {
      setPanelError(error instanceof Error ? error.message : String(error));
      return;
    }
    const duration = audioTrackArtifacts.find((a) => a.sound_track_id === trackId)?.duration_sec ?? 0;
    const full = isFullClip(clip, duration);
    setDraftAsset((current) =>
      current?.sound_timeline
        ? {
            ...current,
            sound_timeline: {
              ...current.sound_timeline,
              sound_tracks: current.sound_timeline.sound_tracks.map((track) =>
                track.sound_track_id === trackId
                  ? {
                      ...track,
                      clip_start_sec: full ? null : clip.start,
                      clip_end_sec: full ? null : clip.end,
                      clip_offset_sec: full ? 0 : clip.offset,
                    }
                  : track,
              ),
            },
          }
        : current,
    );
  }

  // Slider release: the draft shows the new gain until the saved asset replaces it, like a clip edit.
  async function commitGain(trackId: string, gain: number) {
    if (!onSetTrackGain || regenerateBlockedReason(hasTimelineEdits, state.status, state.stage)) {
      return;
    }
    setPanelError(null);
    try {
      await onSetTrackGain(trackId, gain);
    } catch (error) {
      previewTrackGain(trackId, soundTracks.find((t) => t.sound_track_id === trackId)?.gain ?? 1);
      setPanelError(error instanceof Error ? error.message : String(error));
      return;
    }
    setDraftAsset((current) =>
      current?.sound_timeline
        ? {
            ...current,
            sound_timeline: {
              ...current.sound_timeline,
              sound_tracks: current.sound_timeline.sound_tracks.map((track) =>
                track.sound_track_id === trackId ? { ...track, gain } : track,
              ),
            },
          }
        : current,
    );
  }

  async function changeAudioMode(mode: "generate" | "separate") {
    // Each mode has its own timeline, so unsaved edits to this one cannot carry over.
    const hadEdits = hasTimelineEdits;
    if (hadEdits && !window.confirm("Switching the audio method discards unsaved timeline edits.")) {
      return;
    }
    try {
      await onSetAudioMode?.(mode);
    } catch {
      return;
    }
    // The server timeline replaces the draft: drop the edits and reload it. Without edits the refresh
    // after the mode change has already loaded it.
    setPendingSpans({});
    if (hadEdits) {
      setReloadKey((key) => key + 1);
    }
  }

  async function submitGenerateAudio() {
    if (!onGenerateAudio) {
      return;
    }
    const committedAsset = draftAsset
      ? clearGeneratedAudio(applyTimelineEdits(draftAsset, timelineRows, pendingSpans))
      : null;
    try {
      await onGenerateAudio(serverUrl, committedAsset);
    } catch {
      return;
    }
    if (committedAsset) {
      setBaseAsset(cloneAsset(committedAsset));
      setDraftAsset(cloneAsset(committedAsset));
    }
    setPendingSpans({});
  }

  function exportEditedAsset() {
    if (!draftAsset) {
      return;
    }
    downloadJson(applyTimelineEdits(draftAsset, timelineRows, pendingSpans), "video-asset-edited.json");
    setPendingSpans({});
  }

  async function cleanWorkspace() {
    if (
      !window.confirm(
        "Clean the current video and generated records from this UI workspace?",
      )
    ) {
      return;
    }
    stopPreviewAudio(true);
    try {
      await onClean();
    } catch {
      return;
    }
    setFrame(0);
    setTrackWindow(null);
    setTimelineRows([]);
    setBaseAsset(null);
    setPreviewUrl(null);
    setDraftAsset(null);
    setPendingSpans({});
  }

  // Span edits block only what changes the audio of the whole timeline (clips); per-track actions and
  // adding or deleting a track wait only for a running job.
  const editBlockedReason = regenerateBlockedReason(hasTimelineEdits, state.status, state.stage);
  const trackBlockedReason = regenerateBlockedReason(false, state.status, state.stage);

  // The selected track's edited spans go with its regenerate; the other tracks' edits stay pending.
  async function regenerateSelectedTrack(
    trackId: string,
    method: "separate" | "generate",
    description: string | null,
    model?: "selsync" | "t2a" | "tts",
  ) {
    const events = trackEventSpans(timelineRows, pendingSpans, trackId);
    await onRegenerateTrack?.(trackId, method, description, events, model);
    if (events.length > 0) {
      setPendingSpans((pending) => withoutEvents(pending, events));
    }
  }

  // Like regenerate, Trim applies this track's pending span edits; the other tracks' edits stay pending.
  async function trimSelectedTrack(trackId: string) {
    const events = trackEventSpans(timelineRows, pendingSpans, trackId);
    await onTrimTrack?.(trackId, events);
    if (events.length > 0) {
      setPendingSpans((pending) => withoutEvents(pending, events));
    }
  }

  async function createTrack(track: NewTrack) {
    const id = await onCreateTrack?.(track);
    setDraftSpans(null);
    if (id) {
      setPendingSelect(id);
    }
  }

  const mp4Ready = state.status === "complete" && !!previewUrl && !hasTimelineEdits;
  const mp4Href = previewUrl ?? "";
  // Changing the player's src reloads it and jumps to 0, so the src follows the files, not the edit state:
  // the previous preview stays while a re-render runs, and a new URL appears only when a run completes.
  const sourceRef = useRef({ videoId: "", original: "", preview: null as string | null });
  const source = sourceRef.current;
  if (source.videoId !== (videoId ?? "")) {
    source.videoId = videoId ?? "";
    source.original = `/api/video?asset_version=${state.asset_version}`;
    source.preview = null;
  }
  if (state.status === "complete") {
    source.preview = previewUrl;
  }

  return (
    <div className="app">
      <TopBar
        hasAudio={video && baseAsset ? (baseAsset.has_audio ?? false) : null}
        state={state}
        submitError={dialogOpen ? null : submitError}
        canGenerate={!!video}
        audioMode={baseAsset?.audio_mode ?? "generate"}
        onSetMode={(mode) => void changeAudioMode(mode)}
        onOpen={() => {
          setStaleDialogError(submitError);
          setDialogOpen(true);
        }}
        onGenerate={() => void submitGenerateAudio()}
      />
      <main className="wrap">
        <section className="stage panel" aria-label="Video">
          {video ? (
            <Stage
              frame={selectedFrame}
              highlightColor={highlightColor}
              showAllObjects={!selectedTrack}
              sceneTrackColors={objects.sceneTrackColors}
              highlightLabel={highlightLabel}
              highlightSceneTrackIds={highlightSceneTrackIds}
              isPlaying={isPlaying}
              maxFrame={maxFrame}
              onSelectFrame={selectFrame}
              onTogglePlay={togglePlayback}
              onVideoPlayState={setIsPlaying}
              onVideoSync={syncFrameFromVideo}
              tracks={trackWindow?.tracks ?? []}
              video={video}
              videoRef={videoRef}
              videoSrc={source.preview ?? source.original}
            />
          ) : (
            <div className="empty-video">Open a video to start</div>
          )}
        </section>
        <Timeline
          rows={timelineRows}
          soundTracks={soundTracks}
          audioTracks={audioTrackArtifacts}
          frame={selectedFrame}
          frameCount={timelineFrameCount}
          onEditSoundEvent={editSoundEventTimestamp}
          onRemoveSoundEvent={removeSoundEvent}
          onPlayTrackAudio={playTrackAudio}
          onSelectFrame={selectFrame}
          fps={fps}
          selectedTrackId={selectedTrackId}
          onSelectTrack={setSelectedTrackId}
          soloTrackId={soloTrackId}
          onToggleSolo={toggleSolo}
          trackStatus={baseAsset?.track_status ?? {}}
          appRunning={state.status === "running"}
          trackColors={trackColors}
          objectLanes={objects.lanes}
          editBlockedReason={editBlockedReason}
          trackBlockedReason={trackBlockedReason}
          onEditClip={commitClip}
          onSetTrackGain={onSetTrackGain ? commitGain : undefined}
          onPreviewTrackGain={previewTrackGain}
          onAddTrack={
            onCreateTrack && draftAsset?.sound_timeline && !draftSpans
              ? () => setDraftSpans([newTrackSpan(selectedFrame, timelineFrameCount, fps)])
              : undefined
          }
          draftSpans={draftSpans ?? undefined}
          onDraftSpansChange={setDraftSpans}
        />
        <div className="side">
        {draftSpans ? (
          <NewTrackPanel
            spans={draftSpans}
            fps={fps}
            hasAudio={baseAsset?.has_audio ?? false}
            blockedReason={trackBlockedReason}
            onRemoveSpan={(index) => setDraftSpans(removeSpan(draftSpans, index))}
            onCancel={() => setDraftSpans(null)}
            onCreate={createTrack}
          />
        ) : selectedTrack ? (
          <SelectedTrackPanel
            key={selectedTrack.sound_track_id}
            track={selectedTrack}
            artifact={audioTrackArtifacts.find((a) => a.sound_track_id === selectedTrack.sound_track_id)}
            status={baseAsset?.track_status?.[selectedTrack.sound_track_id]}
            appRunning={state.status === "running"}
            blockedReason={trackBlockedReason}
            hasAudio={baseAsset?.has_audio ?? false}
            residualEnabled={baseAsset?.residual_enabled ?? false}
            objectColor={highlightColor}
            error={panelError}
            pendingSpans={trackEventSpans(timelineRows, pendingSpans, selectedTrack.sound_track_id).length > 0}
            hasUnsavedEdits={hasTimelineEdits}
            onDiscardEdits={resetTimestampEdits}
            onRegenerate={(method, description, model) =>
              runPanelAction(() => regenerateSelectedTrack(selectedTrack.sound_track_id, method, description, model))
            }
            onSetResidual={(enabled) => runPanelAction(() => onSetResidual?.(enabled))}
            onTrim={onTrimTrack ? () => runPanelAction(() => trimSelectedTrack(selectedTrack.sound_track_id)) : undefined}
            onUndoTrim={onUndoTrim ? () => runPanelAction(() => onUndoTrim(selectedTrack.sound_track_id)) : undefined}
            onDeleteTrack={
              onDeleteTrack ? () => runPanelAction(() => onDeleteTrack(selectedTrack.sound_track_id)) : undefined
            }
            events={(draftAsset?.sound_timeline?.sound_events ?? []).filter(
              (event) =>
                event.sound_track_id === selectedTrack.sound_track_id && !isRemoved(pendingSpans, event.sound_event_id),
            )}
            onRegenerateEvent={(id, description, spokenText) =>
              void regenerateEventFromPanel(id, description, spokenText)
            }
          />
        ) : null}
        {sceneSummaries.length ? (
          <AnalysisPanel
            scenes={sceneSummaries}
            fps={fps}
            frame={selectedFrame}
            soundTrackCount={soundTracks.length}
            onSelectFrame={selectFrame}
          />
        ) : null}
        </div>
        <p className="sr" role="status">
          {selectedTrack ? `Selected: ${selectedTrack.label}` : ""}
        </p>
      </main>
      <OpenVideoDialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        running={state.status === "running"}
        hasVideo={!!video}
        error={submitError === staleDialogError ? null : submitError}
        serverUrl={serverUrl}
        onServerUrlChange={setServerUrl}
        onSubmit={onSubmit}
        onImport={onImport}
        onClean={cleanWorkspace}
        onOpenProject={onOpenProject}
        onDeleteProject={onDeleteProject}
        onResetSoundTimeline={onResetSoundTimeline}
        canDownloadJson={!!draftAsset}
        onDownloadJson={exportEditedAsset}
        hasEdits={hasTimelineEdits}
        onDiscardEdits={resetTimestampEdits}
        videoDownloadHref={mp4Ready ? mp4Href : null}
      />
    </div>
  );
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

function inferFrameCount(rows: TimelineRow[]): number {
  return rows.reduce((maxEnd, row) => Math.max(maxEnd, row.end_frame), 0);
}

function clearGeneratedAudio(asset: VideoAsset): VideoAsset {
  const nextAsset = cloneAsset(asset);
  nextAsset.synthesized_video_path = null;
  nextAsset.sound_event_audio_artifacts = [];
  nextAsset.sound_track_audio_artifacts = [];
  return nextAsset;
}

function applyTimelineEdits(asset: VideoAsset, rows: TimelineRow[], pending: PendingSpans): VideoAsset {
  const editedAsset = cloneAsset(asset);
  const editedRows = new Map(
    rows
      .filter((row) => row.sound_event_id)
      .map((row) => [row.sound_event_id, row]),
  );
  const timeline = editedAsset.sound_timeline;
  if (!timeline) {
    return editedAsset;
  }
  timeline.sound_events = timeline.sound_events
    .filter((event) => !isRemoved(pending, event.sound_event_id))
    .map((event) => {
      const row = editedRows.get(event.sound_event_id);
      if (!row) {
        return event;
      }
      return {
        ...event,
        start_frame_index: row.start_frame,
        end_frame_index: row.end_frame,
        description: row.label,
        spoken_text: row.spoken_text ?? event.spoken_text,
      };
    });
  return editedAsset;
}

function updateSoundEventInAsset(
  asset: VideoAsset,
  soundEventId: string,
  update: (event: SoundEvent) => SoundEvent,
): VideoAsset {
  const nextAsset = cloneAsset(asset);
  const timeline = nextAsset.sound_timeline;
  if (!timeline) {
    return nextAsset;
  }
  timeline.sound_events = timeline.sound_events.map((event) =>
    event.sound_event_id === soundEventId ? update(event) : event,
  );
  return nextAsset;
}

function cloneAsset(asset: VideoAsset): VideoAsset {
  return typeof structuredClone === "function"
    ? structuredClone(asset)
    : JSON.parse(JSON.stringify(asset));
}

function downloadJson(value: unknown, filename: string): void {
  const blob = new Blob([JSON.stringify(value, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
