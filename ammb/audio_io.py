"""Reading / writing audio. Everything inside AMMB is float32 stereo [frames, 2]."""

from __future__ import annotations

from math import gcd
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

AUDIO_EXTS = {".wav", ".flac", ".aif", ".aiff", ".ogg"}


def to_stereo(data: np.ndarray) -> np.ndarray:
    if data.ndim == 1:
        data = data[:, None]
    ch = data.shape[1]
    if ch == 1:
        return np.repeat(data, 2, axis=1)
    if ch == 2:
        return data
    # >2 channels: fold odd channels left, even channels right
    left = data[:, 0::2].mean(axis=1)
    right = data[:, 1::2].mean(axis=1)
    return np.stack([left, right], axis=1)


def resample(data: np.ndarray, sr_from: int, sr_to: int) -> np.ndarray:
    if sr_from == sr_to:
        return data
    g = gcd(sr_from, sr_to)
    return resample_poly(data, sr_to // g, sr_from // g, axis=0).astype(np.float32)


def load(path: str | Path, sr: int | None = None) -> tuple[np.ndarray, int]:
    """Load any soundfile-readable file as float32 stereo, optionally resampled to `sr`."""
    data, file_sr = sf.read(str(path), dtype="float32", always_2d=True)
    data = to_stereo(data)
    if sr is not None and sr != file_sr:
        data = resample(data, file_sr, sr)
        file_sr = sr
    return np.ascontiguousarray(data, dtype=np.float32), file_sr


def write(path: str | Path, data: np.ndarray, sr: int, subtype: str = "FLOAT") -> None:
    sf.write(str(path), data, sr, subtype=subtype)


def pad_to(data: np.ndarray, n: int) -> np.ndarray:
    if data.shape[0] >= n:
        return data[:n]
    out = np.zeros((n, data.shape[1]), dtype=data.dtype)
    out[: data.shape[0]] = data
    return out


def list_audio(folder: str | Path) -> list[Path]:
    folder = Path(folder)
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in AUDIO_EXTS and p.is_file())
