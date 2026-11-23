from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from v2a_inspect.models import SoundEvent, SoundTimeline, SoundTrack, VideoAsset
from v2a_inspect.ui import projects
from v2a_inspect.ui.projects import list_projects, new_project_dir, project_path, write_meta
from v2a_inspect.ui.routes import create_router
from v2a_inspect.ui.store import VideoAssetStore


def _timeline(label: str) -> SoundTimeline:
    track = SoundTrack(track_type="sfx", label=label, generation_model="t2a")
    event = SoundEvent(
        sound_track_id=track.sound_track_id,
        start_frame_index=0,
        end_frame_index=30,
        description=label,
    )
    return SoundTimeline(sound_tracks=[track], sound_events=[event])


def _project(root: Path, name: str = "clip.mp4") -> tuple[Path, VideoAsset]:
    path = new_project_dir(root)
    write_meta(path, name)
    preview = path / "preview.mp4"
    preview.write_bytes(b"mp4")
    asset = VideoAsset(
        source_path=path / "clip.prepared.mp4",
        frame_count=60,
        sound_timeline=_timeline("Door"),
        inactive_timeline=_timeline("Stem"),
        synthesized_video_path=preview,
        audio_mode="separate",
    )
    return path, asset


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr("v2a_inspect.ui.routes.DEFAULT_WORK_DIR", str(tmp_path))
    root = tmp_path / "v2a-inspect-ui"
    store = VideoAssetStore()
    app = FastAPI()
    app.include_router(create_router(store))
    return root, store, TestClient(app)


def _complete(store: VideoAssetStore, asset: VideoAsset) -> None:
    asyncio.run(store.set_complete_asset(asset))


def test_complete_asset_is_saved_with_meta(tmp_path):
    path, asset = _project(tmp_path)
    _complete(VideoAssetStore(), asset)
    meta = json.loads((path / "project.json").read_text())
    assert meta["name"] == "clip.mp4"
    assert (meta["duration_sec"], meta["audio_mode"], meta["tracks"]) == (2.0, "separate", 1)
    assert meta["has_preview"] is True
    assert not list(path.glob("*.tmp"))
    assert VideoAsset.model_validate_json((path / "asset.json").read_text()).inactive_timeline


def test_running_asset_is_not_saved(tmp_path):
    path, asset = _project(tmp_path)
    asyncio.run(VideoAssetStore().publish_asset_mutation(asset))
    assert not (path / "asset.json").exists()


def test_failed_replace_keeps_previous_asset_json(tmp_path, monkeypatch):
    path, asset = _project(tmp_path)
    projects.save_project(asset)
    before = (path / "asset.json").read_text()

    def boom(self, target):
        raise OSError("disk")

    monkeypatch.setattr(Path, "replace", boom)
    with pytest.raises(OSError):
        projects.save_project(asset.model_copy(update={"audio_mode": "generate"}))
    assert (path / "asset.json").read_text() == before


def test_legacy_flat_asset_is_not_saved(tmp_path):
    asset = VideoAsset(source_path=tmp_path / "old.prepared.mp4", frame_count=30)
    _complete(VideoAssetStore(), asset)
    assert not (tmp_path / "asset.json").exists()


def test_list_newest_first_and_failed_dir(env):
    root, store, client = env
    (_, first), (_, second) = _project(root, "a.mp4"), _project(root, "b.mp4")
    _complete(store, first)
    _complete(store, second)
    failed = new_project_dir(root)
    write_meta(failed, "broken.mp4")
    (root / "projects" / "not-a-uuid").mkdir()

    rows = client.get("/api/projects").json()
    # The unsaved project was created last, so it sorts first.
    assert [r["name"] for r in rows] == ["broken.mp4", "b.mp4", "a.mp4"]
    assert {r["id"]: r["status"] for r in rows}[failed.name] == "failed"
    assert len(rows) == 3
    assert rows[1]["tracks"] == 1 and rows[1]["audio_mode"] == "separate"
    assert list_projects(root / "missing") == []


def test_open_restores_asset_with_inactive_timeline(env):
    root, store, client = env
    path, asset = _project(root)
    _complete(store, asset)
    asyncio.run(store.clear())

    assert client.post(f"/api/projects/{path.name}/open").status_code == 200
    opened = asyncio.run(store.snapshot())
    assert opened.status == "complete"
    assert opened.asset.inactive_timeline.sound_tracks[0].label == "Stem"
    assert opened.asset.synthesized_video_path == asset.synthesized_video_path
    assert opened.asset.synthesized_video_path.exists()


def test_open_conflicts_while_running_and_for_unsaved(env):
    root, store, client = env
    path, asset = _project(root)
    _complete(store, asset)
    unsaved = new_project_dir(root)
    write_meta(unsaved, "x.mp4")
    assert client.post(f"/api/projects/{unsaved.name}/open").status_code == 409
    asyncio.run(store.set_running(stage="busy"))
    assert client.post(f"/api/projects/{path.name}/open").status_code == 409


def test_delete_removes_dir_and_clears_current(env):
    root, store, client = env
    path, asset = _project(root)
    other, _ = _project(root)
    _complete(store, asset)
    assert client.delete(f"/api/projects/{path.name}").status_code == 200
    assert not path.exists() and other.exists()
    assert asyncio.run(store.snapshot()).asset is None


def test_delete_refuses_running_project(env):
    root, store, client = env
    path, asset = _project(root)
    _complete(store, asset)
    asyncio.run(store.set_running(stage="busy"))
    assert client.delete(f"/api/projects/{path.name}").status_code == 409
    assert path.exists()


@pytest.mark.parametrize("bad", ["..", "not-a-uuid", str(uuid4()).upper(), "a/../b"])
def test_traversal_and_bad_ids_rejected(env, bad):
    root, _, client = env
    with pytest.raises(ValueError):
        project_path(root, bad)
    assert client.delete(f"/api/projects/{bad}").status_code in (404, 405)
    assert client.post(f"/api/projects/{bad}/open").status_code in (404, 405)


def test_unknown_project_is_404(env):
    _, _, client = env
    assert client.post(f"/api/projects/{uuid4()}/open").status_code == 404


def test_runs_land_in_separate_project_dirs(env, monkeypatch):
    root, store, client = env

    async def fake_pipeline(upload_path, work_dir, store, options):
        (work_dir / "preview.mp4").write_bytes(upload_path.read_bytes())
        await store.set_complete_asset(
            VideoAsset(source_path=work_dir / "v.prepared.mp4", frame_count=30)
        )

    monkeypatch.setattr("v2a_inspect.ui.routes.run_uploaded_video_pipeline", fake_pipeline)
    for name, data in (("one.mp4", b"first"), ("two.mov", b"second")):
        resp = client.post("/api/runs", files={"video": (name, data, "video/mp4")})
        assert resp.status_code == 200

    rows = client.get("/api/projects").json()
    assert sorted(r["name"] for r in rows) == ["one.mp4", "two.mov"]
    previews = {
        r["name"]: (root / "projects" / r["id"] / "preview.mp4").read_bytes() for r in rows
    }
    assert previews == {"one.mp4": b"first", "two.mov": b"second"}
    assert all(r["status"] == "complete" for r in rows)
    for r in rows:  # the upload sits inside its own project
        assert list((root / "projects" / r["id"] / "uploads").iterdir())


def test_bundle_import_creates_project_and_roundtrips(env):
    root, store, client = env
    path, asset = _project(root, "clip.mp4")
    (path / "uploads").mkdir()
    (path / "uploads" / "u.mp4").write_bytes(b"raw")
    _complete(store, asset)

    bundle = client.get("/api/bundle")
    assert bundle.status_code == 200
    resp = client.post(
        "/api/bundle/import",
        files={"bundle": ("clip-bundle.zip", bundle.content, "application/zip")},
    )
    assert resp.status_code == 200
    rows = client.get("/api/projects").json()
    assert len(rows) == 2
    imported = next(r for r in rows if r["id"] != path.name)
    assert imported["name"] == "clip" and imported["status"] == "complete"
    assert imported["tracks"] == 1
    current = asyncio.run(store.snapshot()).asset
    assert current.source_path.parent == (root / "projects" / imported["id"]).resolve()
    assert current.inactive_timeline is not None
