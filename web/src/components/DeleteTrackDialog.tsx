import { useEffect, useId, useRef } from "react";

interface DeleteTrackDialogProps {
  open: boolean;
  label: string;
  onClose: () => void;
  onConfirm: () => void;
}

// Native modal dialog (Escape or the backdrop cancels); focus starts on Cancel so Enter never deletes.
export default function DeleteTrackDialog({ open, label, onClose, onConfirm }: DeleteTrackDialogProps) {
  const uid = useId();
  const dialogRef = useRef<HTMLDialogElement | null>(null);
  const cancelRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      dialog.showModal();
      cancelRef.current?.focus();
    }
    if (!open && dialog.open) dialog.close();
  }, [open]);

  return (
    <dialog
      aria-describedby={`${uid}-body`}
      aria-labelledby={`${uid}-title`}
      className="dlg dlg-sm"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
      onClose={onClose}
      ref={dialogRef}
    >
      <div className="dlg-body">
        <h2 id={`${uid}-title`}>Delete track?</h2>
        <p id={`${uid}-body`}>
          “{label}” will be removed with its events and audio. This cannot be undone.
        </p>
        <div className="actions dlg-actions">
          <button className="btn" type="button" onClick={onClose} ref={cancelRef}>
            Cancel
          </button>
          <button className="btn danger" type="button" onClick={onConfirm}>
            Delete
          </button>
        </div>
      </div>
    </dialog>
  );
}
