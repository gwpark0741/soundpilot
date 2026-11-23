import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import OpenVideoDialog from "./OpenVideoDialog";
import Timeline from "./Timeline";
import SelectedTrackPanel from "./SelectedTrackPanel";
import AnalysisPanel from "./AnalysisPanel";
import { summarizeScenes } from "../lib/analysis";
import Stage from "./Stage";
import TopBar from "./TopBar";
import { fetchAssetForExport, fetchTrackingWindow } from "../api";
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
  ) => Promise<void> | void;
  onSetAudioMode?: (mode: "generate" | "separate") => Promise<void> | void;
  onSetResidual?: (enabled: boolean) => Promise<void> | void;
}

export function regenerateBlockedReason(
  hasTimelineEdits: boolean,
  status: string,
  stage?: string | null,
): string | null {
  if (hasTimelineEdits) {
    return "Unsaved timeline edits. Generate audio to apply them or discard.";
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
  onResetSoundTimeline,
  onGenerateAudio,
  onRegenerateEventAudio,
  onRegenerateTrack,
  onSetAudioMode,
  onSetResidual,
}: VideoEditorProps) {
  const [frame, setFrame] = useState(0);
  const [selectedTrackId, setSelectedTrackId] = useState<string | null>(null);
  const [soloTrackId, setSoloTrackId] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [serverUrl, setServerUrl] = useState("");
  const [panelError, setPanelError] = useState<string | null>(null);
  // submitError as it was when the dialog opened: it came from a non-dialog action, so don't show it there.
  const [staleDialogError, setStaleDialogError] = useState<string | null>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [trackWindow, setTrackWindow] = useState<TrackWindowResponse | null>(null);
  const [timelineRows, setTimelineRows] = useState<TimelineRow[]>(state.timeline_rows);
  const [baseAsset, setBaseAsset] = useState<VideoAsset | null>(null);
  const [draftAsset, setDraftAsset] = useState<VideoAsset | null>(null);
  const [hasTimelineEdits, setHasTimelineEdits] = useState(false);
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
  const audioTrackArtifacts = useMemo<AudioTrackArtifact[]>(
    () =>
      hasTimelineEdits
        ? []
        : (baseAsset?.sound_track_audio_artifacts ?? state.audio_tracks ?? []),
    [baseAsset, hasTimelineEdits, state.audio_tracks],
  );
  const selectFrame = useCallback(
    (nextFrame: number) => {
      stopPreviewAudio(true);
      const next = clamp(nextFrame, 0, maxFrame);
      setFrame(next);
      const videoElement = videoRef.current;
      if (videoElement) {
        videoElement.currentTime = next / fps;
      }
    },
    [fps, maxFrame],
  );

  useEffect(() => {
    if (hasTimelineEdits) {
      return;
    }
    setTimelineRows(state.timeline_rows);
    let alive = true;
    void fetchAssetForExport()
      .then((asset) => {
        if (!alive) {
          return;
        }
        setBaseAsset(asset);
        setDraftAsset(asset ? cloneAsset(asset) : null);
      })
      .catch(() => {
        if (alive) {
          setBaseAsset(null);
          setDraftAsset(null);
        }
      });
    return () => {
      alive = false;
    };
  }, [state.timeline_rows, state.version]);

  // A new run clears the asset (and a new video replaces it): drop everything tied to the old project,
  // or a stale "unsaved edits" flag would freeze the refresh above.
  const videoId = video?.video_id;
  useEffect(() => {
    stopPreviewAudio(true);
    setFrame(0);
    setSelectedTrackId(null);
    setSoloTrackId(null);
    setTrackWindow(null);
    trackWindowRequestRef.current = null;
    trackWindowAssetRef.current = null;
    setTimelineRows(state.timeline_rows);
    setBaseAsset(null);
    setDraftAsset(null);
    setHasTimelineEdits(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [videoId]);

  useEffect(() => {
    return () => {
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
    setSelectedTrackId((current) =>
      reconcileSelection(current, soundTracks.map((t) => t.sound_track_id)),
    );
    setSoloTrackId((current) =>
      current && !soundTracks.some((t) => t.sound_track_id === current) ? null : current,
    );
  }, [selectedTrackId, soundTracks]);

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

    if (videoElement) {
      void videoElement.play().catch(() => {
        cleanup();
      });
    }
    void audio.play().catch(() => {
      cleanup();
    });
  }

  function playTrackAudio(soundTrackId: string, startFrame: number) {
    playSyncedAudio(trackAudioUrl(soundTrackId), startFrame / fps, startFrame);
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
    if (!onRegenerateEventAudio || regenerateBlockedReason(hasTimelineEdits, state.status, state.stage)) {
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
    setHasTimelineEdits(false);
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
      playSyncedAudio(trackAudioUrl(next), currentFrame / fps, currentFrame);
    } else {
      stopPreviewAudio(true);
      void video.play();
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
      playSyncedAudio(trackAudioUrl(soloTrackId), selectedFrame / fps, selectedFrame);
    } else if (video) {
      stopPreviewAudio(true);
      void video.play();
    }
  }

  const editSoundEventTimestamp = useCallback(
    (soundEventId: string, startFrame: number, endFrame: number) => {
      const nextStart = clamp(startFrame, 0, Math.max(0, maxFrame));
      const nextEnd = clamp(endFrame, nextStart + 1, timelineFrameCount || 1);
      const current = timelineRows.find((row) => row.sound_event_id === soundEventId);
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
      setDraftAsset((currentAsset) =>
        currentAsset
          ? updateSoundEventInAsset(currentAsset, soundEventId, (event) => ({
              ...event,
              start_frame_index: nextStart,
              end_frame_index: nextEnd,
            }))
          : currentAsset,
      );
      setHasTimelineEdits(true);
    },
    [maxFrame, timelineFrameCount, timelineRows],
  );

  function resetTimestampEdits() {
    setTimelineRows(state.timeline_rows);
    setDraftAsset(baseAsset ? cloneAsset(baseAsset) : null);
    setHasTimelineEdits(false);
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
    setHasTimelineEdits(true);
    return true;
  }

  async function changeAudioMode(mode: "generate" | "separate") {
    try {
      await onSetAudioMode?.(mode);
    } catch {
      return;
    }
    // Unsaved timeline edits keep the loaded draft, which Generate audio sends back: it must carry the new mode.
    setBaseAsset((asset) => (asset ? { ...asset, audio_mode: mode } : asset));
    setDraftAsset((asset) => (asset ? { ...asset, audio_mode: mode } : asset));
  }

  async function submitGenerateAudio() {
    if (!onGenerateAudio) {
      return;
    }
    const committedAsset = draftAsset
      ? clearGeneratedAudio(applyTimelineEdits(draftAsset, timelineRows))
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
    setHasTimelineEdits(false);
  }

  function exportEditedAsset() {
    if (!draftAsset) {
      return;
    }
    downloadJson(applyTimelineEdits(draftAsset, timelineRows), "video-asset-edited.json");
    setHasTimelineEdits(false);
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
    setDraftAsset(null);
    setHasTimelineEdits(false);
  }

  const mp4Ready = state.status === "complete" && !!baseAsset?.synthesized_video_path && !hasTimelineEdits;
  const mp4Href = `/api/synthesized-video?asset_version=${state.asset_version}`;

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
              videoSrc={mp4Ready ? mp4Href : `/api/video?asset_version=${state.asset_version}`}
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
        />
        {selectedTrack ? (
          <SelectedTrackPanel
            key={selectedTrack.sound_track_id}
            track={selectedTrack}
            artifact={audioTrackArtifacts.find((a) => a.sound_track_id === selectedTrack.sound_track_id)}
            status={baseAsset?.track_status?.[selectedTrack.sound_track_id]}
            appRunning={state.status === "running"}
            blockedReason={regenerateBlockedReason(hasTimelineEdits, state.status, state.stage)}
            hasAudio={baseAsset?.has_audio ?? false}
            sourceType={soundSources?.find((source) => source.sound_source_id === selectedTrack.sound_source_id)?.source_type}
            residualEnabled={baseAsset?.residual_enabled ?? false}
            objectColor={highlightColor}
            error={panelError}
            hasUnsavedEdits={hasTimelineEdits}
            onDiscardEdits={resetTimestampEdits}
            onRegenerate={(method, description) =>
              runPanelAction(() => onRegenerateTrack?.(selectedTrack.sound_track_id, method, description))
            }
            onSetResidual={(enabled) => runPanelAction(() => onSetResidual?.(enabled))}
            events={(draftAsset?.sound_timeline?.sound_events ?? []).filter(
              (event) => event.sound_track_id === selectedTrack.sound_track_id,
            )}
            onRegenerateEvent={(id, description, spokenText) =>
              void regenerateEventFromPanel(id, description, spokenText)
            }
          />
        ) : sceneSummaries.length ? (
          <AnalysisPanel
            scenes={sceneSummaries}
            fps={fps}
            frame={selectedFrame}
            soundTrackCount={soundTracks.length}
            onSelectFrame={selectFrame}
          />
        ) : null}
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

function applyTimelineEdits(asset: VideoAsset, rows: TimelineRow[]): VideoAsset {
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
  timeline.sound_events = timeline.sound_events.map((event) => {
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
