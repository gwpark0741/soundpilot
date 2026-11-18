from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx

from v2a_inspect.audio_generation.audyssey import (
    generate_track,
    separate_track,
    source_plan,
    track_description,
)
from v2a_inspect.models import SoundEvent, SoundTrack


def _track(**kwargs) -> SoundTrack:
    return SoundTrack(track_type=kwargs.pop("track_type", "sfx"), label="Dog", **kwargs)


def _event(track: SoundTrack, start: int, end: int, description: str = "e") -> SoundEvent:
    return SoundEvent(
        sound_track_id=track.sound_track_id,
        start_frame_index=start,
        end_frame_index=end,
        description=description,
    )


def _response(status: int, *, content: bytes = b"", json_body=None) -> MagicMock:
    response = MagicMock()
    response.status_code = status
    response.content = content
    response.text = content.decode(errors="replace")
    if json_body is None:
        response.json.side_effect = ValueError("no json")
    else:
        response.json.return_value = json_body
    return response


def _settings(token="t", default="label") -> MagicMock:
    settings = MagicMock()
    settings.audyssey_tools_url = "http://tools:8700"
    settings.audyssey_tool_token = None if token is None else MagicMock(
        get_secret_value=lambda: token
    )
    settings.track_description_default = default
    return settings


@patch("v2a_inspect.audio_generation.audyssey.get_settings")
class AudysseyClientTest(unittest.TestCase):
    def test_source_plan_maps_speech_and_spans(self, _settings_mock) -> None:
        track = _track(track_type="speech")
        plan = source_plan(
            track,
            [_event(track, 30, 60), _event(track, 0, 15)],
            fps=30,
            description="d",
        )
        self.assertEqual(plan["name"], "Dog")
        self.assertEqual(plan["kind"], "dialogue")
        self.assertEqual(plan["description"], "d")
        self.assertEqual(
            plan["spans"],
            [{"start": 0.0, "end": 0.5}, {"start": 1.0, "end": 2.0}],
        )

    def test_source_plan_without_events(self, _settings_mock) -> None:
        plan = source_plan(_track(), [], fps=30, description="d")
        self.assertEqual(plan["kind"], "sfx")
        self.assertEqual(plan["spans"], [])

    def test_track_description_prefers_override_then_label(self, get_settings) -> None:
        get_settings.return_value = _settings(default="label")
        track = _track()
        events = [_event(track, 30, 60, "late"), _event(track, 0, 15, "early")]
        self.assertEqual(track_description(track, events), "Dog")
        override = _track(description="custom")
        self.assertEqual(track_description(override, events), "custom")
        get_settings.return_value = _settings(default="first_event")
        self.assertEqual(track_description(track, events), "early")
        self.assertEqual(track_description(track, []), "Dog")

    @patch("v2a_inspect.audio_generation.audyssey.httpx.post")
    def test_generate_sends_seed_and_writes_wav(self, post, get_settings) -> None:
        get_settings.return_value = _settings()
        post.return_value = _response(200, content=b"RIFFwav")
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "v.mp4"
            video.write_bytes(b"video")
            out = Path(tmp) / "out.wav"
            plan = {"name": "Dog", "kind": "sfx", "description": "d", "spans": []}
            result = generate_track(video, plan, 7, out)
            self.assertEqual(result, out)
            self.assertEqual(out.read_bytes(), b"RIFFwav")
        args, kwargs = post.call_args
        self.assertEqual(args[0], "http://tools:8700/generate")
        self.assertEqual(kwargs["data"]["seed"], "7")
        self.assertEqual(json.loads(kwargs["data"]["plan"]), plan)
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer t")
        self.assertEqual(kwargs["timeout"], 600)
        self.assertIn("video", kwargs["files"])

    @patch("v2a_inspect.audio_generation.audyssey.httpx.post")
    def test_separate_omits_header_without_token(self, post, get_settings) -> None:
        get_settings.return_value = _settings(token=None)
        post.return_value = _response(200, content=b"wav")
        with tempfile.TemporaryDirectory() as tmp:
            mono = Path(tmp) / "m.wav"
            mono.write_bytes(b"mono")
            separate_track(mono, {"name": "Dog"}, 2, Path(tmp) / "o.wav")
        _, kwargs = post.call_args
        self.assertNotIn("Authorization", kwargs["headers"])
        self.assertEqual(kwargs["data"]["attempt"], "2")
        self.assertIn("mono", kwargs["files"])

    @patch("v2a_inspect.audio_generation.audyssey.httpx.post")
    def test_separate_error_message(self, post, get_settings) -> None:
        get_settings.return_value = _settings()
        post.return_value = _response(401, json_body={"detail": "unauthorized"})
        with tempfile.TemporaryDirectory() as tmp:
            mono = Path(tmp) / "m.wav"
            mono.write_bytes(b"mono")
            with self.assertRaises(RuntimeError) as ctx:
                separate_track(mono, {}, 0, Path(tmp) / "o.wav")
        self.assertIn("401", str(ctx.exception))
        self.assertIn("unauthorized", str(ctx.exception))

    @patch("v2a_inspect.audio_generation.audyssey.httpx.post")
    def test_unreachable(self, post, get_settings) -> None:
        get_settings.return_value = _settings()
        post.side_effect = httpx.ConnectError("x")
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "v.mp4"
            video.write_bytes(b"v")
            with self.assertRaises(RuntimeError) as ctx:
                generate_track(video, {}, 1, Path(tmp) / "o.wav")
        self.assertIn("unreachable", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
