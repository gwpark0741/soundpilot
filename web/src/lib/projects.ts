import type { ProjectSummary } from "../types";

function formatDuration(seconds: number): string {
  const whole = Math.round(seconds);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

// One line under the project name: date, duration, audio method, track count.
export function projectMeta(project: ProjectSummary): string {
  const date = new Date(project.updated).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  if (project.status === "failed") return `${date} · Analysis did not finish`;
  if (project.status === "running") return `${date} · Analyzing`;
  const parts = [date];
  if (project.duration_sec != null) parts.push(formatDuration(project.duration_sec));
  if (project.audio_mode) parts.push(project.audio_mode === "separate" ? "Separate" : "Generate");
  parts.push(`${project.tracks} ${project.tracks === 1 ? "track" : "tracks"}`);
  return parts.join(" · ");
}
