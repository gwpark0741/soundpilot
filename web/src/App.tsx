import { FormEvent, useEffect, useRef, useState } from "react";
import {
  fetchAssetSummary,
  cleanAsset,
  importBundle,
  openProject,
  deleteProject,
  resetSoundTimeline,
  startRun,
  generateAudio,
  regenerateEventAudio,
  regenerateTrackAudio,
  setAudioMode,
  setResidualEnabled,
  setTrackClip,
  trimTrackToSpans,
  undoTrackTrim,
  deleteTrack,
  createTrack,
  type NewTrack,
} from "./api";
import type { Clip } from "./lib/clip";
import type { EventSpan } from "./lib/pendingEdits";
import { failure } from "./lib/errors";
import type { VideoAsset } from "./types";
import VideoEditor from "./components/VideoEditor";
import type { AssetResponse } from "./types";

const emptyAsset: AssetResponse = {
  status: "idle",
  stage: null,
  error: null,
  version: 0,
  asset_version: 0,
  updated_at: "",
  video: null,
  timeline_rows: [],
  audio_tracks: [],
  audio_events: [],
  track_targets: {},
  track_target_fallbacks: {},
};

export default function App() {
  const [state, setState] = useState<AssetResponse>(emptyAsset);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const summaryRequestRef = useRef(0);

  useEffect(() => {
    void refreshAssetSummary();
    const events = new EventSource("/events");
    events.addEventListener("asset_update", () => {
      void refreshAssetSummary();
    });
    return () => events.close();
  }, []);

  async function refreshAssetSummary() {
    const request = ++summaryRequestRef.current;
    const nextState = await fetchAssetSummary();
    // A slower, older response must not replace a newer one.
    if (request === summaryRequestRef.current) {
      setState(nextState);
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitError(null);
    try {
      await startRun(event.currentTarget);
      await refreshAssetSummary();
    } catch (error) {
      setSubmitError(failure("Could not analyze the video.", error, "Check the video, then press Run again."));
      throw error;
    }
  }

  async function handleImport(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitError(null);
    try {
      await importBundle(event.currentTarget);
      await refreshAssetSummary();
    } catch (error) {
      setSubmitError(failure("Could not load the project.", error, "Check the bundle, then press Load again."));
      throw error;
    }
  }

  async function handleOpenProject(id: string) {
    setSubmitError(null);
    try {
      await openProject(id);
      await refreshAssetSummary();
    } catch (error) {
      setSubmitError(failure("Could not open the project.", error, "Wait for the analysis to finish, then try again."));
      throw error;
    }
  }

  async function handleDeleteProject(id: string) {
    setSubmitError(null);
    try {
      await deleteProject(id);
      await refreshAssetSummary();
    } catch (error) {
      setSubmitError(failure("Could not delete the project.", error, "Try again in a moment."));
      throw error;
    }
  }

  async function handleResetSoundTimeline() {
    setSubmitError(null);
    try {
      await resetSoundTimeline();
      await refreshAssetSummary();
    } catch (error) {
      setSubmitError(failure("Could not reset the soundtrack.", error, "Try again in a moment."));
      throw error;
    }
  }

  async function handleClean() {
    setSubmitError(null);
    try {
      await cleanAsset();
      await refreshAssetSummary();
    } catch (error) {
      setSubmitError(failure("Could not clean the workspace.", error, "Try again in a moment."));
      throw error;
    }
  }

  async function handleGenerateAudio(serverUrl: string, draftAsset: VideoAsset | null) {
    setSubmitError(null);
    try {
      await generateAudio(serverUrl, draftAsset);
      await refreshAssetSummary();
    } catch (error) {
      setSubmitError(failure("Could not generate audio.", error, "Check the inference server URL, then press Generate audio again."));
      throw error;
    }
  }

  async function handleRegenerateEventAudio(
    soundEventId: string,
    description: string,
    spokenText: string | null,
    serverUrl: string | null,
  ) {
    setSubmitError(null);
    try {
      await regenerateEventAudio(soundEventId, description, spokenText, serverUrl);
      await refreshAssetSummary();
    } catch (error) {
      // Top line: short summary. The full message goes next to the failed control.
      setSubmitError("Could not regenerate the event audio.");
      throw new Error(failure("Could not regenerate the event audio.", error, "Press Regenerate to try again."));
    }
  }

  async function handleRegenerateTrack(
    trackId: string,
    method: "separate" | "generate",
    description: string | null,
    events?: EventSpan[],
    model?: "selsync" | "t2a" | "tts",
  ) {
    setSubmitError(null);
    try {
      await regenerateTrackAudio(trackId, method, description ?? undefined, undefined, events, model);
      await refreshAssetSummary();
    } catch (error) {
      // Top line: short summary. The full message goes next to the failed control.
      setSubmitError("Could not regenerate the track.");
      throw new Error(failure("Could not regenerate the track.", error, "Press Regenerate to try again."));
    }
  }

  async function handleSetAudioMode(mode: "generate" | "separate") {
    setSubmitError(null);
    try {
      await setAudioMode(mode);
      await refreshAssetSummary();
    } catch (error) {
      setSubmitError(failure("Could not change the audio method.", error, "Try again in a moment."));
      throw error;
    }
  }

  async function handleSetResidual(enabled: boolean) {
    setSubmitError(null);
    try {
      await setResidualEnabled(enabled);
      await refreshAssetSummary();
    } catch (error) {
      // Top line: short summary. The full message goes next to the failed control.
      setSubmitError("Could not update the soundtrack mix.");
      throw new Error(failure("Could not update the soundtrack mix.", error, "Try the switch again."));
    }
  }

  async function handleSetTrackClip(trackId: string, clip: Clip) {
    setSubmitError(null);
    try {
      await setTrackClip(trackId, clip);
      await refreshAssetSummary();
    } catch (error) {
      // Top line: short summary. The full message goes next to the track.
      setSubmitError("Could not update the track audio.");
      throw new Error(failure("Could not update the track audio.", error, "Try the edit again."));
    }
  }

  async function handleTrimTrack(trackId: string, events?: EventSpan[]) {
    setSubmitError(null);
    try {
      await trimTrackToSpans(trackId, events);
      await refreshAssetSummary();
    } catch (error) {
      setSubmitError("Could not trim the track.");
      throw new Error(failure("Could not trim the track.", error, "Try Trim to spans again."));
    }
  }

  async function handleUndoTrim(trackId: string) {
    setSubmitError(null);
    try {
      await undoTrackTrim(trackId);
      await refreshAssetSummary();
    } catch (error) {
      setSubmitError("Could not undo the trim.");
      throw new Error(failure("Could not undo the trim.", error, "Try Undo trim again."));
    }
  }

  async function handleDeleteTrack(trackId: string) {
    setSubmitError(null);
    try {
      await deleteTrack(trackId);
      await refreshAssetSummary();
    } catch (error) {
      setSubmitError("Could not delete the track.");
      throw new Error(failure("Could not delete the track.", error, "Try again in a moment."));
    }
  }

  async function handleCreateTrack(track: NewTrack): Promise<string> {
    setSubmitError(null);
    let id: string;
    try {
      id = await createTrack(track);
      await refreshAssetSummary();
    } catch (error) {
      setSubmitError("Could not add the track.");
      throw new Error(failure("Could not add the track.", error, "Check the fields, then try again."));
    }
    // The track exists now; its audio follows through the per-track regenerate flow.
    try {
      await handleRegenerateTrack(id, track.model === "separate" ? "separate" : "generate", null);
    } catch {
      // The top line already says so, and the track can be regenerated from its panel.
    }
    return id;
  }

  return (
    <VideoEditor
      state={state}
      submitError={submitError}
      onSubmit={handleSubmit}
      onImport={handleImport}
      onClean={handleClean}
      onOpenProject={handleOpenProject}
      onDeleteProject={handleDeleteProject}
      onResetSoundTimeline={handleResetSoundTimeline}
      onGenerateAudio={handleGenerateAudio}
      onRegenerateEventAudio={handleRegenerateEventAudio}
      onRegenerateTrack={handleRegenerateTrack}
      onSetAudioMode={handleSetAudioMode}
      onSetResidual={handleSetResidual}
      onSetTrackClip={handleSetTrackClip}
      onTrimTrack={handleTrimTrack}
      onUndoTrim={handleUndoTrim}
      onDeleteTrack={handleDeleteTrack}
      onCreateTrack={handleCreateTrack}
    />
  );
}
