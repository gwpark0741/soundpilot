from __future__ import annotations

import json
from pathlib import Path

import httpx

from v2a_inspect.config.settings import get_settings
from v2a_inspect.models import SoundEvent, SoundTrack


def track_description(track: SoundTrack, events: list[SoundEvent]) -> str:
    if track.description:
        return track.description
    if get_settings().track_description_default == "first_event" and events:
        return min(events, key=lambda e: e.start_frame_index).description
    return track.label


def source_plan(
    track: SoundTrack, events: list[SoundEvent], *, fps: float, description: str
) -> dict:
    spans = [
        {
            "start": round(e.start_frame_index / fps, 3),
            "end": round(e.end_frame_index / fps, 3),
        }
        for e in sorted(events, key=lambda e: e.start_frame_index)
    ]
    # Audyssey rejects spans with end <= start; zero-length events round to one.
    spans = [s for s in spans if s["end"] > s["start"]]
    return {
        "name": track.label,
        "kind": {"speech": "dialogue"}.get(track.track_type, track.track_type),
        "description": description,
        "spans": spans,
    }


def _post(endpoint: str, files: dict, data: dict, out_path: Path) -> Path:
    settings = get_settings()
    url = settings.audyssey_tools_url
    token = settings.audyssey_tool_token
    headers = {"Authorization": f"Bearer {token.get_secret_value()}"} if token else {}
    try:
        response = httpx.post(
            f"{url}/{endpoint}",
            files=files,
            data=data,
            headers=headers,
            timeout=600,
        )
    except httpx.HTTPError as exc:
        raise RuntimeError(
            f"Audyssey tools server unreachable at {url}: {exc}"
        ) from exc
    if not 200 <= response.status_code < 300:
        try:
            detail = response.json()["detail"]
        except (ValueError, KeyError, TypeError):
            detail = response.text[:200]
        raise RuntimeError(
            f"Audyssey {endpoint} failed ({response.status_code}): {detail}"
        )
    out_path.write_bytes(response.content)
    return out_path


def separate_track(mono_path: Path, plan: dict, attempt: int, out_path: Path) -> Path:
    with mono_path.open("rb") as mono:
        return _post(
            "separate",
            {"mono": (mono_path.name, mono, "audio/wav")},
            {"plan": json.dumps(plan), "attempt": str(attempt)},
            out_path,
        )


def generate_track(video_path: Path, plan: dict, seed: int, out_path: Path) -> Path:
    with video_path.open("rb") as video:
        return _post(
            "generate",
            {"video": (video_path.name, video, "video/mp4")},
            {"plan": json.dumps(plan), "seed": str(seed)},
            out_path,
        )
