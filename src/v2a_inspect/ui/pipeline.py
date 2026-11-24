from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
import shutil
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal
from uuid import UUID

import numpy as np
from moviepy import AudioFileClip
from scipy.io import wavfile

from v2a_inspect.agents.sound_timeline import run_sound_timeline_agent_parallel
from v2a_inspect.client import SAM3Client, VideoClient
from v2a_inspect.config import settings
from v2a_inspect.models import (
    AudioPlan,
    AudioPlanItem,
    InitialScene,
    SoundEvent,
    SoundEventAudioArtifact,
    SoundTrack,
    SoundTrackAudioArtifact,
    TrackStatus,
    VideoAsset,
)
from v2a_inspect.audio_generation.audyssey import (
    generate_track,
    separate_track,
    source_plan,
    track_description,
)
from v2a_inspect.audio_generation.client import generate_audio_for_item
from v2a_inspect.audio_generation.clip import render_clip_wav, resolve_clip
from v2a_inspect.audio_generation.plan import build_audio_plan
from v2a_inspect.audio_generation.residual import compute_residual
from v2a_inspect.audio_generation.routing import (
    backfill_agent_model,
    generation_route,
    route_tracks,
)
from v2a_inspect.audio_generation.mix import (
    mix_audio_into_video,
    mix_audio_items_to_wav,
)
from v2a_inspect.separation_planning import (
    plan_separation,
    stage_summary,
    switch_audio_mode,
)
from v2a_inspect.preprocessing import (
    analyze_initial_scene,
    build_visual_identity_layer,
    compute_visual_events,
    detect_initial_scenes,
    extract_keyframes_for_initial_scene,
    prepare_video,
    sam3_tracking_video_path,
    track_initial_scenes_object_seeds,
)

from .store import VideoAssetStore
from .track_edit import _AGENT_MODEL, delete_track, reset_clips


@dataclass(frozen=True)
class PipelineOptions:
    scene_threshold: float = 27.0
    max_keyframes_per_scene: int = 20
    server_url: str | None = None


async def run_uploaded_video_pipeline(
    upload_path: Path,
    work_dir: Path,
    store: VideoAssetStore,
    options: PipelineOptions,
) -> None:
    """Run the full pipeline and publish each completed VideoAsset stage."""

    try:
        loop = asyncio.get_running_loop()
        await store.set_running(stage="prepare video")
        video_asset = await asyncio.to_thread(prepare_video, upload_path, work_dir)
        await store.set_asset(video_asset, stage="prepared video")

        await store.touch(stage="detect scenes")
        scenes = await asyncio.to_thread(
            detect_initial_scenes,
            video_asset,
            threshold=options.scene_threshold,
        )
        video_asset = video_asset.model_copy(update={"initial_scenes": scenes})
        await store.publish_asset_mutation(video_asset, stage="detected scenes")

        await store.touch(stage="extract keyframes")
        video_asset = await _extract_keyframes_incrementally(
            video_asset,
            work_dir,
            store,
            max_keyframes_per_scene=options.max_keyframes_per_scene,
        )
        await store.publish_asset_mutation(video_asset, stage="extracted keyframes")

        await store.touch(stage="analyze scenes")
        video_asset = await _analyze_scenes_incrementally(
            video_asset,
            store,
        )
        await store.publish_asset_mutation(video_asset, stage="analyzed scenes")

        await store.touch(stage="track objects")
        video_asset = await _track_objects(video_asset, options.server_url, store)
        await store.publish_asset_mutation(video_asset, stage="tracked objects")

        await store.touch(stage="build visual identity")
        video_asset = await asyncio.to_thread(build_visual_identity_layer, video_asset)
        await store.publish_asset_mutation(video_asset, stage="built visual identity")

        await store.touch(stage="compute visual events")
        video_asset = await asyncio.to_thread(compute_visual_events, video_asset)
        await store.publish_asset_mutation(video_asset, stage="computed visual events")

        await store.touch(stage="build sound timeline")
        on_sound_change = _threadsafe_publish_callback(loop, store, video_asset)
        await asyncio.to_thread(
            run_sound_timeline_agent_parallel,
            video_asset,
            segment_seconds=settings.agent_sound_timeline_segment_seconds,
            max_workers=settings.agent_sound_timeline_max_workers,
            on_change=on_sound_change,
        )
        video_asset = _apply_track_routing(video_asset)
        await store.publish_asset_mutation(video_asset, stage="built sound timeline")

        await store.set_complete()
    except Exception as exc:  # noqa: BLE001 - UI needs stage-specific failure text.
        await store.set_error(str(exc))


async def run_sound_timeline_pipeline(
    video_asset: VideoAsset,
    store: VideoAssetStore,
) -> None:
    """Rebuild only the final SoundTimeline stage for an existing VideoAsset."""

    try:
        loop = asyncio.get_running_loop()
        await store.touch(stage="build sound timeline")
        on_sound_change = _threadsafe_publish_callback(loop, store, video_asset)
        await asyncio.to_thread(
            run_sound_timeline_agent_parallel,
            video_asset,
            segment_seconds=settings.agent_sound_timeline_segment_seconds,
            max_workers=settings.agent_sound_timeline_max_workers,
            on_change=on_sound_change,
        )
        video_asset = _apply_track_routing(video_asset)
        await store.publish_asset_mutation(video_asset, stage="built sound timeline")
        await store.set_complete()
    except Exception as exc:  # noqa: BLE001 - UI needs stage-specific failure text.
        await store.set_error(str(exc))


def _apply_track_routing(video_asset: VideoAsset) -> VideoAsset:
    if video_asset.sound_timeline is None:
        return video_asset
    routed = route_tracks(
        video_asset.sound_timeline,
        mode=video_asset.audio_mode,
        has_audio=video_asset.has_audio,
    )
    return video_asset.model_copy(update={"sound_timeline": routed})


async def _extract_keyframes_incrementally(
    video_asset: VideoAsset,
    work_dir: Path,
    store: VideoAssetStore,
    *,
    max_keyframes_per_scene: int,
) -> VideoAsset:
    updated_scenes = list(video_asset.initial_scenes)
    for scene_index, scene in enumerate(video_asset.initial_scenes):
        keyframes = await asyncio.to_thread(
            extract_keyframes_for_initial_scene,
            video_asset,
            scene,
            work_dir,
            max_keyframes_per_scene,
        )
        updated_scenes[scene_index] = scene.model_copy(update={"keyframes": keyframes})
        video_asset = video_asset.model_copy(update={"initial_scenes": updated_scenes})
        await store.publish_asset_mutation(
            video_asset,
            stage=f"extracted keyframes {scene_index + 1}/{len(updated_scenes)}",
        )
    return video_asset


async def _analyze_scenes_incrementally(
    video_asset: VideoAsset,
    store: VideoAssetStore,
) -> VideoAsset:
    updated_scenes = list(video_asset.initial_scenes)
    completed = 0
    batch_size = settings.llm_initial_scene_analysis_batch_size
    for batch_start in range(0, len(updated_scenes), batch_size):
        batch = updated_scenes[batch_start : batch_start + batch_size]
        tasks = [
            asyncio.create_task(_analyze_scene(batch_start + offset, scene))
            for offset, scene in enumerate(batch)
        ]
        for task in asyncio.as_completed(tasks):
            scene_index, analyzed_scene = await task
            updated_scenes[scene_index] = analyzed_scene
            completed += 1
            video_asset = video_asset.model_copy(
                update={"initial_scenes": updated_scenes}
            )
            await store.publish_asset_mutation(
                video_asset,
                stage=f"analyzed scenes {completed}/{len(updated_scenes)}",
            )
    return video_asset


async def _analyze_scene(
    scene_index: int,
    scene: InitialScene,
) -> tuple[int, InitialScene]:
    analyzed_scene = await asyncio.to_thread(analyze_initial_scene, scene)
    return scene_index, analyzed_scene


async def _track_objects(
    video_asset: VideoAsset,
    server_url: str | None,
    store: VideoAssetStore,
) -> VideoAsset:
    tracking_video_path = sam3_tracking_video_path(video_asset)
    async with VideoClient(base_url=server_url) as video_client:
        try:
            upload_response = await video_client.upload(str(tracking_video_path))
        except Exception as exc:
            raise RuntimeError(f"upload tracking video failed: {exc}") from exc
    async with SAM3Client(base_url=server_url) as sam_client:
        for scene_index in range(len(video_asset.initial_scenes)):
            scene_number = scene_index + 1
            scene_count = len(video_asset.initial_scenes)
            try:
                video_asset = await track_initial_scenes_object_seeds(
                    video_asset,
                    video_id=upload_response.video_id,
                    sam_client=sam_client,
                    scene_indexes=[scene_index],
                )
            except Exception as exc:
                raise RuntimeError(
                    f"track objects scene {scene_number}/{scene_count} failed: {exc}"
                ) from exc
            video_asset = await _rebuild_visual_layers(video_asset)
            await store.publish_asset_mutation(
                video_asset,
                stage=f"tracked objects {scene_number}/{scene_count}",
            )
    return video_asset


async def _rebuild_visual_layers(video_asset: VideoAsset) -> VideoAsset:
    video_asset = await asyncio.to_thread(build_visual_identity_layer, video_asset)
    return await asyncio.to_thread(compute_visual_events, video_asset)


def _threadsafe_publish_callback(
    loop: asyncio.AbstractEventLoop,
    store: VideoAssetStore,
    video_asset: VideoAsset,
) -> Callable[[str], None]:
    def publish(stage: str) -> None:
        future = asyncio.run_coroutine_threadsafe(
            store.publish_asset_mutation(video_asset, stage=stage),
            loop,
        )
        future.result()

    return publish


async def run_audio_generation_pipeline(
    video_asset: VideoAsset,
    store: VideoAssetStore,
    server_url: str | None,
) -> None:
    try:
        # Hand-replaced tracks go back to the chosen mode before a full run.
        video_asset = _apply_track_routing(video_asset)
        timeline = video_asset.sound_timeline
        if not timeline:
            raise ValueError("No sound timeline found in video asset.")
        if not timeline.sound_events and not any(
            t.generation_model in _TRACK_LEVEL_MODELS for t in timeline.sound_tracks
        ):
            raise ValueError("No audio items to generate.")
        # The audio changes, so every trim/move is dropped.
        timeline = reset_clips(timeline)
        video_asset = video_asset.model_copy(update={"sound_timeline": timeline})

        previous_stems = {
            a.sound_track_id: a
            for a in video_asset.sound_track_audio_artifacts
            if a.origin != "mixed"
        }
        await store.set_running(stage="preparing audio plan")
        video_asset = video_asset.model_copy(
            update={
                "synthesized_video_path": None,
                "sound_event_audio_artifacts": [],
                "sound_track_audio_artifacts": [],
                "track_status": {},
            }
        )
        await store.publish_asset_mutation(video_asset, stage="preparing audio plan")

        video_duration = video_asset.duration_sec
        fps = video_asset.fps
        work_root = _audio_work_root(video_asset)
        event_dir = work_root / "audio" / "events"
        track_dir = work_root / "audio" / "tracks"
        event_dir.mkdir(parents=True, exist_ok=True)
        track_dir.mkdir(parents=True, exist_ok=True)

        event_artifacts: list[SoundEventAudioArtifact] = []
        track_artifacts: list[SoundTrackAudioArtifact] = []
        failed_labels: list[str] = []
        silent_track_ids: set[UUID] = set()

        for track in timeline.sound_tracks:
            method = track.generation_model
            if method not in _TRACK_LEVEL_MODELS:
                continue
            track_id = track.sound_track_id
            events = [e for e in timeline.sound_events if e.sound_track_id == track_id]
            video_asset = await _set_track_status(
                video_asset, store, track_id, TrackStatus(state="running")
            )
            try:
                artifact = await _generate_track_stem(
                    video_asset,
                    track,
                    method=method,
                    description=track_description(track, events),
                    seed=settings.selsync_seed if method == "selsync" else None,
                    attempt=1 if method == "separate" else None,
                    out_dir=track_dir,
                )
            except Exception as exc:  # noqa: BLE001 - collect per-track failures.
                failed_labels.append(track.label)
                status = TrackStatus(state="failed", error=str(exc))
                if track_id in previous_stems:
                    track_artifacts.append(previous_stems[track_id])
            else:
                if method == "selsync" and _silent_in_events(
                    artifact.path, events, fps
                ):
                    # SelSync placed no sound inside the agent's events: use t2a instead.
                    silent_track_ids.add(track_id)
                    continue
                track_artifacts.append(artifact)
                if method == "separate":
                    video_asset = video_asset.model_copy(
                        update={
                            "separated_stem_paths": {
                                **video_asset.separated_stem_paths,
                                track_id: artifact.path,
                            }
                        }
                    )
                status = TrackStatus(state="done")
            video_asset = await _set_track_status(video_asset, store, track_id, status)

        if silent_track_ids:
            timeline = timeline.model_copy(
                update={
                    "sound_tracks": [
                        t.model_copy(update={"generation_model": "t2a"})
                        if t.sound_track_id in silent_track_ids
                        else t
                        for t in timeline.sound_tracks
                    ]
                }
            )
            video_asset = video_asset.model_copy(update={"sound_timeline": timeline})

        audio_plan = build_audio_plan(
            timeline,
            fps=fps,
            total_duration=video_duration,
        )
        item_context = _audio_item_context(timeline, audio_plan)
        event_items = [
            item
            for item in audio_plan.items
            if item.generation_model not in _TRACK_LEVEL_MODELS
        ]

        video_id: str | None = None
        if any(item.generation_model == "v2a" for item in event_items):
            await store.touch(stage="uploading video for V2A generation")
            async with VideoClient(base_url=server_url) as video_client:
                try:
                    res = await video_client.upload(str(video_asset.source_path))
                    video_id = res.video_id
                except Exception:
                    pass

        n_items = len(event_items)

        event_track_failures: dict[UUID, str] = {}
        for i, item in enumerate(event_items, 1):
            context = item_context.get(item.item_id)
            if context is None:
                continue
            event, track = context
            await store.touch(stage=f"generating audio {i}/{n_items}")
            duration = item.time[1] - item.time[0]
            event_path = _event_audio_path(event_dir, item.item_id, track.label)
            raw_path = event_path.with_name(f".{event_path.stem}.generated.wav")

            try:
                audio_file = await asyncio.to_thread(
                    generate_audio_for_item,
                    kind=item.type,
                    description=item.description,
                    out_path=str(raw_path),
                    duration=duration,
                    video_id=video_id,
                    fps=fps,
                    time=item.time,
                    generation_model=item.generation_model,
                    spoken_text=item.spoken_text,
                    server_url=server_url,
                )
            except Exception as exc:  # noqa: BLE001 - collect per-item failures.
                failed_labels.append(track.label)
                event_track_failures.setdefault(track.sound_track_id, str(exc))
                continue

            normalized = await asyncio.to_thread(
                _convert_audio_to_wav,
                Path(audio_file),
                event_path,
            )
            if raw_path.exists() and raw_path != event_path:
                raw_path.unlink(missing_ok=True)
            if normalized is None:
                event_track_failures.setdefault(
                    track.sound_track_id, "No audio file was generated."
                )
                continue

            event_artifacts.append(
                SoundEventAudioArtifact(
                    sound_event_id=event.sound_event_id,
                    sound_track_id=track.sound_track_id,
                    path=normalized,
                    duration_sec=round(duration, 3),
                    generation_model=item.generation_model,
                    description=item.description,
                )
            )

        event_track_ids = {
            item_context[item.item_id][1].sound_track_id
            for item in event_items
            if item.item_id in item_context
        }
        for track_id in event_track_ids:
            if track_id in event_track_failures:
                status = TrackStatus(
                    state="failed", error=event_track_failures[track_id]
                )
            else:
                status = TrackStatus(state="done")
            video_asset = await _set_track_status(video_asset, store, track_id, status)

        if not event_artifacts and not track_artifacts:
            # Short top-bar copy; per-track details live in TrackStatus.error.
            names = ", ".join(dict.fromkeys(failed_labels))
            raise ValueError(f"{names} failed." if names else "No audio was generated.")

        video_asset = await _refresh_residual(video_asset)
        video_asset = await _rebuild_audio_outputs(
            video_asset, audio_plan, event_artifacts, store, track_artifacts
        )
        await store.set_complete_asset(
            video_asset, stage="Audio generation complete. Ready for preview."
        )
    except Exception as exc:
        await store.set_error(_top_bar_error(exc, "Audio generation failed."))


async def run_event_audio_delete_pipeline(
    video_asset: VideoAsset,
    store: VideoAssetStore,
    sound_event_id: str,
) -> None:
    try:
        timeline = video_asset.sound_timeline
        if timeline is None:
            raise ValueError("No sound timeline found in video asset.")

        removed = [
            item
            for item in video_asset.sound_event_audio_artifacts
            if str(item.sound_event_id) == sound_event_id
        ]
        if not removed:
            raise ValueError("No event audio available.")

        await store.set_running(stage="deleting event audio")
        await asyncio.to_thread(_unlink_audio_artifacts, removed)
        event_artifacts = [
            item
            for item in video_asset.sound_event_audio_artifacts
            if str(item.sound_event_id) != sound_event_id
        ]
        audio_plan = build_audio_plan(
            timeline,
            fps=video_asset.fps,
            total_duration=video_asset.duration_sec,
        )
        video_asset = await _rebuild_audio_outputs(
            video_asset, audio_plan, event_artifacts, store
        )
        await store.set_complete_asset(video_asset, stage="Deleted event audio.")
    except Exception as exc:  # noqa: BLE001 - UI needs stage-specific failure text.
        await store.set_error(str(exc))


async def run_event_audio_regeneration_pipeline(
    video_asset: VideoAsset,
    store: VideoAssetStore,
    sound_event_id: str,
    description: str,
    spoken_text: str | None,
    server_url: str | None,
) -> None:
    track: SoundTrack | None = None
    try:
        timeline = video_asset.sound_timeline
        if timeline is None:
            raise ValueError("No sound timeline found in video asset.")

        event, track = _event_context(
            timeline.sound_events, timeline.sound_tracks, sound_event_id
        )
        next_description = description.strip()
        if not next_description:
            raise ValueError("Audio prompt is required.")
        next_spoken_text = (spoken_text or "").strip() or None
        if track.track_type == "speech" and next_spoken_text is None:
            next_spoken_text = next_description
        if track.track_type != "speech":
            next_spoken_text = None

        updated_event = event.model_copy(
            update={"description": next_description, "spoken_text": next_spoken_text}
        )
        timeline = timeline.model_copy(
            update={
                "sound_events": [
                    updated_event
                    if current.sound_event_id == event.sound_event_id
                    else current
                    for current in timeline.sound_events
                ]
            }
        )
        video_asset = video_asset.model_copy(update={"sound_timeline": timeline})

        await store.set_running(stage="regenerating event audio")
        await store.publish_asset_mutation(video_asset, stage="updated event prompt")

        audio_plan = build_audio_plan(
            timeline,
            fps=video_asset.fps,
            total_duration=video_asset.duration_sec,
        )
        item_context = _audio_item_context(timeline, audio_plan)
        item = next(
            (item for item in audio_plan.items if item.item_id == sound_event_id), None
        )
        context = item_context.get(sound_event_id) if item else None
        if item is None or context is None:
            raise ValueError("No audio plan item found for event.")
        event, track = context

        video_id: str | None = None
        if item.generation_model == "v2a":
            await store.touch(stage="uploading video for V2A generation")
            async with VideoClient(base_url=server_url) as video_client:
                res = await video_client.upload(str(video_asset.source_path))
                video_id = res.video_id

        event_dir = _audio_work_root(video_asset) / "audio" / "events"
        event_dir.mkdir(parents=True, exist_ok=True)
        artifact = await _generate_event_audio_artifact(
            item,
            event,
            track,
            event_dir,
            video_id=video_id,
            fps=video_asset.fps,
            server_url=server_url,
        )
        if artifact is None:
            raise ValueError("No audio file was generated.")

        event_artifacts = [
            current
            for current in video_asset.sound_event_audio_artifacts
            if str(current.sound_event_id) != sound_event_id
        ]
        event_artifacts.append(artifact)
        if track.sound_track_id in video_asset.track_status:
            # Clear an earlier failure now that this track produced audio again.
            video_asset = await _set_track_status(
                video_asset, store, track.sound_track_id, TrackStatus(state="done")
            )
        video_asset = await _rebuild_audio_outputs(
            video_asset, audio_plan, event_artifacts, store
        )
        await store.set_complete_asset(video_asset, stage="Regenerated event audio.")
    except Exception as exc:  # noqa: BLE001 - UI needs stage-specific failure text.
        if track is None:
            await store.set_error(str(exc))
            return
        # Short top-bar copy; the detail stays in the track's status.
        await _set_track_status(
            video_asset,
            store,
            track.sound_track_id,
            TrackStatus(state="failed", error=str(exc)),
        )
        await store.set_error(f"{track.label} failed. Previous audio kept.")


async def run_track_regeneration_pipeline(
    video_asset: VideoAsset,
    store: VideoAssetStore,
    sound_track_id: str,
    method: Literal["separate", "generate"],
    description: str | None,
    seed: int | None,
    model: Literal["selsync", "t2a", "tts"] | None = None,
) -> None:
    try:
        timeline = video_asset.sound_timeline
        if timeline is None:
            raise ValueError("No sound timeline found in video asset.")
        track = next(
            (
                t
                for t in timeline.sound_tracks
                if str(t.sound_track_id) == sound_track_id
            ),
            None,
        )
        if track is None:
            raise ValueError("Sound track not found.")
        track_id = track.sound_track_id

        original_model, original_agent = track.generation_model, track.agent_model
        track = backfill_agent_model(track)
        # "generate" follows the agent's routing for this one track unless the user picked a model.
        route = (
            "separate"
            if method == "separate"
            else model
            or generation_route(
                track, {s.sound_source_id: s for s in timeline.sound_sources}
            )
        )
        update: dict[str, object] = {"generation_model": route}
        if method == "generate" and model:
            update["agent_model"] = _AGENT_MODEL[model]
        if description and description.strip():
            update["description"] = description.strip()
        track = track.model_copy(update=update)
        timeline = timeline.model_copy(
            update={
                "sound_tracks": [
                    track if t.sound_track_id == track_id else t
                    for t in timeline.sound_tracks
                ]
            }
        )
        video_asset = video_asset.model_copy(update={"sound_timeline": timeline})

        previous = next(
            (
                a
                for a in video_asset.sound_track_audio_artifacts
                if a.sound_track_id == track_id and a.origin != "mixed"
            ),
            None,
        )
        events = [e for e in timeline.sound_events if e.sound_track_id == track_id]
        track_dir = _audio_work_root(video_asset) / "audio" / "tracks"
        track_dir.mkdir(parents=True, exist_ok=True)

        await store.set_running(stage="regenerating track")
        video_asset = await _set_track_status(
            video_asset, store, track_id, TrackStatus(state="running")
        )
        try:
            if route in _TRACK_LEVEL_MODELS:
                artifact = await _generate_track_stem(
                    video_asset,
                    track,
                    method=route,
                    description=track_description(track, events),
                    seed=secrets.randbelow(2**31) if seed is None else seed,
                    attempt=((previous.attempt if previous else None) or 0) + 1,
                    out_dir=track_dir,
                )
                new_stems, new_events = [artifact], []
            else:
                new_stems, new_events = (
                    [],
                    await _generate_track_event_artifacts(video_asset, track),
                )
        except Exception as exc:  # noqa: BLE001 - keep previous audio, surface error.
            # Failed switch: keep the edited description but restore the model.
            restored = track.model_copy(
                update={
                    "generation_model": original_model,
                    "agent_model": original_agent,
                }
            )
            video_asset = video_asset.model_copy(
                update={
                    "sound_timeline": timeline.model_copy(
                        update={
                            "sound_tracks": [
                                restored if t.sound_track_id == track_id else t
                                for t in timeline.sound_tracks
                            ]
                        }
                    )
                }
            )
            await _set_track_status(
                video_asset,
                store,
                track_id,
                TrackStatus(state="failed", error=str(exc)),
            )
            # Short top-bar copy; the detail stays in TrackStatus.error.
            await store.set_error(f"{track.label} failed. Previous audio kept.")
            return

        separated = dict(video_asset.separated_stem_paths)
        if route == "separate":
            separated[track_id] = new_stems[0].path
        timeline = reset_clips(
            timeline, {track_id}
        )  # new audio: drop the old trim/move
        video_asset = video_asset.model_copy(
            update={"separated_stem_paths": separated, "sound_timeline": timeline}
        )
        video_asset = await _refresh_residual(video_asset)
        # The user chose the model, so a silent SelSync result is kept (no paid fallback) and flagged.
        status = (
            TrackStatus(state="failed", error=SELSYNC_SILENT_MESSAGE)
            if route == "selsync"
            and _silent_in_events(new_stems[0].path, events, video_asset.fps)
            else TrackStatus(state="done")
        )
        video_asset = await _set_track_status(video_asset, store, track_id, status)

        # Only this track's audio changes; its old stem/events are dropped so it is
        # not mixed twice (a stem-level track keeps no events, an event-level no stem).
        stems = [
            a
            for a in video_asset.sound_track_audio_artifacts
            if a.origin != "mixed" and a.sound_track_id != track_id
        ] + new_stems
        audio_plan = build_audio_plan(
            timeline, fps=video_asset.fps, total_duration=video_asset.duration_sec
        )
        event_artifacts = [
            a
            for a in video_asset.sound_event_audio_artifacts
            if a.sound_track_id != track_id
        ] + new_events
        video_asset = await _rebuild_audio_outputs(
            video_asset, audio_plan, event_artifacts, store, stems
        )
        await store.set_complete_asset(video_asset, stage="Regenerated track audio.")
    except Exception as exc:  # noqa: BLE001 - UI needs stage-specific failure text.
        await store.set_error(str(exc))


async def run_residual_toggle_pipeline(
    video_asset: VideoAsset,
    store: VideoAssetStore,
    enabled: bool,
) -> None:
    try:
        timeline = video_asset.sound_timeline
        if timeline is None:
            raise ValueError("No sound timeline found in video asset.")
        video_asset = video_asset.model_copy(update={"residual_enabled": enabled})
        await store.set_running(stage="mixing audio")
        audio_plan = build_audio_plan(
            timeline, fps=video_asset.fps, total_duration=video_asset.duration_sec
        )
        video_asset = await _rebuild_audio_outputs(
            video_asset, audio_plan, video_asset.sound_event_audio_artifacts, store
        )
        await store.set_complete_asset(video_asset, stage="Updated residual.")
    except Exception:  # noqa: BLE001 - UI needs stage-specific failure text.
        logging.getLogger(__name__).exception("Residual toggle failed")
        await store.set_error("Could not change the rest of original audio.")


async def run_audio_mode_switch(
    video_asset: VideoAsset,
    store: VideoAssetStore,
    mode: Literal["generate", "separate"],
) -> None:
    """Swap the timelines; when the active one has audio, re-render the preview the swap dropped."""
    video_asset = switch_audio_mode(video_asset, mode)
    stage = f"Audio mode: {mode.capitalize()}"
    timeline = video_asset.sound_timeline
    track_ids = {t.sound_track_id for t in timeline.sound_tracks} if timeline else set()
    if timeline and any(
        a.sound_track_id in track_ids for a in video_asset.sound_track_audio_artifacts
    ):
        try:
            await store.set_running(stage="mixing audio")
            audio_plan = build_audio_plan(
                timeline, fps=video_asset.fps, total_duration=video_asset.duration_sec
            )
            video_asset = await _rebuild_audio_outputs(
                video_asset, audio_plan, video_asset.sound_event_audio_artifacts, store
            )
        except Exception:  # noqa: BLE001 - the switch itself succeeded; the preview stays cleared.
            logging.getLogger(__name__).exception(
                "Preview rebuild after the mode switch failed"
            )
    await store.set_complete_asset(video_asset, stage=stage)


async def run_track_clip_pipeline(
    video_asset: VideoAsset,
    store: VideoAssetStore,
) -> None:
    """Re-render the preview after a track's clip fields changed (no regeneration)."""
    try:
        timeline = video_asset.sound_timeline
        if timeline is None:
            raise ValueError("No sound timeline found in video asset.")
        await store.set_running(stage="mixing audio")
        audio_plan = build_audio_plan(
            timeline, fps=video_asset.fps, total_duration=video_asset.duration_sec
        )
        video_asset = await _rebuild_audio_outputs(
            video_asset, audio_plan, video_asset.sound_event_audio_artifacts, store
        )
        await store.set_complete_asset(video_asset, stage="Updated track audio.")
    except Exception:  # noqa: BLE001 - UI needs stage-specific failure text.
        logging.getLogger(__name__).exception("Track clip update failed")
        await store.set_error("Could not update the track audio.")


async def run_track_delete_pipeline(
    video_asset: VideoAsset,
    store: VideoAssetStore,
    sound_track_id: str,
) -> None:
    try:
        track_id = UUID(sound_track_id)
        doomed = [
            p
            for a in video_asset.sound_track_audio_artifacts
            if a.sound_track_id == track_id
            for p in (a.path, a.clip_path)
            if p is not None
        ] + [
            a.path
            for a in video_asset.sound_event_audio_artifacts
            if a.sound_track_id == track_id
        ]
        had_stem = track_id in video_asset.separated_stem_paths
        await store.set_running(stage="removing track")
        video_asset = delete_track(video_asset, track_id)
        if had_stem:
            video_asset = (
                await _refresh_residual(video_asset)
                if video_asset.separated_stem_paths
                else video_asset.model_copy(
                    update={"residual_path": None, "residual_enabled": False}
                )
            )
        assert video_asset.sound_timeline is not None  # delete_track needs one
        audio_plan = build_audio_plan(
            video_asset.sound_timeline,
            fps=video_asset.fps,
            total_duration=video_asset.duration_sec,
        )
        video_asset = await _rebuild_audio_outputs(
            video_asset, audio_plan, video_asset.sound_event_audio_artifacts, store
        )
        await store.set_complete_asset(video_asset, stage="Removed track.")
        for path in doomed:
            path.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001 - UI needs stage-specific failure text.
        logging.getLogger(__name__).exception("Track delete failed")
        await store.set_error("Could not remove the track.")


async def run_separation_planning_pipeline(
    video_asset: VideoAsset,
    store: VideoAssetStore,
) -> None:
    """Plan audible sources, then switch to Separate with them as the timeline."""
    try:
        timeline, dropped = await asyncio.to_thread(plan_separation, video_asset)
        if not timeline.sound_tracks:
            reasons = "; ".join(i.message for i in dropped)
            raise ValueError(
                "No audible sources were found." + (f" {reasons}" if reasons else "")
            )
        # The planned timeline is stored as the inactive one, then swapped in.
        video_asset = switch_audio_mode(
            video_asset.model_copy(update={"inactive_timeline": timeline}), "separate"
        )
        await store.set_complete_asset(
            video_asset, stage=stage_summary(timeline, dropped)
        )
    except Exception as exc:  # noqa: BLE001 - UI needs stage-specific failure text.
        logging.getLogger(__name__).exception("Separation planning failed")
        await store.set_error(f"Could not plan the separation: {exc}")


_TRACK_LEVEL_MODELS = ("separate", "selsync")


def _top_bar_error(exc: Exception, short: str) -> str:
    """Hide tools-server details (host, port, errno) from the top bar."""
    text = str(exc)
    return short if re.search(r"unreachable|Audyssey \S+ failed \(", text) else text


async def _refresh_residual(video_asset: VideoAsset) -> VideoAsset:
    if not (
        video_asset.has_audio
        and video_asset.original_audio_path
        and video_asset.separated_stem_paths
    ):
        return video_asset
    stems = [p for p in video_asset.separated_stem_paths.values() if p.exists()]
    out_path = _audio_work_root(video_asset) / "audio" / "residual.wav"
    await asyncio.to_thread(
        compute_residual, video_asset.original_audio_path, stems, out_path
    )
    return video_asset.model_copy(update={"residual_path": out_path})


async def _set_track_status(
    video_asset: VideoAsset,
    store: VideoAssetStore,
    track_id: UUID,
    status: TrackStatus,
) -> VideoAsset:
    video_asset = video_asset.model_copy(
        update={"track_status": {**video_asset.track_status, track_id: status}}
    )
    await store.publish_asset_mutation(video_asset, stage="generating track stems")
    return video_asset


async def _generate_track_stem(
    video_asset: VideoAsset,
    track: SoundTrack,
    *,
    method: Literal["separate", "selsync"],
    description: str,
    seed: int | None,
    attempt: int | None,
    out_dir: Path,
) -> SoundTrackAudioArtifact:
    timeline = video_asset.sound_timeline
    events = [
        e
        for e in (timeline.sound_events if timeline else [])
        if e.sound_track_id == track.sound_track_id
    ]
    plan = source_plan(track, events, fps=video_asset.fps, description=description)
    short = str(track.sound_track_id)[:8]
    if method == "separate":
        if video_asset.original_audio_path is None:
            raise ValueError("No original audio to separate.")
        attempt = 1 if attempt is None else attempt
        out_path = out_dir / f"{short}_separate_{attempt}.wav"
        await asyncio.to_thread(
            separate_track, video_asset.original_audio_path, plan, attempt, out_path
        )
        origin = "separated"
    else:
        seed = settings.selsync_seed if seed is None else seed
        out_path = out_dir / f"{short}_selsync_{seed}.wav"
        video_path = video_asset.raw_video_path or video_asset.source_path
        await asyncio.to_thread(generate_track, video_path, plan, seed, out_path)
        origin = "generated"
    peaks = await asyncio.to_thread(_waveform_peaks, out_path)
    return SoundTrackAudioArtifact(
        sound_track_id=track.sound_track_id,
        track_label=track.label,
        track_type=track.track_type,
        path=out_path,
        duration_sec=round(video_asset.duration_sec, 3),
        event_count=len(events),
        waveform_peaks=peaks,
        origin=origin,
        seed=seed if method == "selsync" else None,
        attempt=attempt if method == "separate" else None,
        description=description,
    )


async def _generate_track_event_artifacts(
    video_asset: VideoAsset, track: SoundTrack
) -> list[SoundEventAudioArtifact]:
    """Regenerate every event of a track; files only replace the old ones if all succeed."""
    timeline = video_asset.sound_timeline
    assert timeline is not None  # callers regenerate tracks of an existing timeline
    plan = build_audio_plan(
        timeline, fps=video_asset.fps, total_duration=video_asset.duration_sec
    )
    context = _audio_item_context(timeline, plan)
    items = [i for i in plan.items if i.track_id == str(track.sound_track_id)]
    if not items:
        raise ValueError("No audio items to generate.")
    event_dir = _audio_work_root(video_asset) / "audio" / "events"
    staging = event_dir / f".regen_{_short_id(track.sound_track_id)}"
    staging.mkdir(parents=True, exist_ok=True)
    try:
        staged = []
        for item in items:
            event, item_track = context[item.item_id]
            artifact = await _generate_event_audio_artifact(
                item,
                event,
                item_track,
                staging,
                video_id=None,
                fps=video_asset.fps,
                server_url=None,
            )
            if artifact is None:
                raise ValueError("No audio file was generated.")
            staged.append((item, artifact))
        artifacts = []
        for item, artifact in staged:
            final = _event_audio_path(event_dir, item.item_id, track.label)
            os.replace(artifact.path, final)
            artifacts.append(artifact.model_copy(update={"path": final}))
        return artifacts
    finally:
        shutil.rmtree(staging, ignore_errors=True)


async def _generate_event_audio_artifact(
    item: AudioPlanItem,
    event: SoundEvent,
    track: SoundTrack,
    event_dir: Path,
    *,
    video_id: str | None,
    fps: float,
    server_url: str | None,
) -> SoundEventAudioArtifact | None:
    duration = item.time[1] - item.time[0]
    event_path = _event_audio_path(event_dir, item.item_id, track.label)
    raw_path = event_path.with_name(f".{event_path.stem}.generated.wav")
    audio_file = await asyncio.to_thread(
        generate_audio_for_item,
        kind=item.type,
        description=item.description,
        out_path=str(raw_path),
        duration=duration,
        video_id=video_id,
        fps=fps,
        time=item.time,
        generation_model=item.generation_model,
        spoken_text=item.spoken_text,
        server_url=server_url,
    )
    normalized = await asyncio.to_thread(
        _convert_audio_to_wav,
        Path(audio_file),
        event_path,
    )
    if raw_path.exists() and raw_path != event_path:
        raw_path.unlink(missing_ok=True)
    if normalized is None:
        return None
    return SoundEventAudioArtifact(
        sound_event_id=event.sound_event_id,
        sound_track_id=track.sound_track_id,
        path=normalized,
        duration_sec=round(duration, 3),
        generation_model=item.generation_model,
        description=item.description,
    )


async def _rebuild_audio_outputs(
    video_asset: VideoAsset,
    audio_plan: AudioPlan,
    event_artifacts: list[SoundEventAudioArtifact],
    store: VideoAssetStore,
    track_artifacts: list[SoundTrackAudioArtifact] | None = None,
) -> VideoAsset:
    timeline = video_asset.sound_timeline
    if timeline is None:
        raise ValueError("No sound timeline found in video asset.")

    if track_artifacts is None:
        # Event-only rebuilds keep the track-level stems already on the asset.
        track_artifacts = [
            a for a in video_asset.sound_track_audio_artifacts if a.origin != "mixed"
        ]
    # Stems only live on tracks that are still track-level; their events are not mixed.
    track_level_ids = {
        str(t.sound_track_id)
        for t in timeline.sound_tracks
        if t.generation_model in _TRACK_LEVEL_MODELS
    }
    stem_artifacts = [
        a
        for a in track_artifacts
        if a.path.exists() and str(a.sound_track_id) in track_level_ids
    ]

    plan_item_ids = {
        item.item_id
        for item in audio_plan.items
        if item.track_id not in track_level_ids
    }
    kept_event_artifacts = [
        item
        for item in event_artifacts
        if str(item.sound_event_id) in plan_item_ids and item.path.exists()
    ]
    generated_audio = {
        str(item.sound_event_id): str(item.path) for item in kept_event_artifacts
    }
    if not generated_audio and not stem_artifacts:
        return video_asset.model_copy(
            update={
                "synthesized_video_path": None,
                "sound_event_audio_artifacts": [],
                "sound_track_audio_artifacts": [],
            }
        )

    await store.touch(stage="mixing track stems")
    video_duration = video_asset.duration_sec
    track_dir = _audio_work_root(video_asset) / "audio" / "tracks"
    track_dir.mkdir(parents=True, exist_ok=True)
    track_items: dict[str, list[AudioPlanItem]] = defaultdict(list)
    for item in audio_plan.items:
        if item.track_id and item.item_id in generated_audio:
            track_items[item.track_id].append(item)

    mixed_artifacts: list[SoundTrackAudioArtifact] = []
    for track in timeline.sound_tracks:
        items = track_items.get(str(track.sound_track_id), [])
        if not items:
            continue
        track_path = _track_audio_path(
            track_dir, str(track.sound_track_id), track.label
        )
        stem_path = await asyncio.to_thread(
            mix_audio_items_to_wav,
            items,
            generated_audio,
            str(track_path),
            total_duration=video_duration,
        )
        if not stem_path or not Path(stem_path).exists():
            continue
        peaks = await asyncio.to_thread(_waveform_peaks, Path(stem_path))
        mixed_artifacts.append(
            SoundTrackAudioArtifact(
                sound_track_id=track.sound_track_id,
                track_label=track.label,
                track_type=track.track_type,
                path=Path(stem_path),
                duration_sec=round(video_duration, 3),
                event_count=len(items),
                waveform_peaks=peaks,
            )
        )

    tracks_by_id = {t.sound_track_id: t for t in timeline.sound_tracks}
    track_artifacts = [
        await asyncio.to_thread(
            _with_clip, a, tracks_by_id[a.sound_track_id], video_duration
        )
        for a in mixed_artifacts + stem_artifacts
    ]
    if not track_artifacts:
        return video_asset.model_copy(
            update={
                "synthesized_video_path": None,
                "sound_event_audio_artifacts": kept_event_artifacts,
                "sound_track_audio_artifacts": [],
            }
        )

    await store.touch(stage="mixing audio")
    preview_plan = AudioPlan(total_duration=video_duration)
    preview_audio: dict[str, str] = {}
    for artifact in track_artifacts:
        item_id = str(artifact.sound_track_id)
        preview_plan.items.append(
            AudioPlanItem(
                item_id=item_id,
                type=artifact.track_type,
                time=(0.0, video_duration),
                description=artifact.track_label,
                volume=tracks_by_id[artifact.sound_track_id].gain,
                track_id=item_id,
                generation_model="stem",
            )
        )
        preview_audio[item_id] = str(artifact.playback_path)
    if (
        video_asset.residual_enabled
        and video_asset.residual_path
        and video_asset.residual_path.exists()
    ):
        preview_plan.items.append(
            AudioPlanItem(
                item_id="residual",
                type="ambience",
                time=(0.0, video_duration),
                description="residual",
                volume=1.0,
                generation_model="stem",
            )
        )
        preview_audio["residual"] = str(video_asset.residual_path)

    output_path = str(_audio_work_root(video_asset) / "preview.mp4")
    result = await asyncio.to_thread(
        mix_audio_into_video,
        video_path=str(video_asset.source_path),
        audio_plan=preview_plan,
        generated_audio=preview_audio,
        output_path=output_path,
        keep_original_audio=False,
    )
    if not result or not Path(result).exists():
        raise RuntimeError("Audio synthesis and mixing failed.")

    return video_asset.model_copy(
        update={
            "synthesized_video_path": Path(result),
            "sound_event_audio_artifacts": kept_event_artifacts,
            "sound_track_audio_artifacts": track_artifacts,
        }
    )


def _with_clip(
    artifact: SoundTrackAudioArtifact, track: SoundTrack, video_duration: float
) -> SoundTrackAudioArtifact:
    """Attach the video-length WAV with the track's trim/move applied (or drop a stale one)."""
    out = artifact.path.with_name(f"{artifact.path.stem}.clip.wav")
    if not track.has_clip:
        out.unlink(missing_ok=True)
        return artifact.model_copy(update={"clip_path": None})
    start, end, offset = resolve_clip(track, artifact.duration_sec)
    render_clip_wav(
        artifact.path,
        out,
        start=start,
        end=end,
        offset=offset,
        video_duration=video_duration,
        segments=track.clip_segments,
    )
    return artifact.model_copy(update={"clip_path": out})


def _audio_item_context(
    timeline,
    audio_plan: AudioPlan,
) -> dict[str, tuple[SoundEvent, SoundTrack]]:
    track_map = {track.sound_track_id: track for track in timeline.sound_tracks}
    event_map = {str(event.sound_event_id): event for event in timeline.sound_events}
    item_context: dict[str, tuple[SoundEvent, SoundTrack]] = {}
    for item in audio_plan.items:
        event = event_map.get(item.item_id)
        track = track_map.get(event.sound_track_id) if event else None
        if event and track:
            item_context[item.item_id] = (event, track)
    return item_context


def _event_context(
    events: list[SoundEvent],
    tracks: list[SoundTrack],
    sound_event_id: str,
) -> tuple[SoundEvent, SoundTrack]:
    event = next(
        (item for item in events if str(item.sound_event_id) == sound_event_id), None
    )
    if event is None:
        raise ValueError("Unknown sound_event_id.")
    track = next(
        (item for item in tracks if item.sound_track_id == event.sound_track_id), None
    )
    if track is None:
        raise ValueError("Unknown sound_track_id.")
    return event, track


def _unlink_audio_artifacts(artifacts: list[SoundEventAudioArtifact]) -> None:
    for artifact in artifacts:
        artifact.path.unlink(missing_ok=True)


def _audio_work_root(video_asset: VideoAsset) -> Path:
    root = video_asset.source_path.parent
    if root.name == "uploads":
        return root.parent
    return root


def _event_audio_path(audio_dir: Path, event_id: str, track_label: str) -> Path:
    return audio_dir / f"event_{_short_id(event_id)}_{_slug(track_label)}.wav"


def _track_audio_path(audio_dir: Path, track_id: str, track_label: str) -> Path:
    return audio_dir / f"track_{_short_id(track_id)}_{_slug(track_label)}.wav"


def _short_id(value: object) -> str:
    return str(value).replace("-", "")[:12] or "unknown"


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-_.").lower()
    return slug[:48] or "track"


def _convert_audio_to_wav(input_path: Path, output_path: Path) -> Path | None:
    if not input_path.exists():
        return None
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        clip = AudioFileClip(str(input_path))
        try:
            clip.write_audiofile(
                str(output_path),
                fps=44100,
                codec="pcm_s16le",
                logger=None,
            )
        finally:
            clip.close()
        return output_path
    except Exception:
        return None


# Quietest per-frame peak inside the events that still counts as audible; the samurai
# clip's missed sword stems peak at -78 to -42 dB, its audible armor stem at -26 dB.
SELSYNC_SILENCE_DB = -35.0
SELSYNC_SILENT_MESSAGE = "SelSync returned silence in the event spans. Regenerate to try another seed or switch the model."


def _silent_in_events(path: Path, events: list[SoundEvent], fps: float) -> bool:
    """True when no 1/fps window inside the events reaches SELSYNC_SILENCE_DB."""
    if not events:
        return False
    try:
        sample_rate, data = wavfile.read(path)
    except Exception:
        return False
    samples = data.astype(np.float32)
    if np.issubdtype(data.dtype, np.integer):
        samples /= np.iinfo(data.dtype).max
    if samples.ndim > 1:
        samples = samples.mean(axis=1)
    hop = max(1, round(sample_rate / fps))
    for event in events:
        seg = samples[event.start_frame_index * hop : (event.end_frame_index + 1) * hop]
        for start in range(0, len(seg), hop):
            rms = float(np.sqrt(np.mean(seg[start : start + hop] ** 2)))
            if 20 * np.log10(rms + 1e-9) >= SELSYNC_SILENCE_DB:
                return False
    return True


def _waveform_peaks(path: Path, *, bucket_count: int = 768) -> list[float]:
    try:
        _sample_rate, data = wavfile.read(path)
    except Exception:
        return []
    if data.size == 0:
        return []

    samples = data.astype(np.float32)
    if np.issubdtype(data.dtype, np.integer):
        info = np.iinfo(data.dtype)
        samples = samples / max(abs(info.min), info.max)
    if samples.ndim > 1:
        samples = np.max(np.abs(samples), axis=1)
    else:
        samples = np.abs(samples)

    buckets = max(1, min(bucket_count, samples.size))
    peaks = [float(chunk.max()) for chunk in np.array_split(samples, buckets)]
    # Fixed dB scale shared by all tracks (-60 dBFS -> 0, 0 dBFS -> 1), so a silent
    # stem draws flat instead of being stretched to full height.
    return [
        round(min(max((20 * np.log10(peak + 1e-9) + 60) / 60, 0.0), 1.0), 4)
        for peak in peaks
    ]
