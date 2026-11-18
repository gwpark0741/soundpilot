from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from v2a_inspect.audio_generation.client import (
    generate_audio_for_item,
    generate_sfx_elevenlabs,
    generate_speech_kokoro,
    generate_v2a_hunyuan,
)


def _failing_async_client(method: str, message: str) -> MagicMock:
    inner = MagicMock()
    getattr(inner, method).side_effect = RuntimeError(message)
    client_cls = MagicMock()
    client_cls.return_value.__aenter__ = AsyncMock(return_value=inner)
    client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
    return client_cls


class AudioRoutingTest(unittest.TestCase):
    @patch("v2a_inspect.audio_generation.client.generate_speech_kokoro")
    def test_speech_uses_spoken_text(self, generate_speech) -> None:
        generate_speech.return_value = "speech.wav"

        result = generate_audio_for_item(
            kind="speech",
            description="A calm close-mic voice",
            spoken_text="Hello there",
            out_path="speech.wav",
            duration=1.0,
            generation_model="tts",
        )

        self.assertEqual(result, "speech.wav")
        generate_speech.assert_called_once_with("Hello there", "speech.wav", 1.0, None)

    @patch("v2a_inspect.audio_generation.client.generate_speech_kokoro")
    def test_speech_falls_back_to_description(self, generate_speech) -> None:
        generate_speech.return_value = "speech.wav"

        result = generate_audio_for_item(
            kind="speech",
            description="A calm close-mic voice",
            out_path="speech.wav",
            duration=1.0,
            generation_model="t2a",
        )

        self.assertEqual(result, "speech.wav")
        generate_speech.assert_called_once_with(
            "A calm close-mic voice", "speech.wav", 1.0, None
        )

    @patch("v2a_inspect.audio_generation.client.generate_sfx_elevenlabs")
    def test_non_synced_sfx_uses_t2a(self, generate_sfx) -> None:
        generate_sfx.return_value = "sfx.wav"

        result = generate_audio_for_item(
            kind="sfx",
            description="A distant dog bark",
            out_path="sfx.wav",
            duration=1.0,
            generation_model="t2a",
        )

        self.assertEqual(result, "sfx.wav")
        generate_sfx.assert_called_once()

    @patch("v2a_inspect.audio_generation.client.generate_sfx_elevenlabs")
    def test_v2a_does_not_fall_back_to_t2a(self, generate_sfx) -> None:
        with self.assertRaises(ValueError):
            generate_audio_for_item(
                kind="sfx",
                description="A visible impact",
                out_path="impact.wav",
                duration=0.2,
                generation_model="v2a",
            )

        generate_sfx.assert_not_called()

    @patch("v2a_inspect.audio_generation.client.generate_speech_kokoro")
    def test_tts_rejects_non_speech(self, generate_speech) -> None:
        with self.assertRaises(ValueError):
            generate_audio_for_item(
                kind="sfx",
                description="A visible impact",
                out_path="impact.wav",
                duration=0.2,
                generation_model="tts",
            )

        generate_speech.assert_not_called()

    @patch(
        "v2a_inspect.audio_generation.client.generate_sfx_elevenlabs",
        side_effect=RuntimeError("quota"),
    )
    def test_generation_error_propagates(self, _generate_sfx) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            generate_audio_for_item(
                kind="sfx",
                description="A distant dog bark",
                out_path="sfx.wav",
                duration=1.0,
                generation_model="t2a",
            )

        self.assertIn("quota", str(ctx.exception))

    @patch("v2a_inspect.audio_generation.client.get_settings")
    def test_missing_elevenlabs_key_raises(self, get_settings) -> None:
        get_settings.return_value.elevenlabs_api_key = None
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir) / "x.wav"
            with self.assertRaises(RuntimeError) as ctx:
                generate_sfx_elevenlabs("x", str(out), 1.0)

            self.assertIn("ELEVENLABS_API_KEY", str(ctx.exception))
            self.assertFalse(out.exists())

    def test_kokoro_api_failure_propagates(self) -> None:
        client_cls = _failing_async_client("generate_speech", "kokoro 500")
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir) / "x.wav"
            with patch("v2a_inspect.audio_generation.client.KokoroClient", client_cls):
                with self.assertRaises(RuntimeError) as ctx:
                    generate_speech_kokoro("hi", str(out), 1.0)

            self.assertIn("kokoro 500", str(ctx.exception))
            self.assertFalse(out.exists())

    @patch("v2a_inspect.audio_generation.client.ElevenLabs")
    @patch("v2a_inspect.audio_generation.client.get_settings")
    def test_elevenlabs_api_failure_propagates(self, _settings, elevenlabs) -> None:
        elevenlabs.return_value.text_to_sound_effects.convert.side_effect = (
            RuntimeError("quota exceeded")
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir) / "x.wav"
            with self.assertRaises(RuntimeError) as ctx:
                generate_sfx_elevenlabs("x", str(out), 1.0)

            self.assertIn("quota exceeded", str(ctx.exception))
            self.assertFalse(out.exists())

    def test_hunyuan_api_failure_propagates(self) -> None:
        client_cls = _failing_async_client("generate_v2a", "hunyuan 500")
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir) / "x.wav"
            with patch("v2a_inspect.audio_generation.client.HunyuanClient", client_cls):
                with self.assertRaises(RuntimeError) as ctx:
                    generate_v2a_hunyuan("vid", 30.0, (0.0, 1.0), "x", str(out), 1.0)

            self.assertIn("hunyuan 500", str(ctx.exception))
            self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
