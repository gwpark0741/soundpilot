import { FormEvent, useEffect, useId, useRef, useState } from "react";
import { fetchProjects } from "../api";
import { projectMeta } from "../lib/projects";
import type { ProjectSummary } from "../types";
import Icon from "./Icon";

interface OpenVideoDialogProps {
  open: boolean;
  onClose: () => void;
  running: boolean;
  hasVideo: boolean;
  error: string | null;
  serverUrl: string;
  onServerUrlChange: (value: string) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => Promise<void> | void;
  onImport: (event: FormEvent<HTMLFormElement>) => Promise<void> | void;
  onClean: () => Promise<void> | void;
  onOpenProject: (id: string) => Promise<void> | void;
  onDeleteProject: (id: string) => Promise<void> | void;
  onResetSoundTimeline: () => Promise<void> | void;
  canDownloadJson: boolean;
  onDownloadJson: () => void;
  hasEdits: boolean;
  onDiscardEdits: () => void;
  videoDownloadHref: string | null;
}

export default function OpenVideoDialog({
  open,
  onClose,
  running,
  hasVideo,
  error,
  serverUrl,
  onServerUrlChange,
  onSubmit,
  onImport,
  onClean,
  onOpenProject,
  onDeleteProject,
  onResetSoundTimeline,
  canDownloadJson,
  onDownloadJson,
  hasEdits,
  onDiscardEdits,
  videoDownloadHref,
}: OpenVideoDialogProps) {
  const uid = useId();
  const dialogRef = useRef<HTMLDialogElement | null>(null);
  const [workDir, setWorkDir] = useState("");
  const runFormId = `${uid}-run`;
  const [projects, setProjects] = useState<ProjectSummary[]>([]);

  const loadProjects = () => {
    fetchProjects().then(setProjects, () => setProjects([]));
  };
  // Refresh the history each time the dialog opens, and when a run ends (its project appears or finishes).
  useEffect(() => {
    if (open) loadProjects();
  }, [open, running]);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  // Close once the action went through; on failure the error stays visible in the window.
  const closeAfter = (action: (event: FormEvent<HTMLFormElement>) => Promise<void> | void) =>
    async (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      try {
        await action(event);
        onClose();
      } catch {
        // App reports the failure through `error`.
      }
    };

  return (
    <dialog
      aria-labelledby={`${uid}-title`}
      className="dlg"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
      onClose={onClose}
      ref={dialogRef}
    >
      <div className="dlg-body">
        <div className="dlg-head">
          <h2 id={`${uid}-title`}>Open video</h2>
          <button className="btn sm" type="button" onClick={onClose}>
            Close
          </button>
        </div>
        <form className="dlg-sec" id={runFormId} onSubmit={closeAfter(onSubmit)}>
          <h3>Analyze a new video</h3>
          <input name="work_dir" type="hidden" value={workDir} />
          <label>
            Video
            <input name="video" type="file" accept="video/*" required />
          </label>
          <div className="actions">
            <button className="btn" type="submit" disabled={running}>
              Run
            </button>
          </div>
        </form>
        <form className="dlg-sec" onSubmit={closeAfter(onImport)}>
          <h3>Load a saved project</h3>
          <input name="work_dir" type="hidden" value={workDir} />
          <label>
            Bundle (.zip)
            <input name="bundle" type="file" accept=".zip,application/zip" required />
          </label>
          <div className="actions">
            <button className="btn" type="submit" disabled={running}>
              Load
            </button>
          </div>
        </form>
        <section className="dlg-sec" aria-labelledby={`${uid}-recent`}>
          <h3 id={`${uid}-recent`}>Recent projects</h3>
          {projects.length === 0 ? (
            <p className="dlg-empty">No saved projects yet. Analyzed videos appear here.</p>
          ) : (
            <ul className="proj-list">
              {projects.map((project) => (
                <li className="proj" key={project.id}>
                  <div className="proj-info">
                    <span className="proj-name">{project.name}</span>
                    <span className="proj-meta">{projectMeta(project)}</span>
                  </div>
                  <button
                    className="btn sm"
                    type="button"
                    disabled={running || project.status !== "complete"}
                    onClick={() => void Promise.resolve(onOpenProject(project.id)).then(onClose, () => {})}
                  >
                    Open
                  </button>
                  <button
                    aria-label={`Delete ${project.name}`}
                    className="btn sm danger"
                    type="button"
                    disabled={project.status === "running"}
                    onClick={() => {
                      if (!window.confirm(`Delete "${project.name}" and its generated files? This cannot be undone.`)) return;
                      void Promise.resolve(onDeleteProject(project.id)).then(loadProjects, () => {});
                    }}
                  >
                    Delete
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
        {error ? (
          <p className="err" role="alert">
            <Icon name="x" />
            <span>{error}</span>
          </p>
        ) : null}
        <details className="dlg-adv">
          <summary>Advanced</summary>
          <div className="dlg-adv-body">
            <label>
              Work directory
              <input
                type="text"
                placeholder="/tmp/v2a-inspect-ui"
                value={workDir}
                onChange={(event) => setWorkDir(event.target.value)}
              />
            </label>
            <label>
              Inference server URL
              <input
                form={runFormId}
                name="server_url"
                type="url"
                placeholder="http://..."
                value={serverUrl}
                onChange={(event) => onServerUrlChange(event.target.value)}
              />
            </label>
            <div className="split">
              <label>
                Scene threshold
                <input form={runFormId} name="scene_threshold" type="number" min="1" max="100" step="1" defaultValue="27" />
              </label>
              <label>
                Keyframes
                <input form={runFormId} name="max_keyframes_per_scene" type="number" min="1" max="100" step="1" defaultValue="20" />
              </label>
            </div>
            <div className="actions">
              <button className="btn" type="button" disabled={running || !hasVideo} onClick={() => void Promise.resolve(onResetSoundTimeline()).catch(() => {})}>
                Reset + rerun soundtrack
              </button>
              <button className="btn" type="button" disabled={!hasEdits} onClick={onDiscardEdits}>
                Discard timeline edits
              </button>
              <button className="btn" type="button" disabled={!canDownloadJson} onClick={onDownloadJson}>
                Download edited JSON
              </button>
              {videoDownloadHref ? (
                <a className="btn" href={videoDownloadHref} download>
                  Download video (MP4)
                </a>
              ) : null}
              <button className="btn danger" type="button" disabled={running || !hasVideo} onClick={() => void onClean()}>
                Clean workspace
              </button>
            </div>
          </div>
        </details>
      </div>
    </dialog>
  );
}
