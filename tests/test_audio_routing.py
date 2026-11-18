from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from v2a_inspect.audio_generation.client import (
    generate_audio_for_item,
    generate_sfx_elevenlabs,
    generate_speech_elevenlabs,
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


def _tts_settings(provider: str = "kokoro") -> MagicMock:
    settings = MagicMock()
    settings.tts_provider = provider
    settings.elevenlabs_api_key.get_secret_value.return_value = "key"
    settings.elevenlabs_tts_voice_id = "voice-1"
    settings.elevenlabs_tts_model = "model-1"
    return settings


class AudioRoutingTest(unittest.TestCase):
    @patch("v2a_inspect.audio_generation.client.get_settings")
    @patch("v2a_inspect.audio_generation.client.generate_speech_kokoro")
    def test_speech_uses_spoken_text(self, generate_speech, get_settings) -> None:
        get_settings.return_value = _tts_settings("kokoro")
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

    @patch("v2a_inspect.audio_generation.client.get_settings")
    @patch("v2a_inspect.audio_generation.client.generate_speech_kokoro")
    def test_speech_falls_back_to_description(
        self, generate_speech, get_settings
    ) -> None:
        get_settings.return_value = _tts_settings("kokoro")
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

    @patch("v2a_inspect.audio_generation.client.get_settings")
    @patch("v2a_inspect.audio_generation.client.generate_speech_kokoro")
    @patch("v2a_inspect.audio_generation.client.generate_speech_elevenlabs")
    def test_speech_defaults_to_elevenlabs(
        self, eleven, kokoro, get_settings
    ) -> None:
        get_settings.return_value = _tts_settings("elevenlabs")
        eleven.return_value = "speech.wav"

        result = generate_audio_for_item(
            kind="speech",
            description="A calm voice",
            spoken_text="Hello there",
            out_path="speech.wav",
            duration=1.0,
            generation_model="tts",
        )

        self.assertEqual(result, "speech.wav")
        eleven.assert_called_once_with("Hello there", "speech.wav", 1.0)
        kokoro.assert_not_called()

    @patch("v2a_inspect.audio_generation.client.ElevenLabs")
    @patch("v2a_inspect.audio_generation.client.get_settings")
    def test_elevenlabs_tts_calls_sdk_and_writes_bytes(
        self, get_settings, elevenlabs
    ) -> None:
        get_settings.return_value = _tts_settings()
        elevenlabs.return_value.text_to_speech.convert.return_value = iter(
            [b"RIFF", b"data"]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir) / "x.wav"
            result = generate_speech_elevenlabs("Hi", str(out), 3.0)

            self.assertEqual(result, str(out))
            self.assertEqual(out.read_bytes(), b"RIFFdata")
        elevenlabs.assert_called_once_with(api_key="key")
        elevenlabs.return_value.text_to_speech.convert.assert_called_once_with(
            "voice-1", text="Hi", model_id="model-1", output_format="wav_48000"
        )

    @patch("v2a_inspect.audio_generation.client.get_settings")
    def test_missing_elevenlabs_key_raises_for_tts(self, get_settings) -> None:
        get_settings.return_value = _tts_settings()
        get_settings.return_value.elevenlabs_api_key = None
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir) / "x.wav"
            with self.assertRaises(RuntimeError) as ctx:
                generate_speech_elevenlabs("Hi", str(out))

            self.assertIn("V2A_INSPECT_TTS_PROVIDER=kokoro", str(ctx.exception))
            self.assertFalse(out.exists())
