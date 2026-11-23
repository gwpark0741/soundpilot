import asyncio
import subprocess
import tempfile
import unittest
from pathlib import Path

from scipy.io import wavfile

from v2a_inspect.preprocessing.video import extract_original_audio, prepare_video
from v2a_inspect.ui.routes import _resolve_import_video


def _make_video(path: Path, audio: str | None) -> None:
    command = [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc=d=2:s=320x180:r=30",
    ]
    if audio is not None:
        command += ["-f", "lavfi", "-i", audio, "-shortest"]
    command += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(command, check=True)


class OriginalAudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.work = root / "work"
        cls.work.mkdir()
        cls.audible = root / "audible.mp4"
        cls.no_stream = root / "no_stream.mp4"
        cls.silent = root / "silent.mp4"
        _make_video(cls.audible, "sine=frequency=440:duration=2")
        _make_video(cls.no_stream, None)
        _make_video(cls.silent, "anullsrc=r=48000:cl=mono")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_audio_video_has_audio(self) -> None:
        out = extract_original_audio(self.audible, self.work / "a.wav")
        self.assertEqual(out, self.work / "a.wav")
        rate, data = wavfile.read(out)
        self.assertEqual(rate, 48000)
        self.assertEqual(data.ndim, 1)

    def test_no_stream_is_silent(self) -> None:
        self.assertIsNone(extract_original_audio(self.no_stream, self.work / "n.wav"))

    def test_silent_stream_is_silent(self) -> None:
        out_path = self.work / "s.wav"
        self.assertIsNone(extract_original_audio(self.silent, out_path))
        self.assertFalse(out_path.exists())

    def test_prepare_video_keeps_raw_and_audio(self) -> None:
        asset = prepare_video(self.audible, self.work)
        self.assertEqual(asset.raw_video_path, self.audible)
        self.assertEqual(asset.original_audio_path, self.work / "audible.original.wav")
        self.assertTrue(asset.has_audio)

    def test_prepare_video_silent_has_no_audio(self) -> None:
        asset = prepare_video(self.silent, self.work)
        self.assertEqual(asset.raw_video_path, self.silent)
        self.assertIsNone(asset.original_audio_path)
        self.assertFalse(asset.has_audio)

    def test_import_resolves_raw_and_audio_for_both_paths(self) -> None:
        # Prepared-format uploads must be audio-free, so they take the fast path.
        fast = Path(self._tmp.name) / "fast.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc=d=1:s=1280x720:r=30",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(fast),
            ],
            check=True,
        )
        asset = asyncio.run(_resolve_import_video(fast, self.work))
        self.assertEqual(asset.source_path, fast)
        self.assertEqual(asset.raw_video_path, fast)
        self.assertFalse(asset.has_audio)
        asset = asyncio.run(_resolve_import_video(self.audible, self.work))
        self.assertEqual(asset.raw_video_path, self.audible)
        self.assertTrue(asset.has_audio)


if __name__ == "__main__":
    unittest.main()
