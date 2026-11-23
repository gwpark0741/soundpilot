import { FormEvent, useId, useState } from "react";
import { NEW_TRACK_MODELS, type NewTrackModel } from "../lib/newTrack";
import { objectColor } from "../lib/selection";
import { formatSpan, type Span } from "../lib/span";
import type { NewTrack } from "../api";
import Icon from "./Icon";

interface NewTrackPanelProps {
  // The draft's spans, edited on the timeline's "New track" lane.
  spans: Span[];
  fps: number;
  hasAudio: boolean;
  // Why creating is unavailable right now (a running job), if it is.
  blockedReason: string | null;
  onRemoveSpan: (index: number) => void;
  onCancel: () => void;
  // Creates the track and starts its generation; a rejection is shown here.
  onCreate: (track: NewTrack) => Promise<void>;
}

export default function NewTrackPanel({ spans, fps, hasAudio, blockedReason, onRemoveSpan, onCancel, onCreate }: NewTrackPanelProps) {
  const uid = useId();
  const [label, setLabel] = useState("");
  const [description, setDescription] = useState("");
  const [model, setModel] = useState<NewTrackModel>("selsync");
  const [spoken, setSpoken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const speech = model === "tts";
  const ready = !!label.trim() && !!description.trim() && spans.length > 0 && (!speech || !!spoken.trim());

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await onCreate({
        label: label.trim(),
        description: description.trim(),
        model,
        ...(speech ? { spoken_text: spoken.trim() } : {}),
        spans,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
    setBusy(false);
  }

  return (
    <section className="side-group" aria-label="New track">
      <h2>New track</h2>
      <form className="detail nt" style={{ ["--oc" as string]: objectColor(-1) }} onSubmit={(event) => void submit(event)}>
        <label>
          Name
          <input value={label} onChange={(e) => setLabel(e.target.value)} />
        </label>
        <label>
          Description
          <textarea rows={2} value={description} onChange={(e) => setDescription(e.target.value)} />
        </label>
        <label>
          Model
          <select value={model} onChange={(e) => setModel(e.target.value as NewTrackModel)}>
            {NEW_TRACK_MODELS.map((option) => (
              <option key={option.value} value={option.value} disabled={option.value === "separate" && !hasAudio}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
        {!hasAudio ? <span className="hint">SAM Audio is unavailable: the video has no original audio.</span> : null}
        {speech ? (
          <label>
            Spoken text
            <textarea rows={2} value={spoken} onChange={(e) => setSpoken(e.target.value)} />
          </label>
        ) : null}
        <div className="nt-spans">
          <span id={`${uid}-spans`}>Spans</span>
          <ul aria-labelledby={`${uid}-spans`}>
            {spans.map((span, index) => (
              <li key={index}>
                <span>{formatSpan(span, fps)}</span>
                <button
                  aria-label={`Remove span ${formatSpan(span, fps)}`}
                  className="btn sm"
                  onClick={() => onRemoveSpan(index)}
                  type="button"
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>
          <p className="hint">Drag on the New track lane to add a span. Drag a span to move it, or its edges to resize.</p>
        </div>
        {error ? (
          <div className="err" role="alert">
            <Icon name="x" />
            <div>{error}</div>
          </div>
        ) : null}
        {blockedReason ? (
          <p className="reason">
            <Icon name="info" />
            {blockedReason}
          </p>
        ) : null}
        <div className="actions">
          <button className="btn" type="button" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button className="btn primary" type="submit" disabled={busy || !ready || blockedReason !== null}>
            Create and generate
          </button>
        </div>
      </form>
    </section>
  );
}
