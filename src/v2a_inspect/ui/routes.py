from __future__ import annotations

import asyncio
import io
import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, ValidationError

from v2a_inspect.media_utils import probe_prepared_video
from v2a_inspect.audio_generation.routing import route_tracks
from v2a_inspect.models import SoundTimeline, VideoAsset
from v2a_inspect.preprocessing import prepare_video
from v2a_inspect.preprocessing.video import extract_original_audio
from v2a_inspect.separation_planning import switch_audio_mode

from .bundle import read_bundle, write_bundle
from .overlays import render_tracking_overlay
from .pipeline import (
    PipelineOptions,
    run_sound_timeline_pipeline,
    run_uploaded_video_pipeline,
    run_audio_generation_pipeline,
    run_event_audio_delete_pipeline,
    run_event_audio_regeneration_pipeline,
    run_residual_toggle_pipeline,
    run_separation_planning_pipeline,
    run_track_clip_pipeline,
    run_track_delete_pipeline,
    run_track_regeneration_pipeline,
)
from .projects import (
    list_projects,
    load_project,
    new_project_dir,
    project_dir_of,
    project_path,
    write_meta,
)
from .rows import (
    current_frame_rows,
    timeline_rows,
    track_highlight_fallbacks,
    track_highlight_targets,
    tracking_window_rows,
)
from .store import VideoAssetSnapshot, VideoAssetStore
from .track_edit import NewTrackModel, add_track, set_clip


DEFAULT_WORK_DIR = os.getenv("V2A_INSPECT_UI_WORK_DIR")
DEFAULT_SERVER_URL = os.getenv("V2A_INSPECT_UI_SERVER_URL")
DEFAULT_SCENE_THRESHOLD = float(os.getenv("V2A_INSPECT_UI_SCENE_THRESHOLD", "27.0"))
DEFAULT_MAX_KEYFRAMES_PER_SCENE = int(
    os.getenv("V2A_INSPECT_UI_MAX_KEYFRAMES_PER_SCENE", "20")
)


class ClipBody(BaseModel):
    start: float
    end: float
    offset: float


class NewTrackBody(BaseModel):
    label: str
    description: str
    model: NewTrackModel
    spoken_text: str | None = None
    start_frame: int
    end_frame: int


def create_router(store: VideoAssetStore) -> APIRouter:
    router = APIRouter()
    # Project the last /api/runs started; only used to label a directory without asset.json as running.
    run_project_id: str | None = None

    @router.get("/api/asset")
    async def get_asset() -> dict[str, object]:
        snapshot = await store.snapshot()
        return _full_snapshot_payload(snapshot)

    @router.get("/api/asset/export")
    async def export_asset() -> Response:
        snapshot = await store.snapshot()
        if snapshot.asset is None:
            raise HTTPException(status_code=404, detail="No video asset loaded")
        return Response(
            snapshot.asset.model_dump_json(
                indent=2,
                exclude_computed_fields=True,
            ),
            media_type="application/json",
            headers={
                "Content-Disposition": 'attachment; filename="video-asset.json"',
            },
        )

    @router.get("/api/asset-summary")
    async def get_asset_summary() -> dict[str, object]:
        snapshot = await store.snapshot()
        return _summary_payload(snapshot)

    @router.post("/api/asset/clean")
    async def clean_asset() -> dict[str, object]:
        snapshot = await store.snapshot()
        if snapshot.status == "running":
            raise HTTPException(status_code=409, detail="Pipeline is running")
        cleaned = (
            None
            if snapshot.asset is None
            else await asyncio.to_thread(_delete_asset_work_dir, snapshot.asset)
        )
        await store.clear()
        return {
            "status": "cleaned",
            "deleted": None if cleaned is None else str(cleaned),
        }

    @router.get("/api/video")
    async def get_video() -> FileResponse:
        snapshot = await store.snapshot()
        if snapshot.asset is None:
            raise HTTPException(status_code=404, detail="No video asset loaded")
        return FileResponse(snapshot.asset.source_path)

    @router.get("/api/synthesized-video")
    async def get_synthesized_video() -> FileResponse:
        snapshot = await store.snapshot()
        if (
            snapshot.asset is None
            or not snapshot.asset.synthesized_video_path
            or not snapshot.asset.synthesized_video_path.exists()
        ):
            raise HTTPException(
                status_code=404, detail="No synthesized video available"
            )
        return FileResponse(
            snapshot.asset.synthesized_video_path,
            media_type="video/mp4",
            headers={"Accept-Ranges": "bytes"},
        )

    @router.get("/api/audio/tracks/{sound_track_id}")
    async def get_audio_track(sound_track_id: str) -> FileResponse:
        snapshot = await store.snapshot()
        if snapshot.asset is None:
            raise HTTPException(status_code=404, detail="No video asset loaded")
        artifact = next(
            (
                item
                for item in snapshot.asset.sound_track_audio_artifacts
                if str(item.sound_track_id) == sound_track_id
            ),
            None,
        )
        if artifact is None:
            raise HTTPException(status_code=404, detail="No track audio available")
        return _audio_file_response(
            artifact.playback_path, "No track audio file available"
        )

    @router.get("/api/audio/events/{sound_event_id}")
    async def get_audio_event(sound_event_id: str) -> FileResponse:
        snapshot = await store.snapshot()
        if snapshot.asset is None:
            raise HTTPException(status_code=404, detail="No video asset loaded")
        artifact = next(
            (
                item
                for item in snapshot.asset.sound_event_audio_artifacts
                if str(item.sound_event_id) == sound_event_id
            ),
            None,
        )
        if artifact is None:
            raise HTTPException(status_code=404, detail="No event audio available")
        return _audio_file_response(artifact.path, "No event audio file available")

    @router.delete("/api/audio/events/{sound_event_id}")
    async def delete_audio_event(
        sound_event_id: str,
        background_tasks: BackgroundTasks,
    ) -> dict[str, object]:
        snapshot = await store.snapshot()
        if snapshot.status == "running":
            raise HTTPException(status_code=409, detail="Pipeline is running")
        if snapshot.asset is None:
            raise HTTPException(status_code=404, detail="No video asset loaded")
        _reject_track_level_event(snapshot.asset, sound_event_id)
        background_tasks.add_task(
            run_event_audio_delete_pipeline, snapshot.asset, store, sound_event_id
        )
        await store.publish_asset_mutation(
            snapshot.asset, stage="queued event audio delete"
        )
        return {"status": "queued"}

    @router.post("/api/audio/events/{sound_event_id}/regenerate")
    async def regenerate_audio_event(
        sound_event_id: str,
        background_tasks: BackgroundTasks,
        description: Annotated[str, Form()],
        spoken_text: Annotated[str | None, Form()] = None,
        server_url: Annotated[str | None, Form()] = None,
    ) -> dict[str, object]:
        snapshot = await store.snapshot()
        if snapshot.status == "running":
            raise HTTPException(status_code=409, detail="Pipeline is running")
        if snapshot.asset is None:
            raise HTTPException(status_code=404, detail="No video asset loaded")
        _reject_track_level_event(snapshot.asset, sound_event_id)
        background_tasks.add_task(
            run_event_audio_regeneration_pipeline,
            snapshot.asset,
            store,
            sound_event_id,
            description,
            spoken_text,
            server_url or DEFAULT_SERVER_URL or None,
        )
        await store.publish_asset_mutation(
            snapshot.asset, stage="queued event audio regeneration"
        )
        return {"status": "queued"}

    @router.post("/api/audio/tracks/{sound_track_id}/regenerate")
    async def regenerate_audio_track(
        sound_track_id: str,
        background_tasks: BackgroundTasks,
        method: Annotated[Literal["separate", "generate", "selsync"], Form()],
        description: Annotated[str | None, Form()] = None,
        seed: Annotated[int | None, Form()] = None,
    ) -> dict[str, object]:
        snapshot = await store.snapshot()
        if snapshot.status == "running":
            raise HTTPException(status_code=409, detail="Pipeline is running")
        asset = snapshot.asset
        if asset is None:
            raise HTTPException(status_code=404, detail="No video asset loaded")
        tracks = asset.sound_timeline.sound_tracks if asset.sound_timeline else []
        if not any(str(t.sound_track_id) == sound_track_id for t in tracks):
            raise HTTPException(status_code=404, detail="Sound track not found")
        if method == "selsync":  # old name for "generate"
            method = "generate"
        if method == "separate" and not asset.has_audio:
            raise HTTPException(
                status_code=422,
                detail="No original audio to separate.",
            )
        background_tasks.add_task(
            run_track_regeneration_pipeline,
            asset,
            store,
            sound_track_id,
            method,
            description,
            seed,
        )
        await store.publish_asset_mutation(
            asset, stage="queued track audio regeneration"
        )
        return {"status": "queued"}

    @router.post("/api/audio/tracks/{sound_track_id}/clip")
    async def set_audio_track_clip(
        sound_track_id: str,
        body: ClipBody,
        background_tasks: BackgroundTasks,
    ) -> dict[str, object]:
        snapshot = await store.snapshot()
        if snapshot.status == "running":
            raise HTTPException(status_code=409, detail="Pipeline is running")
        asset = snapshot.asset
        if asset is None:
            raise HTTPException(status_code=404, detail="No video asset loaded")
        track_id = _track_uuid(sound_track_id)
        try:
            updated = set_clip(asset, track_id, body.start, body.end, body.offset)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        track = next(
            t for t in updated.sound_timeline.sound_tracks if t.sound_track_id == track_id
        )
        background_tasks.add_task(run_track_clip_pipeline, updated, store)
        await store.publish_asset_mutation(updated, stage="queued track audio edit")
        return {
            "status": "queued",
            "clip": {
                "start": track.clip_start_sec,
                "end": track.clip_end_sec,
                "offset": track.clip_offset_sec,
            },
        }

    @router.delete("/api/audio/tracks/{sound_track_id}")
    async def delete_audio_track(
        sound_track_id: str,
        background_tasks: BackgroundTasks,
    ) -> dict[str, object]:
        snapshot = await store.snapshot()
        if snapshot.status == "running":
            raise HTTPException(status_code=409, detail="Pipeline is running")
        asset = snapshot.asset
        if asset is None:
            raise HTTPException(status_code=404, detail="No video asset loaded")
        _track_uuid(sound_track_id)
        tracks = asset.sound_timeline.sound_tracks if asset.sound_timeline else []
        if not any(str(t.sound_track_id) == sound_track_id for t in tracks):
            raise HTTPException(status_code=404, detail="Sound track not found")
        background_tasks.add_task(
            run_track_delete_pipeline, asset, store, sound_track_id
        )
        await store.publish_asset_mutation(asset, stage="queued track delete")
        return {"status": "queued"}

    @router.post("/api/audio/tracks")
    async def create_audio_track(body: NewTrackBody) -> dict[str, object]:
        snapshot = await store.snapshot()
        if snapshot.status == "running":
            raise HTTPException(status_code=409, detail="Pipeline is running")
        asset = snapshot.asset
        if asset is None or asset.sound_timeline is None:
            raise HTTPException(status_code=404, detail="No sound timeline loaded")
        if body.model == "separate" and not asset.has_audio:
            raise HTTPException(
                status_code=422, detail="No original audio to separate."
            )
        try:
            updated, track = add_track(
                asset,
                label=body.label,
                description=body.description,
                model=body.model,
                spoken_text=body.spoken_text,
                start_frame=body.start_frame,
                end_frame=body.end_frame,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        # Audio is generated next through the per-track regenerate call.
        await store.publish_asset_mutation(updated, stage="added track")
        await store.set_complete(stage="Added track.")
        return {"status": "ok", "sound_track_id": str(track.sound_track_id)}

    @router.post("/api/audio/mode")
    async def set_audio_mode(
        mode: Annotated[Literal["generate", "separate"], Form()],
        background_tasks: BackgroundTasks,
    ) -> dict[str, object]:
        snapshot = await store.snapshot()
        if snapshot.status == "running":
            raise HTTPException(status_code=409, detail="Pipeline is running")
        asset = snapshot.asset
        if asset is None:
            raise HTTPException(status_code=404, detail="No video asset loaded")
        try:  # validation only
            route_tracks(
                asset.sound_timeline or SoundTimeline(),
                mode=mode,
                has_audio=asset.has_audio,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if mode == asset.audio_mode:
            await store.publish_asset_mutation(asset, stage="changing audio mode")
            await store.set_complete(stage=f"Audio mode: {mode.capitalize()}")
            return {"status": "ok"}
        if mode == "separate" and asset.inactive_timeline is None:
            # No separation timeline yet: plan it from the audio; the mode changes when it is ready.
            await store.set_running(stage="planning separation")
            background_tasks.add_task(run_separation_planning_pipeline, asset, store)
            return {"status": "queued"}
        # The preview video changed with the mode, so the media reloads (asset_version bump).
        await store.set_complete_asset(
            switch_audio_mode(asset, mode), stage=f"Audio mode: {mode.capitalize()}"
        )
        return {"status": "ok"}

    @router.post("/api/audio/residual")
    async def set_residual_enabled(
        background_tasks: BackgroundTasks,
        enabled: Annotated[bool, Form()],
    ) -> dict[str, object]:
        snapshot = await store.snapshot()
        if snapshot.status == "running":
            raise HTTPException(status_code=409, detail="Pipeline is running")
        if snapshot.asset is None:
            raise HTTPException(status_code=404, detail="No video asset loaded")
        if enabled and not (
            snapshot.asset.has_audio and snapshot.asset.original_audio_path
        ):
            raise HTTPException(
                status_code=422,
                detail="원본 오디오가 없어 잔여 소리를 켤 수 없습니다.",
            )
        background_tasks.add_task(
            run_residual_toggle_pipeline, snapshot.asset, store, enabled
        )
        await store.publish_asset_mutation(snapshot.asset, stage="queued residual update")
        return {"status": "queued"}

    @router.get("/api/rows/timeline")
    async def get_timeline_rows() -> dict[str, object]:
        snapshot = await store.snapshot()
        rows = [] if snapshot.asset is None else timeline_rows(snapshot.asset)
        return {"version": snapshot.version, "rows": rows}

    @router.get("/api/rows/current-frame")
    async def get_current_frame(frame: int) -> dict[str, object]:
        snapshot = await store.snapshot()
        if snapshot.asset is None:
            return {"version": snapshot.version, "frame": frame, "rows": None}
        return {
            "version": snapshot.version,
            "frame": frame,
            "rows": current_frame_rows(snapshot.asset, frame),
        }

    @router.get("/api/tracks/window")
    async def get_tracking_window(
        start_frame: int, end_frame: int
    ) -> dict[str, object]:
        snapshot = await store.snapshot()
        if snapshot.asset is None:
            return {
                "version": snapshot.version,
                "start_frame": start_frame,
                "end_frame": end_frame,
                "tracks": [],
            }
        start = max(0, start_frame)
        end = min(snapshot.asset.frame_count - 1, end_frame)
        if end < start:
            raise HTTPException(status_code=400, detail="Invalid frame window")
        return {
            "version": snapshot.version,
            "start_frame": start,
            "end_frame": end,
            "tracks": tracking_window_rows(snapshot.asset, start, end),
        }

    @router.get("/api/frames/tracking-overlay")
    async def get_tracking_overlay(frame: int) -> Response:
        snapshot = await store.snapshot()
        if snapshot.asset is None:
            raise HTTPException(status_code=404, detail="No video asset loaded")
        if frame < 0 or frame >= snapshot.asset.frame_count:
            raise HTTPException(status_code=400, detail="Frame out of range")
        return Response(
            render_tracking_overlay(snapshot.asset, frame),
            media_type="image/png",
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/api/keyframes/{keyframe_id}")
    async def get_keyframe(keyframe_id: str) -> FileResponse:
        snapshot = await store.snapshot()
        if snapshot.asset is None:
            raise HTTPException(status_code=404, detail="No video asset loaded")
        path = _keyframe_response_path(snapshot.asset, keyframe_id)
        if path is None:
            raise HTTPException(status_code=404, detail="No keyframe image available")
        return FileResponse(
            path,
            media_type="image/jpeg",
            headers={"Cache-Control": "no-store"},
        )

    @router.post("/api/runs")
    async def start_run(
        background_tasks: BackgroundTasks,
        video: Annotated[UploadFile, File()],
        work_dir: Annotated[str | None, Form()] = None,
        server_url: Annotated[str | None, Form()] = None,
        scene_threshold: Annotated[float, Form()] = DEFAULT_SCENE_THRESHOLD,
        max_keyframes_per_scene: Annotated[
            int, Form()
        ] = DEFAULT_MAX_KEYFRAMES_PER_SCENE,
    ) -> dict[str, object]:
        if not video.filename:
            raise HTTPException(status_code=400, detail="No video filename provided")

        nonlocal run_project_id
        # Each run gets its own project directory so derived files never overwrite another project's.
        project_dir = new_project_dir(_root_dir(work_dir))
        write_meta(project_dir, video.filename)
        upload_path = _write_upload(project_dir, video.filename, await video.read())
        run_project_id = project_dir.name

        options = PipelineOptions(
            scene_threshold=scene_threshold,
            max_keyframes_per_scene=max_keyframes_per_scene,
            server_url=server_url or DEFAULT_SERVER_URL or None,
        )
        background_tasks.add_task(
            run_uploaded_video_pipeline,
            upload_path,
            project_dir,
            store,
            options,
        )
        # Drop the previous project from the UI right away (it stays in the project history).
        await store.clear()
        await store.set_running(stage="queued")
        return {"status": "queued"}

    @router.post("/api/asset/import")
    async def import_asset(
        video: Annotated[UploadFile, File()],
        asset: Annotated[UploadFile, File()],
        work_dir: Annotated[str | None, Form()] = None,
    ) -> dict[str, object]:
        snapshot = await store.snapshot()
        if snapshot.status == "running":
            return {"status": "ignored", "detail": "Pipeline is already running"}
        if not video.filename:
            raise HTTPException(status_code=400, detail="No video filename provided")
        if not asset.filename:
            raise HTTPException(status_code=400, detail="No asset filename provided")

        try:
            imported_asset = VideoAsset.model_validate_json(await asset.read())
        except ValidationError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid VideoAsset JSON: {exc}",
            ) from exc

        root_dir = _root_dir(work_dir)
        upload_path = _write_upload(
            root_dir,
            video.filename,
            await video.read(),
        )
        try:
            resolved_video_asset = await _resolve_import_video(upload_path, root_dir)
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid video: {exc}",
            ) from exc
        if resolved_video_asset.frame_count != imported_asset.frame_count:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Imported video frame count does not match VideoAsset: "
                    f"{resolved_video_asset.frame_count} != "
                    f"{imported_asset.frame_count}"
                ),
            )

        sam3_tracking_path = resolved_video_asset.sam3_tracking_path
        if sam3_tracking_path is not None and not sam3_tracking_path.exists():
            sam3_tracking_path = None
        update = {
            "source_path": resolved_video_asset.source_path,
            "sam3_tracking_path": sam3_tracking_path,
        }
        # The JSON's audio fields win when its files exist; the upload may be the
        # prepared (audio-stripped, letterboxed) video.
        raw_video_path = imported_asset.raw_video_path
        if raw_video_path is None or not raw_video_path.exists():
            update["raw_video_path"] = resolved_video_asset.raw_video_path
        original_audio_path = imported_asset.original_audio_path
        if original_audio_path is None or not original_audio_path.exists():
            update["original_audio_path"] = resolved_video_asset.original_audio_path
            update["has_audio"] = resolved_video_asset.has_audio
        imported_asset = imported_asset.model_copy(update=update)
        await store.set_complete_asset(imported_asset, stage="imported asset")
        return {"status": "imported"}

    @router.get("/api/bundle")
    async def export_bundle() -> Response:
        snapshot = await store.snapshot()
        root = None if snapshot.asset is None else _asset_work_root(snapshot.asset)
        if snapshot.asset is None or root is None:
            raise HTTPException(status_code=404, detail="No video asset loaded")
        buffer = io.BytesIO()
        await asyncio.to_thread(write_bundle, snapshot.asset, root, buffer)
        name = f"{snapshot.asset.source_path.stem}-bundle.zip"
        return Response(
            buffer.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    @router.post("/api/bundle/import")
    async def import_bundle(
        bundle: Annotated[UploadFile, File()],
        work_dir: Annotated[str | None, Form()] = None,
    ) -> dict[str, object]:
        snapshot = await store.snapshot()
        if snapshot.status == "running":
            raise HTTPException(status_code=409, detail="Pipeline is already running")
        nonlocal run_project_id
        project_dir = new_project_dir(_root_dir(work_dir))
        try:
            imported_asset = await asyncio.to_thread(
                read_bundle, bundle.file, project_dir
            )
        except ValueError as exc:
            shutil.rmtree(project_dir, ignore_errors=True)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        name = Path(bundle.filename or "").stem.removesuffix("-bundle")
        write_meta(project_dir, name or imported_asset.source_path.stem)
        run_project_id = None
        await store.set_complete_asset(imported_asset, stage="imported bundle")
        return {"status": "imported"}

    # ponytail: the project history lives under the default work dir; the per-request work_dir override is not listed.
    @router.get("/api/projects")
    async def get_projects() -> list[dict[str, object]]:
        snapshot = await store.snapshot()
        running_id = run_project_id if snapshot.status == "running" else None
        return await asyncio.to_thread(list_projects, _root_dir(None), running_id)

    @router.post("/api/projects/{project_id}/open")
    async def open_project(project_id: str) -> dict[str, object]:
        nonlocal run_project_id
        path = _existing_project(project_id)
        snapshot = await store.snapshot()
        if snapshot.status == "running":
            raise HTTPException(status_code=409, detail="Pipeline is running")
        try:
            asset = await asyncio.to_thread(load_project, path)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if project_dir_of(asset) != path.resolve():
            raise HTTPException(
                status_code=409, detail="Project files were moved; reopen from a bundle"
            )
        run_project_id = None
        await store.set_complete_asset(asset, stage="opened project")
        return {"status": "opened"}

    @router.delete("/api/projects/{project_id}")
    async def delete_project(project_id: str) -> dict[str, object]:
        path = _existing_project(project_id)
        snapshot = await store.snapshot()
        current = (
            snapshot.asset is not None
            and project_dir_of(snapshot.asset) == path.resolve()
        )
        if snapshot.status == "running" and (current or run_project_id == project_id):
            raise HTTPException(status_code=409, detail="Project is running")
        if current:
            await store.clear()
        await asyncio.to_thread(shutil.rmtree, path)
        return {"status": "deleted"}

    @router.post("/api/sound-timeline/reset-run")
    async def reset_sound_timeline(
        background_tasks: BackgroundTasks,
    ) -> dict[str, object]:
        snapshot = await store.snapshot()
        if snapshot.status == "running":
            return {"status": "ignored", "detail": "Pipeline is already running"}
        if snapshot.asset is None:
            raise HTTPException(status_code=404, detail="No video asset loaded")
        video_asset = snapshot.asset.model_copy(
            update={
                "sound_timeline": None,
                "synthesized_video_path": None,
                "sound_event_audio_artifacts": [],
                "sound_track_audio_artifacts": [],
                "separated_stem_paths": {},
                "residual_path": None,
                "residual_enabled": False,
                "track_status": {},
            }
        )
        await store.publish_asset_mutation(
            video_asset,
            stage="reset sound timeline",
        )
        background_tasks.add_task(run_sound_timeline_pipeline, video_asset, store)
        return {"status": "queued"}

    @router.post("/api/audio/generate")
    async def generate_audio(
        background_tasks: BackgroundTasks,
        server_url: Annotated[str | None, Form()] = None,
        asset: Annotated[UploadFile | None, File()] = None,
    ) -> dict[str, object]:
        snapshot = await store.snapshot()
        if snapshot.status == "running":
            return {"status": "ignored", "detail": "Pipeline is already running"}

        if asset is not None:
            try:
                import_asset = VideoAsset.model_validate_json(await asset.read())
                await store.set_complete_asset(
                    import_asset, stage="applied timeline edits"
                )
                video_asset = import_asset
            except ValidationError as exc:
                raise HTTPException(
                    status_code=400, detail=f"Invalid VideoAsset JSON: {exc}"
                ) from exc
        else:
            if snapshot.asset is None:
                raise HTTPException(status_code=404, detail="No video asset loaded")
            video_asset = snapshot.asset

        await store.publish_asset_mutation(
            video_asset,
            stage="queued audio generation",
        )
        background_tasks.add_task(
            run_audio_generation_pipeline,
            video_asset,
            store,
            server_url or DEFAULT_SERVER_URL or None,
        )
        return {"status": "queued"}

    @router.get("/events")
    async def events() -> StreamingResponse:
        async def event_stream():
            snapshot = await store.snapshot()
            yield _sse(snapshot)
            version = snapshot.version
            while True:
                snapshot = await store.wait_for_change(version)
                version = snapshot.version
                yield _sse(snapshot)

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    return router


def _track_uuid(sound_track_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(sound_track_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Sound track not found") from exc


def _reject_track_level_event(asset: VideoAsset, sound_event_id: str) -> None:
    timeline = asset.sound_timeline
    if timeline is None:
        return
    event = next(
        (e for e in timeline.sound_events if str(e.sound_event_id) == sound_event_id),
        None,
    )
    track = event and next(
        (t for t in timeline.sound_tracks if t.sound_track_id == event.sound_track_id),
        None,
    )
    if track is not None and track.generation_model in ("separate", "selsync"):
        raise HTTPException(
            status_code=409,
            detail="분리/SelSync 트랙의 이벤트는 개별로 재생성할 수 없습니다. 트랙 재생성을 쓰세요.",
        )


def _root_dir(work_dir: str | None) -> Path:
    root_dir = Path(work_dir or DEFAULT_WORK_DIR or tempfile.gettempdir())
    if root_dir.name != "v2a-inspect-ui":
        root_dir /= "v2a-inspect-ui"
    return root_dir


def _existing_project(project_id: str) -> Path:
    try:
        path = project_path(_root_dir(None), project_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Project not found") from exc
    if not path.is_dir():
        raise HTTPException(status_code=404, detail="Project not found")
    return path


def _write_upload(root_dir: Path, filename: str, data: bytes) -> Path:
    upload_dir = root_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    upload_path = upload_dir / f"{uuid.uuid4()}{Path(filename).suffix}"
    upload_path.write_bytes(data)
    return upload_path


async def _resolve_import_video(upload_path: Path, root_dir: Path) -> VideoAsset:
    try:
        probe = probe_prepared_video(upload_path)
    except Exception:
        return await asyncio.to_thread(prepare_video, upload_path, root_dir)
    original_audio_path = await asyncio.to_thread(
        extract_original_audio,
        upload_path,
        root_dir / f"{upload_path.stem}.original.wav",
    )
    return VideoAsset(
        source_path=probe.path,
        frame_count=probe.frame_count,
        raw_video_path=upload_path,
        original_audio_path=original_audio_path,
        has_audio=original_audio_path is not None,
    )


def _summary_payload(snapshot: VideoAssetSnapshot) -> dict[str, object]:
    asset = snapshot.asset
    return {
        "status": snapshot.status,
        "stage": snapshot.current_stage,
        "error": snapshot.error,
        "version": snapshot.version,
        "asset_version": snapshot.asset_version,
        "updated_at": snapshot.updated_at.isoformat(),
        "video": None
        if asset is None
        else {
            "video_id": str(asset.video_id),
            "frame_count": asset.frame_count,
            "fps": asset.fps,
            "duration_sec": asset.duration_sec,
            "width": asset.width,
            "height": asset.height,
        },
        "timeline_rows": [] if asset is None else timeline_rows(asset),
        "audio_tracks": []
        if asset is None
        else [
            item.model_dump(mode="json") for item in asset.sound_track_audio_artifacts
        ],
        "audio_events": []
        if asset is None
        else [
            item.model_dump(mode="json") for item in asset.sound_event_audio_artifacts
        ],
        "track_targets": {} if asset is None else track_highlight_targets(asset),
        "track_target_fallbacks": {}
        if asset is None
        else track_highlight_fallbacks(asset),
    }


def _audio_file_response(path: Path, missing_detail: str) -> FileResponse:
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail=missing_detail)
    return FileResponse(
        path,
        media_type="audio/wav",
        headers={"Accept-Ranges": "bytes"},
    )


def _keyframe_response_path(asset: VideoAsset, keyframe_id: str) -> Path | None:
    for scene in asset.initial_scenes:
        for keyframe in scene.keyframes:
            if str(keyframe.keyframe_id) != keyframe_id:
                continue
            try:
                path = keyframe.image_path.resolve(strict=True)
                root = asset.source_path.resolve(strict=True).parent
            except OSError:
                return None
            if path.suffix.lower() not in {".jpg", ".jpeg"}:
                return None
            if not path.is_file() or not path.is_relative_to(root):
                return None
            return path
    return None


def _delete_asset_work_dir(asset: VideoAsset) -> Path | None:
    root = _asset_work_root(asset)
    if root is None or not root.exists():
        return None
    shutil.rmtree(root)
    return root


def _asset_work_root(asset: VideoAsset) -> Path | None:
    root = asset.source_path.parent
    if root.name == "uploads":
        root = root.parent
    root = root.resolve()
    if root.name != "v2a-inspect-ui" and root.parent.name != "projects":
        return None
    return root


def _full_snapshot_payload(snapshot: VideoAssetSnapshot) -> dict[str, object]:
    payload = _summary_payload(snapshot)
    payload["asset"] = (
        None
        if snapshot.asset is None
        else snapshot.asset.model_dump(mode="json", exclude_computed_fields=True)
    )
    return payload


def _sse(snapshot: VideoAssetSnapshot) -> str:
    data = {
        "version": snapshot.version,
        "asset_version": snapshot.asset_version,
        "status": snapshot.status,
        "stage": snapshot.current_stage,
        "error": snapshot.error,
    }
    return f"event: asset_update\ndata: {json.dumps(data)}\n\n"
