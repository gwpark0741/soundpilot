import { useEffect, useId, useRef, useState } from "react";
import DeleteTrackDialog from "./DeleteTrackDialog";
import Icon, { STATUS_ICON } from "./Icon";
import { trackFailureText } from "../lib/errors";
import { methodLabel, statusView } from "../lib/selection";
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
  // Span edits of this track wait for its regenerate; the event-level Regenerate is off until then.
  pendingSpans?: boolean;
  // Any unsaved timeline edit (this track's or another's) can be dropped from here.
  hasUnsavedEdits?: boolean;
  onDiscardEdits?: () => void;
  // `model` is only set when the Model select differs from the track's current model.
  // Starts with this model picked (tests; the user normally picks it).
  initialModel?: ModelChoice;
  onRegenerate: (
    method: "separate" | "generate",
    description: string | null,
    model?: "selsync" | "t2a" | "tts",
  ) => void;
  onSetResidual: (enabled: boolean) => void;
  // Keeps the track's audio only inside its span boxes (this track's pending span edits go with it).
  onTrim?: () => void;
  // Lifts the trim of a track that has one.
  onUndoTrim?: () => void;
  // Called once the delete dialog is confirmed.
  onDeleteTrack?: () => void;
  events: SoundEvent[];
  onRegenerateEvent: (eventId: string, description: string, spokenText: string | null) => void;
}

const STATUS_CLASS = { ok: "done", run: "run", wait: "queue", err: "fail" } as const;
const PENDING_SPANS_REASON = "Span edits are pending. Regenerate the track to apply them.";
const OTHER_MODEL_REASON = "Per-span regeneration uses the current model. Select it again, or regenerate the whole track.";
const TIMING_ONLY_NOTE = "Spans are used as timing only; span descriptions are not used.";

export type ModelChoice = "selsync" | "t2a" | "tts" | "separate";
const EVENT_LEVEL: ModelChoice[] = ["t2a", "tts"];
const MODEL_LABEL: Record<ModelChoice, string> = {
  selsync: "SelVA w/ SelSync",
  t2a: "ElevenLabs",
  tts: "ElevenLabs TTS",
  separate: "Separate",
};

export function mainLabel(current: ModelChoice, selected: ModelChoice, pendingSpans: boolean): string {
  if (selected !== current) return selected === "separate" ? "Separate" : `Regenerate with ${MODEL_LABEL[selected]}`;
  if (pendingSpans) return "Regenerate with new spans";
  return selected === "separate" ? "Re-separate" : "Regenerate track";
}

// What the track description box starts with: the stored description, else (event-level tracks) the earliest span's.
export function initialDescription(
  track: Pick<SoundTrack, "description" | "generation_model">,
  artifact: AudioTrackArtifact | undefined,
  events: SoundEvent[],
): string {
  const stored = artifact?.description ?? track.description ?? "";
  if (stored || track.generation_model === "selsync" || track.generation_model === "separate") return stored;
  return [...events].sort((a, b) => a.start_frame_index - b.start_frame_index)[0]?.description ?? "";
}

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
          Regenerate this span
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
  pendingSpans = false,
  hasUnsavedEdits = false,
  onDiscardEdits,
  initialModel,
  onRegenerate,
  onSetResidual,
  onTrim,
  onUndoTrim,
  onDeleteTrack,
  events,
  onRegenerateEvent,
}: SelectedTrackPanelProps) {
  const uid = useId();
  const [deleteOpen, setDeleteOpen] = useState(false);
  const view = statusView(status, appRunning);
  const current: ModelChoice = track.generation_model === "v2a" ? "selsync" : (track.generation_model as ModelChoice);
  const [picked, setPicked] = useState<ModelChoice | null>(initialModel ?? null);
  const mounted = useRef(false);
  useEffect(() => {
    if (mounted.current) setPicked(null);
    mounted.current = true;
  }, [current]);
  const selected = picked ?? current;
  const options = (["selsync", "t2a", "tts", "separate"] as ModelChoice[]).filter(
    (model) =>
      model === current ||
      (model === "tts" ? track.track_type === "speech" : model === "separate" ? hasAudio : true),
  );
  const currentEventLevel = EVENT_LEVEL.includes(current);
  const eventSelected = EVENT_LEVEL.includes(selected);
  const stored = artifact?.description ?? track.description ?? "";
  const original = initialDescription(track, artifact, events);
  const [edited, setEdited] = useState<string | null>(null);
  const description = edited ?? original;
  const origin = artifact?.origin ?? (track.generation_model === "separate" ? "separated" : "generated");
  const seedLine =
    origin === "separated"
      ? artifact?.attempt != null ? `Attempt ${artifact.attempt}` : null
      : artifact?.seed != null ? `Seed ${artifact.seed}` : null;
  const whyId = `${uid}-why`;
  const blocked = blockedReason !== null;
  const trimmed = track.clip_segments != null;
  const reason = blockedReason ?? (currentEventLevel && pendingSpans ? PENDING_SPANS_REASON : null);
  const rowsReason = reason ?? (eventSelected && selected !== current ? OTHER_MODEL_REASON : null);
  const sameModel = selected === current;
  const label = mainLabel(current, selected, pendingSpans);
  const actionButtons = (
    <div className="actions">
      <button
        type="button"
        className="btn sm"
        disabled={blocked}
        aria-describedby={blocked ? whyId : undefined}
        onClick={() =>
          onRegenerate(
            selected === "separate" ? "separate" : "generate",
            !eventSelected && description.trim() && description !== stored ? description : null,
            selected !== "separate" && !sameModel ? selected : undefined,
          )
        }
      >
        {label}
      </button>
      {artifact && onTrim && (pendingSpans || !trimmed) ? (
        <button
          type="button"
          className="btn sm"
          disabled={blocked}
          aria-describedby={blocked ? whyId : undefined}
          onClick={onTrim}
        >
          Trim to spans
        </button>
      ) : null}
      {artifact && trimmed && onUndoTrim ? (
        <button
          type="button"
          className="btn sm"
          disabled={blocked}
          aria-describedby={blocked ? whyId : undefined}
          onClick={onUndoTrim}
        >
          Undo trim
        </button>
      ) : null}
    </div>
  );

  return (
    <section className="side-group" aria-label="Selected track">
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
              {/* A silent SelSync result keeps the new (silent) audio, not the previous one. */}
              {status.error?.startsWith("SelSync returned silence") ? null : (
                <p>
                  <Icon name="info" />
                  Previous audio kept.
                </p>
              )}
            </div>
          </div>
        ) : null}
        <div className="desc">
          <label htmlFor={`${uid}-model`}>Model</label>
          <select id={`${uid}-model`} value={selected} onChange={(e) => setPicked(e.target.value as ModelChoice)}>
            {options.map((model) => (
              <option key={model} value={model}>
                {MODEL_LABEL[model]}
              </option>
            ))}
          </select>
        </div>
        {eventSelected ? (
          <>
            {actionButtons}
            <ul className="events">
              {events.map((event) => (
                <EventRow
                  key={event.sound_event_id}
                  event={event}
                  disabled={rowsReason !== null}
                  describedBy={whyId}
                  onRegenerate={onRegenerateEvent}
                />
              ))}
            </ul>
          </>
        ) : (
          <>
            <div className="desc">
              <label htmlFor={`${uid}-desc`}>{selected === "selsync" && currentEventLevel ? "Track description" : "Description"}</label>
              <textarea id={`${uid}-desc`} rows={2} value={description} onChange={(e) => setEdited(e.target.value)} />
              {selected === "selsync" && currentEventLevel ? <p className="hint">{TIMING_ONLY_NOTE}</p> : null}
            </div>
            {actionButtons}
          </>
        )}
        {error ? (
          <div className="err" role="alert">
            <Icon name="x" />
            <div>{error}</div>
          </div>
        ) : null}
        {rowsReason ? (
          <p className="reason" id={whyId}>
            <Icon name="info" />
            {rowsReason}
          </p>
        ) : null}
        {onDeleteTrack ? (
          <div className="actions">
            <button
              type="button"
              className="btn sm danger"
              disabled={blocked}
              aria-describedby={blocked ? whyId : undefined}
              onClick={() => setDeleteOpen(true)}
            >
              Delete track
            </button>
            <DeleteTrackDialog
              open={deleteOpen}
              label={track.label}
              onClose={() => setDeleteOpen(false)}
              onConfirm={() => {
                setDeleteOpen(false);
                onDeleteTrack();
              }}
            />
          </div>
        ) : null}
        {hasUnsavedEdits && onDiscardEdits ? (
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
