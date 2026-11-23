from __future__ import annotations

from pathlib import Path

import numpy as np
from scipy.io import wavfile


def _read_mono_float(path: Path) -> np.ndarray:
    _rate, data = wavfile.read(path)
    samples = data.astype(np.float32)
    if np.issubdtype(data.dtype, np.integer):
        info = np.iinfo(data.dtype)
        samples = samples / max(abs(info.min), info.max)
    return samples.mean(axis=1) if samples.ndim > 1 else samples


def compute_residual(
    original_path: Path, stem_paths: list[Path], out_path: Path
) -> Path:
    """Write ``original - sum(stems)`` as 48 kHz mono float32 WAV.

    Each stem is subtracted over the overlapping length only; the output keeps
    the original's length.
    """
    residual = _read_mono_float(original_path)
    for stem_path in stem_paths:
        stem = _read_mono_float(stem_path)
        n = min(len(residual), len(stem))
        residual[:n] -= stem[:n]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(out_path, 48000, residual.astype(np.float32))
    return out_path
