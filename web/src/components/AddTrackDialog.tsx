import { FormEvent, useEffect, useId, useRef, useState } from "react";
import { NEW_TRACK_MODELS, newTrackSpan, type NewTrackModel } from "../lib/newTrack";
import type { NewTrack } from "../api";
import Icon from "./Icon";

interface AddTrackDialogProps {
  open: boolean;
  onClose: () => void;
  hasAudio: boolean;
  frame: number;
  frameCount: number;
  fps: number;
  // Creates the track and starts its generation; a rejection is shown in the dialog.
  onCreate: (track: NewTrack) => Promise<void>;
}

export default function AddTrackDialog({ open, onClose, hasAudio, frame, frameCount, fps, onCreate }: AddTrackDialogProps) {
  const uid = useId();
  const dialogRef = useRef<HTMLDialogElement | null>(null);
  const [label, setLabel] = useState("");
  const [description, setDescription] = useState("");
  const [model, setModel] = useState<NewTrackModel>("selsync");
  const [spoken, setSpoken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const speech = model === "tts";

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      setError(null);
      dialog.showModal();
    }
    if (!open && dialog.open) dialog.close();
  }, [open]);

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
        spans: [newTrackSpan(model, frame, frameCount, fps)],
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
      return;
    }
    setBusy(false);
    setLabel("");
    setDescription("");
    setSpoken("");
    onClose();
  }

  return (
    <dialog
      aria-labelledby={`${uid}-title`}
      className="dlg"
      onClick={(event) => {
        if (event.target === event.currentTarget && !busy) onClose();
      }}
      onClose={onClose}
      ref={dialogRef}
    >
      <form className="dlg-body" onSubmit={(event) => void submit(event)}>
        <h2 id={`${uid}-title`}>Add track</h2>
        <label>
          Name
          <input value={label} onChange={(e) => setLabel(e.target.value)} required />
        </label>
        <label>
          Description
          <textarea rows={2} value={description} onChange={(e) => setDescription(e.target.value)} required />
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
        {!hasAudio ? <span className="dlg-empty">SAM Audio is unavailable: the video has no original audio.</span> : null}
        {speech ? (
          <label>
            Spoken text
            <textarea rows={2} value={spoken} onChange={(e) => setSpoken(e.target.value)} required />
          </label>
        ) : null}
        {error ? (
          <p className="err" role="alert">
            <Icon name="x" />
            <span>{error}</span>
          </p>
        ) : null}
        <div className="actions dlg-actions">
          <button className="btn" type="button" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button className="btn primary" type="submit" disabled={busy}>
            Create and generate
          </button>
        </div>
      </form>
    </dialog>
  );
}
