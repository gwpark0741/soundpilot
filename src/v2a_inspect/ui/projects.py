"""Server-side project history: one directory per project under <root>/projects/.

<project>/asset.json is the VideoAsset, <project>/project.json is the list-view meta.
Every derived file (prepared video, keyframes, audio, preview.mp4) lives in the same directory.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from v2a_inspect.models import VideoAsset

logger = logging.getLogger(__name__)

PROJECTS = "projects"


def new_project_dir(root: Path) -> Path:
    return root / PROJECTS / str(uuid.uuid4())


def project_path(root: Path, project_id: str) -> Path:
    """Directory of a project id; ValueError unless it is a canonical UUID (no path traversal)."""
    try:
        canonical = str(uuid.UUID(project_id))
    except ValueError:
        canonical = ""
    if canonical != project_id:
        raise ValueError(f"Invalid project id: {project_id!r}")
    return root / PROJECTS / project_id


def project_dir_of(asset: VideoAsset) -> Path | None:
    """The project directory an asset lives in, or None for the legacy flat layout."""
    path = asset.source_path.parent
    if path.name == "uploads":
        path = path.parent
    path = path.resolve()
    return path if path.parent.name == PROJECTS else None


def write_meta(path: Path, name: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    now = _now()
    _write_atomic(path / "project.json", json.dumps({"name": name, "created": now}))


def save_project(asset: VideoAsset) -> None:
    """Persist a stable asset; a no-op for assets outside <root>/projects/."""
    path = project_dir_of(asset)
    if path is None:
        return
    path.mkdir(parents=True, exist_ok=True)
    meta = _read_json(path / "project.json")
    now = _now()
    timeline = asset.sound_timeline
    meta.update(
        name=meta.get("name") or path.name,
        created=meta.get("created") or now,
        updated=now,
        duration_sec=asset.duration_sec,
        audio_mode=asset.audio_mode,
        scenes=len(asset.initial_scenes),
        objects=len(asset.visual_identity_layer.visual_objects)
        if asset.visual_identity_layer
        else 0,
        tracks=len(timeline.sound_tracks) if timeline else 0,
        has_preview=bool(
            asset.synthesized_video_path and asset.synthesized_video_path.exists()
        ),
    )
    # asset.json first: a listed project always has a loadable asset.
    _write_atomic(
        path / "asset.json", asset.model_dump_json(exclude_computed_fields=True)
    )
    _write_atomic(path / "project.json", json.dumps(meta))


def save_project_logged(asset: VideoAsset) -> None:
    try:
        save_project(asset)
    except OSError:
        # History must never break the editing session.
        logger.exception("Could not save project %s", asset.source_path)


def load_project(path: Path) -> VideoAsset:
    try:
        return VideoAsset.model_validate_json((path / "asset.json").read_bytes())
    except (OSError, ValidationError) as exc:
        raise ValueError(f"Project has no loadable asset.json: {exc}") from exc


def list_projects(root: Path, running_id: str | None = None) -> list[dict[str, object]]:
    """Projects newest-updated first.

    A directory without asset.json is listed as "failed" (or "running" for running_id) so it can be deleted.
    """
    base = root / PROJECTS
    rows = []
    for path in base.iterdir() if base.is_dir() else []:
        try:
            project_path(root, path.name)
        except ValueError:
            continue
        if not path.is_dir():
            continue
        meta = _read_json(path / "project.json")
        stamp = datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()
        created = meta.get("created") or stamp
        done = (path / "asset.json").is_file()
        rows.append(
            {
                "id": path.name,
                "name": meta.get("name") or path.name,
                "created": created,
                "updated": meta.get("updated") or created,
                "duration_sec": meta.get("duration_sec"),
                "audio_mode": meta.get("audio_mode"),
                "scenes": meta.get("scenes", 0),
                "objects": meta.get("objects", 0),
                "tracks": meta.get("tracks", 0),
                "has_preview": meta.get("has_preview", False),
                "status": "complete"
                if done
                else "running"
                if path.name == running_id
                else "failed",
            }
        )
    return sorted(rows, key=lambda row: str(row["updated"]), reverse=True)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_atomic(path: Path, text: str) -> None:
    # ponytail: fixed temp name; safe because the store lock serializes writers per process.
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)
