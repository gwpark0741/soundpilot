import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import OpenVideoDialog from "./OpenVideoDialog";
import Timeline from "./Timeline";
import SelectedTrackPanel from "./SelectedTrackPanel";
import Stage from "./Stage";
import TopBar from "./TopBar";
import { fetchAssetForExport, fetchTrackingWindow } from "../api";
import { highlightFor, objectColor, transportAction } from "../lib/selection";
import type {
  AssetResponse,
  AudioEventArtifact,
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
    method: "separate" | "selsync",
    description: string | null,
  ) => Promise<void> | void;
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
  onSetResidual,
}: VideoEditorProps) {
  const [frame, setFrame] = useState(0);
  const [selectedTrackId, setSelectedTrackId] = useState<string | null>(null);
  const [soloTrackId, setSoloTrackId] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [serverUrl, setServerUrl] = useState("");
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
  // Colour index counts only sources that have an object mask, in source order.
  const maskedSourceIds = (soundSources ?? [])
    .map((source) => source.sound_source_id)
    .filter((id) =>
      soundTracks.some(
        (track) =>
          track.sound_source_id === id &&
          highlightFor(track.sound_track_id, state.track_targets).length > 0,
      ),
    );
  const trackColors: Record<string, string> = {};
  for (const track of soundTracks) {
    trackColors[track.sound_track_id] = objectColor(
      highlightFor(track.sound_track_id, state.track_targets).length > 0 && track.sound_source_id
        ? maskedSourceIds.indexOf(track.sound_source_id)
        : -1,
    );
  }
  const highlightColor = (selectedTrackId && trackColors[selectedTrackId]) || objectColor(-1);
  const selectedSourceLabel = soundSources?.find(
    (source) => source.sound_source_id === selectedTrack?.sound_source_id,
  )?.label;
  const highlightLabel = selectedTrack
    ? highlightSceneTrackIds.length > 0
      ? (selectedSourceLabel ?? selectedTrack.label)
      : `${selectedSourceLabel ?? selectedTrack.label} has no object mask`
    : null;
  const audioTrackArtifacts = useMemo<AudioTrackArtifact[]>(
    () =>
      hasTimelineEdits
        ? []
        : (baseAsset?.sound_track_audio_artifacts ?? state.audio_tracks ?? []),
    [baseAsset, hasTimelineEdits, state.audio_tracks],
  );
  const audioEventArtifacts = useMemo<AudioEventArtifact[]>(
    () => baseAsset?.sound_event_audio_artifacts ?? state.audio_events ?? [],
    [baseAsset, state.audio_events],
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

  useEffect(() => {
    return () => {
      stopPreviewAudio(true);
      for (const audio of audioCacheRef.current.values()) {
        audio.pause();
      }
      audioCacheRef.current.clear();
    };
  }, []);

  useEffect(() => {
    for (const audio of audioCacheRef.current.values()) {
      audio.pause();
    }
    audioCacheRef.current.clear();
    for (const artifact of audioEventArtifacts) {
      preloadAudio(eventAudioUrl(artifact.sound_event_id));
    }
  }, [audioEventArtifacts, state.asset_version]);

  useEffect(() => {
    if (frame !== selectedFrame) {
      setFrame(selectedFrame);
    }
  }, [frame, selectedFrame]);

  useEffect(() => {
    if (selectedTrackId === null && soundTracks.length > 0) {
      setSelectedTrackId(soundTracks[0].sound_track_id);
    }
  }, [selectedTrackId, soundTracks]);

  useEffect(() => {
    if (!selectedTrackId || !video) {
      return;
    }
    if (
      trackWindow &&
      trackWindow.version === state.version &&
      selectedFrame >= trackWindow.start_frame + 60 &&
      selectedFrame <= trackWindow.end_frame - 60
    ) {
      return;
    }
    const startFrame = Math.max(0, selectedFrame - 300);
    const endFrame = Math.min(maxFrame, selectedFrame + 300);
    const requestKey = `${state.version}:${startFrame}:${endFrame}`;
    if (trackWindowRequestRef.current === requestKey) {
      return;
    }
    trackWindowRequestRef.current = requestKey;
    void fetchTrackingWindow(startFrame, endFrame)
      .then(setTrackWindow)
      .finally(() => {
        trackWindowRequestRef.current = null;
      });
  }, [maxFrame, selectedFrame, selectedTrackId, state.version, trackWindow, video]);

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

  function eventAudioUrl(soundEventId: string): string {
    return `/api/audio/events/${soundEventId}?asset_version=${state.asset_version}`;
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

  async function regenerateEventFromPanel(
    soundEventId: string,
    description: string,
    spokenText: string | null,
  ) {
    if (!onRegenerateEventAudio || regenerateBlockedReason(hasTimelineEdits, state.status, state.stage)) {
      return;
    }
    try {
      await onRegenerateEventAudio(soundEventId, description, spokenText, null);
    } catch {
      // App reports the failure through submitError.
      return;
    }
    submitEditSoundEventDetails(soundEventId, description, spokenText);
    setHasTimelineEdits(false);
  }

  function togglePlayback() {
    const video = videoRef.current;
    const action = transportAction({
      previewPlaying: !!audioPreviewRef.current && !audioPreviewRef.current.paused,
      videoPlaying: !!video && !video.paused,
      // Solo only counts while that track still has audio.
      soloTrackId:
        soloTrackId && audioTrackArtifacts.some((a) => a.sound_track_id === soloTrackId)
          ? soloTrackId
          : null,
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
    [maxFrame, timelineFrameCount],
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
        onOpen={() => setDialogOpen(true)}
        onGenerate={() => void submitGenerateAudio()}
      />
      <main className="wrap">
        <section className="stage panel" aria-label="Video">
          {video ? (
            <Stage
              frame={selectedFrame}
              highlightColor={highlightColor}
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
          assetVersion={state.asset_version}
          frame={selectedFrame}
          frameCount={timelineFrameCount}
          onEditSoundEvent={editSoundEventTimestamp}
          onPlayTrackAudio={playTrackAudio}
          onSelectFrame={selectFrame}
          fps={fps}
          selectedTrackId={selectedTrackId}
          onSelectTrack={setSelectedTrackId}
          soloTrackId={soloTrackId}
          onToggleSolo={(id) => setSoloTrackId((current) => (current === id ? null : id))}
          trackStatus={baseAsset?.track_status ?? {}}
          appRunning={state.status === "running"}
          trackColors={trackColors}
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
            residualEnabled={baseAsset?.residual_enabled ?? false}
            objectColor={highlightColor}
            onRegenerate={(method, description) =>
              // App already reports the failure via submitError.
              void Promise.resolve(onRegenerateTrack?.(selectedTrack.sound_track_id, method, description)).catch(() => {})
            }
            onSetResidual={(enabled) => void Promise.resolve(onSetResidual?.(enabled)).catch(() => {})}
            events={(draftAsset?.sound_timeline?.sound_events ?? []).filter(
              (event) => event.sound_track_id === selectedTrack.sound_track_id,
            )}
            onRegenerateEvent={(id, description, spokenText) =>
              void regenerateEventFromPanel(id, description, spokenText)
            }
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
        error={submitError}
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
