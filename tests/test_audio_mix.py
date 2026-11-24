from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import av
import numpy as np
from scipy.io import wavfile

from v2a_inspect.audio_generation import mix as mix_module
from v2a_inspect.models.audio_plan import AudioPlan, AudioPlanItem


class _FakeClip:
    duration = 1.0
    fps = 24.0
    audio = None

    def subclipped(self, *_args):
        return self

    def with_duration(self, *_args):
        return self

    def with_start(self, *_args):
        return self

    def with_effects(self, *_args):
        return self

    def transform(self, *_args, **_kwargs):
        return self

    def with_audio(self, audio):
        self.audio = audio
        return self

    def write_videofile(self, filename, **kwargs):
        self.write_args = (filename, kwargs)

    def close(self):
        return None


class AudioMixTest(unittest.TestCase):
    def test_moviepy_temp_audio_uses_writable_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            wav_path = root / "event.wav"
            wav_path.touch()
            output_path = root / "output" / "preview.mp4"
            video = _FakeClip()
            plan = AudioPlan(
                items=[
                    AudioPlanItem(
                        item_id="event",
                        type="sfx",
                        time=(0.0, 1.0),
                        description="impact",
                    )
                ],
                total_duration=1.0,
            )

            with (
                patch.object(mix_module, "VideoFileClip", return_value=video),
                patch.object(mix_module, "AudioFileClip", return_value=_FakeClip()),
                patch.object(mix_module, "CompositeAudioClip", return_value=_FakeClip()),
            ):
                result = mix_module.mix_audio_into_video(
                    "input.mp4",
                    plan,
                    {"event": str(wav_path)},
                    str(output_path),
                )

            self.assertEqual(result, str(output_path))
            self.assertEqual(
                video.write_args[1]["temp_audiofile_path"],
                str(output_path.parent),
            )

    def _stem_active_seconds(self, model: str) -> float:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            t = np.arange(44100) / 44100
            clip = (np.sin(2 * np.pi * 440 * t) * 0.5 * 32767).astype(np.int16)
            wavfile.write(root / "clip.wav", 44100, clip)  # 1.0 s
            item = AudioPlanItem(
                item_id="e",
                type="sfx",
                time=(0.0, 0.1),  # 0.1 s event window
                description="x",
                volume=1.0,
                generation_model=model,
            )
            out = mix_module.mix_audio_items_to_wav(
                [item], {"e": str(root / "clip.wav")}, str(root / "stem.wav"), total_duration=2.0
            )
            rate, data = wavfile.read(out)
            return float((np.abs(data.astype(float)).max(axis=-1) > 100).sum() / rate)

    def test_tts_and_t2a_clips_keep_full_length_others_are_trimmed(self) -> None:
        self.assertGreater(self._stem_active_seconds("t2a"), 0.9)
        self.assertGreater(self._stem_active_seconds("tts"), 0.9)
        self.assertLess(self._stem_active_seconds("selsync"), 0.2)


def _video_packets(path: str) -> list[bytes]:
    with av.open(path) as container:
        stream = container.streams.video[0]
        return [bytes(p) for p in container.demux(stream) if p.size]


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is not installed")
class MuxCopyTest(unittest.TestCase):
    def test_preview_copies_the_source_video_stream_and_adds_aac(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "in.mp4"
            made = subprocess.run(
                [
                    "ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                    "-i", "testsrc=size=64x64:rate=30:duration=2",
                    "-c:v", "libx264", "-g", "30", "-pix_fmt", "yuv420p", str(source),
                ],  # fmt: skip
                capture_output=True,
            )
            if made.returncode != 0:
                self.skipTest("ffmpeg has no libx264")
            wavfile.write(root / "a.wav", 44100, np.zeros(22050, dtype=np.int16))  # 0.5 s, shorter than the video
            plan = AudioPlan(
                items=[AudioPlanItem(item_id="a", type="sfx", time=(0.0, 0.5), description="x")],
                total_duration=2.0,
            )

            out = mix_module.mix_audio_into_video(
                str(source), plan, {"a": str(root / "a.wav")}, str(root / "out" / "preview.mp4")
            )

            self.assertEqual(out, str(root / "out" / "preview.mp4"))
            # Same compressed packets: the video was copied, not re-encoded.
            self.assertEqual(_video_packets(out), _video_packets(str(source)))
            with av.open(out) as container:
                self.assertEqual(container.streams.audio[0].codec_context.name, "aac")
                self.assertAlmostEqual(container.duration / 1e6, 2.0, delta=0.1)  # audio padded, video length kept
            # faststart: the moov atom precedes the media data.
            head = Path(out).read_bytes()
            self.assertLess(head.index(b"moov"), head.index(b"mdat"))
            self.assertEqual([p.name for p in Path(out).parent.iterdir()], ["preview.mp4"])


if __name__ == "__main__":
    unittest.main()
