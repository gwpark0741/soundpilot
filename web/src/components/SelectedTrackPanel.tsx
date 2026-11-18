import { useId, useState } from "react";
import Icon, { STATUS_ICON } from "./Icon";
import { trackFailureText } from "../lib/errors";
import { methodLabel, statusView, trackActions, type TrackAction } from "../lib/selection";
import type { AudioTrackArtifact, SoundEvent, SoundTrack, TrackStatus } from "../types";

interface SelectedTrackPanelProps {
  track: SoundTrack;
  artifact: AudioTrackArtifact | undefined;
  status: TrackStatus | undefined;
  appRunning: boolean;
  blockedReason: string | null;
  hasAudio: boolean;
  residualEnabled: boolean;
  objectColor: string;
  // Failure of the last action started from this panel, shown next to the controls.
  error?: string | null;
  // Unsaved timeline edits can be dropped from here, since they are what blocks Regenerate.
  hasUnsavedEdits?: boolean;
  onDiscardEdits?: () => void;
  onRegenerate: (method: "separate" | "selsync", description: string | null) => void;
  onSetResidual: (enabled: boolean) => void;
  events: SoundEvent[];
  onRegenerateEvent: (eventId: string, description: string, spokenText: string | null) => void;
}

const STATUS_CLASS = { ok: "done", run: "run", wait: "queue", err: "fail" } as const;
const ACTION_VIEW: Record<TrackAction, { label: string; method: "separate" | "selsync" }> = {
  "re-separate": { label: "Re-separate", method: "separate" },
  "generate-instead": { label: "Generate instead", method: "selsync" },
  regenerate: { label: "Regenerate", method: "selsync" },
};

function EventRow({
  event,
  disabled,
  describedBy,
  onRegenerate,
}: {
  event: SoundEvent;
  disabled: boolean;
  describedBy?: string;
  onRegenerate: SelectedTrackPanelProps["onRegenerateEvent"];
}) {
  const id = useId();
  const [description, setDescription] = useState(event.description);
  const [spoken, setSpoken] = useState(event.spoken_text ?? "");
  const isSpeech = event.spoken_text != null;
  const empty = !description.trim() || (isSpeech && !spoken.trim());
  return (
    <li className="desc">
      <label htmlFor={`${id}-d`}>Description</label>
      <textarea id={`${id}-d`} rows={2} value={description} onChange={(e) => setDescription(e.target.value)} />
      {isSpeech ? (
        <>
          <label htmlFor={`${id}-s`}>Spoken text</label>
          <textarea id={`${id}-s`} rows={2} value={spoken} onChange={(e) => setSpoken(e.target.value)} />
        </>
      ) : null}
      <div className="actions">
        <button
          type="button"
          className="btn sm"
          disabled={disabled || empty}
          aria-describedby={disabled ? describedBy : undefined}
          onClick={() => onRegenerate(event.sound_event_id, description.trim(), isSpeech ? spoken.trim() : null)}
        >
          Regenerate
        </button>
      </div>
    </li>
  );
}

export default function SelectedTrackPanel({
  track,
  artifact,
  status,
  appRunning,
  blockedReason,
  hasAudio,
  residualEnabled,
  objectColor,
  error = null,
  hasUnsavedEdits = false,
  onDiscardEdits,
  onRegenerate,
  onSetResidual,
  events,
  onRegenerateEvent,
}: SelectedTrackPanelProps) {
  const uid = useId();
  const original = artifact?.description ?? track.description ?? "";
  const [description, setDescription] = useState(original);
  const view = statusView(status, appRunning);
  const actions = trackActions(track.generation_model, hasAudio, track.track_type);
  const origin = artifact?.origin ?? (track.generation_model === "separate" ? "separated" : "generated");
  const seedLine =
    origin === "separated"
      ? artifact?.attempt != null ? `Attempt ${artifact.attempt}` : null
      : artifact?.seed != null ? `Seed ${artifact.seed}` : null;
  const whyId = `${uid}-why`;
  const blocked = blockedReason !== null;
  const eventLevel = actions.length === 0;

  return (
    <section className="side" aria-label="Selected track">
      <h2>Selected track</h2>
      <article className="detail sel" style={{ ["--oc" as string]: objectColor }} aria-label={track.label}>
        <div className="d-head">
          <span className="sw" style={{ background: objectColor }} aria-hidden="true" />
          <h3>{track.label}</h3>
          {view.label ? (
            <span className={`st st-${STATUS_CLASS[view.tone ?? "err"]}`}>
              <Icon name={STATUS_ICON[view.tone ?? "err"]} />
              {view.label}
            </span>
          ) : null}
        </div>
        <div className="d-meta">
          <span className={`badge ${track.generation_model === "separate" ? "badge-sep" : "badge-gen"}`}>
            {methodLabel(track.generation_model)}
          </span>
          {seedLine ? <span>{seedLine}</span> : null}
        </div>
        {status?.state === "failed" ? (
          <div className="err">
            <Icon name="x" />
            <div>
              {trackFailureText(status.error)}
              <p>
                <Icon name="info" />
                Previous audio kept.
              </p>
            </div>
          </div>
        ) : null}
        {eventLevel ? (
          <ul className="events">
            {events.map((event) => (
              <EventRow
                key={event.sound_event_id}
                event={event}
                disabled={blocked}
                describedBy={whyId}
                onRegenerate={onRegenerateEvent}
              />
            ))}
          </ul>
        ) : (
          <>
            <div className="desc">
              <label htmlFor={`${uid}-desc`}>Description</label>
              <textarea id={`${uid}-desc`} rows={2} value={description} onChange={(e) => setDescription(e.target.value)} />
            </div>
            <div className="actions">
              {actions.map((action) => (
                <button
                  key={action}
                  type="button"
                  className="btn sm"
                  disabled={blocked}
                  aria-describedby={blocked ? whyId : undefined}
                  onClick={() => onRegenerate(ACTION_VIEW[action].method, description === original ? null : description)}
                >
                  {ACTION_VIEW[action].label}
                </button>
              ))}
            </div>
          </>
        )}
        {error ? (
          <div className="err" role="alert">
            <Icon name="x" />
            <div>{error}</div>
          </div>
        ) : null}
        {blocked ? (
          <p className="reason" id={whyId}>
            <Icon name="info" />
            {blockedReason}
          </p>
        ) : null}
        {blocked && hasUnsavedEdits && onDiscardEdits ? (
          <div className="actions">
            <button type="button" className="btn sm" onClick={onDiscardEdits}>
              Discard edits
            </button>
          </div>
        ) : null}
      </article>
      {hasAudio ? (
        <div className="resid">
          <span id={`${uid}-resid-l`}>Include rest of original audio</span>
          <span className="sw-state">{residualEnabled ? "On" : "Off"}</span>
          <button
            type="button"
            className="switch"
            role="switch"
            aria-checked={residualEnabled}
            aria-labelledby={`${uid}-resid-l`}
            disabled={appRunning}
            onClick={() => onSetResidual(!residualEnabled)}
          >
            <span className="knob" />
          </button>
        </div>
      ) : null}
    </section>
  );
}
