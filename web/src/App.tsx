import { FormEvent, useEffect, useState } from "react";
import {
  fetchAssetSummary,
  cleanAsset,
  importAsset,
  resetSoundTimeline,
  startRun,
  generateAudio,
  regenerateEventAudio,
  regenerateTrackAudio,
  setResidualEnabled,
} from "./api";
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
};

export default function App() {
  const [state, setState] = useState<AssetResponse>(emptyAsset);
  const [submitError, setSubmitError] = useState<string | null>(null);

  useEffect(() => {
    void refreshAssetSummary();
    const events = new EventSource("/events");
    events.addEventListener("asset_update", () => {
      void refreshAssetSummary();
    });
    return () => events.close();
  }, []);

  async function refreshAssetSummary() {
    const nextState = await fetchAssetSummary();
    setState(nextState);
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
      await importAsset(event.currentTarget);
      await refreshAssetSummary();
    } catch (error) {
      setSubmitError(failure("Could not load the project.", error, "Check both files, then press Load again."));
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
      setSubmitError(failure("Could not regenerate the event audio.", error, "Press Regenerate to try again."));
      throw error;
    }
  }

  async function handleRegenerateTrack(
    trackId: string,
    method: "separate" | "selsync",
    description: string | null,
  ) {
    setSubmitError(null);
    try {
      await regenerateTrackAudio(trackId, method, description ?? undefined);
      await refreshAssetSummary();
    } catch (error) {
      setSubmitError(failure("Could not regenerate the track.", error, "Press Regenerate to try again."));
      throw error;
    }
  }

  async function handleSetResidual(enabled: boolean) {
    setSubmitError(null);
    try {
      await setResidualEnabled(enabled);
      await refreshAssetSummary();
    } catch (error) {
      setSubmitError(failure("Could not update the soundtrack mix.", error, "Try the switch again."));
      throw error;
    }
  }

  return (
    <VideoEditor
      state={state}
      submitError={submitError}
      onSubmit={handleSubmit}
      onImport={handleImport}
      onClean={handleClean}
      onResetSoundTimeline={handleResetSoundTimeline}
      onGenerateAudio={handleGenerateAudio}
      onRegenerateEventAudio={handleRegenerateEventAudio}
      onRegenerateTrack={handleRegenerateTrack}
      onSetResidual={handleSetResidual}
    />
  );
}
