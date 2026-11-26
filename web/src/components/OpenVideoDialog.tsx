import { FormEvent, useEffect, useId, useRef, useState } from "react";
import { fetchConnection, fetchProjects, saveConnection } from "../api";
import { projectMeta } from "../lib/projects";
import type { GpuConnection, ProjectSummary } from "../types";
import Icon from "./Icon";

interface OpenVideoDialogProps {
  open: boolean;
  onClose: () => void;
  running: boolean;
  hasVideo: boolean;
  error: string | null;
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

  const [serverUrl, setServerUrl] = useState("");
  const [toolsUrl, setToolsUrl] = useState("");
  const [token, setToken] = useState("");
  const [tokenSet, setTokenSet] = useState(false);
  const [connResult, setConnResult] = useState<{ ok: boolean; text: string } | null>(null);

  const loadedConn = useRef({ server_url: "", tools_url: "" });
  const applyConnection = (conn: GpuConnection) => {
    loadedConn.current = { server_url: conn.server_url, tools_url: conn.tools_url };
    setServerUrl(conn.server_url);
    setToolsUrl(conn.tools_url);
    setTokenSet(conn.token_set);
  };
  useEffect(() => {
    if (!open) return;
    setConnResult(null);
    fetchConnection().then(applyConnection, () => {});
  }, [open]);

  // An empty token field keeps the saved token.
  const persistConnection = async () => {
    applyConnection(await saveConnection({ server_url: serverUrl, tools_url: toolsUrl, token: token || null }));
    setToken("");
  };

  const saveGpuServers = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setConnResult(null);
    try {
      await persistConnection();
      setConnResult({ ok: true, text: "Saved." });
    } catch (error) {
      setConnResult({ ok: false, text: error instanceof Error ? error.message : String(error) });
    }
  };

  // Run also applies GPU server fields that were typed but not saved.
  const submitRun = async (event: FormEvent<HTMLFormElement>) => {
    if (token || serverUrl !== loadedConn.current.server_url || toolsUrl !== loadedConn.current.tools_url) {
      // React sets event.currentTarget to null once this handler awaits; onSubmit reads the form from it.
      const form = event.currentTarget;
      try {
        await persistConnection();
      } catch (error) {
        setConnResult({ ok: false, text: error instanceof Error ? error.message : String(error) });
        throw error;
      }
      event = Object.assign(Object.create(event), { currentTarget: form });
    }
    await onSubmit(event);
  };

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
        <form className="dlg-sec" id={runFormId} onSubmit={closeAfter(submitRun)}>
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
            <form className="dlg-sec" onSubmit={saveGpuServers}>
              <h3>GPU servers</h3>
              <label>
                Inference server URL
                <input type="url" placeholder="http://..." value={serverUrl} onChange={(event) => setServerUrl(event.target.value)} />
              </label>
              <label>
                Audyssey tools URL
                <input type="url" placeholder="http://..." value={toolsUrl} onChange={(event) => setToolsUrl(event.target.value)} />
              </label>
              <label>
                Token
                <input
                  type="password"
                  autoComplete="off"
                  placeholder={tokenSet ? "Saved" : "From the pod log"}
                  value={token}
                  onChange={(event) => setToken(event.target.value)}
                />
              </label>
              <div className="actions">
                <button className="btn" type="submit">
                  Save
                </button>
              </div>
              {connResult?.ok ? <p role="status">{connResult.text}</p> : null}
              {connResult && !connResult.ok ? <p className="err" role="alert">{connResult.text}</p> : null}
            </form>
          </div>
        </details>
      </div>
    </dialog>
  );
}
