"""Genre presets: a target instead of a reference track.

Matchering only "hears" two things in a reference: its average mid and side
spectra, and its RMS in the loudest parts. So a preset is turned into a
synthetic reference - stationary noise shaped to the preset's mid/side curves
at the preset's loudness - and Matchering is pointed at that.

Preset curves are OCTAVE-BAND POWER in dB (each octave's total energy),
relative to whatever you like - only the shape matters. side_db is the side
channel relative to mid at the same frequency (very negative = mono there).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from . import analysis, audio_io

PRESET_DIR = Path(__file__).parent / "presets"


def available() -> list[str]:
    return sorted(p.stem for p in PRESET_DIR.glob("*.json"))


def load(name_or_path: str) -> dict:
    p = Path(name_or_path)
    if not p.is_file():
        p = PRESET_DIR / f"{name_or_path}.json"
    if not p.is_file():
        raise FileNotFoundError(f"Unknown preset '{name_or_path}'. Available: {', '.join(available())}")
    preset = json.loads(p.read_text())
    preset.setdefault("id", p.stem)
    return preset


def _curve(points: dict, f: np.ndarray) -> np.ndarray:
    """Interpolate a {hz: dB} dict over f in log-frequency, flat beyond the ends."""
    hz = np.array(sorted(float(k) for k in points))
    db = np.array([points[k] for k in sorted(points, key=float)], dtype=float)
    return np.interp(np.log2(np.maximum(f, 1.0)), np.log2(hz), db)


def _shaped_noise(octave_db: dict, n: int, sr: int, rng) -> np.ndarray:
    f = np.fft.rfftfreq(n, 1.0 / sr)
    # octave-band power P -> power density per Hz ~ P / f  (an octave at f is ~0.7 f wide)
    dens = 10 ** (_curve(octave_db, f) / 10) / np.maximum(f, 1.0)
    dens *= 1.0 / (1 + (18.0 / np.maximum(f, 1e-3)) ** 8)        # steep rumble cut below ~18 Hz
    dens *= 1.0 / (1 + (f / 20500.0) ** 16)                       # and above 20.5 kHz
    spec = (rng.standard_normal(len(f)) + 1j * rng.standard_normal(len(f))) * np.sqrt(dens)
    spec[0] = 0
    return np.fft.irfft(spec, n)


def make_reference(preset: dict, path: str | Path, sr: int, seconds: float = 30.0, seed: int = 1) -> Path:
    rng = np.random.default_rng(seed)
    n = int(seconds * sr)
    mid_db = preset["mid_db"]
    side_rel = preset.get("side_db", {"1000": -60})
    hz = sorted(set(map(float, mid_db)) | set(map(float, side_rel)))
    fgrid = np.array(hz)
    side_db = {str(h): float(m + s) for h, m, s in
               zip(hz, _curve(mid_db, fgrid), _curve(side_rel, fgrid))}
    mid = _shaped_noise(mid_db, n, sr, rng)
    side = _shaped_noise(side_db, n, sr, rng)
    # match the side's absolute level to (mid + offset): both noises came out with
    # the same scale per unit curve, so they're already consistent.
    x = np.stack([mid + side, mid - side], axis=1)
    x *= 0.1 / np.sqrt(np.mean(x ** 2))          # bring to ~-20 dBFS RMS first so the LUFS gate sees it
    target = float(preset.get("master", {}).get("lufs", -9.0))
    x *= 10 ** ((target - analysis.lufs(x, sr)) / 20)
    audio_io.write(path, x.astype(np.float32), sr, subtype="FLOAT")
    return Path(path)


def prepare(name_or_path: str, out_dir: str | Path, sr: int, opts) -> Path:
    """Load a preset, write its synthetic reference into out_dir, apply its rules to opts."""
    preset = load(name_or_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Preset: {preset['name']}  (target {preset.get('master', {}).get('lufs', -9)} LUFS)")
    opts.mono_below_hz = preset.get("rules", {}).get("mono_below_hz")
    return make_reference(preset, out_dir / f"preset_reference_{preset['id']}.wav", sr)


def mono_below(x: np.ndarray, sr: int, hz: float) -> np.ndarray:
    """Remove side-channel content below `hz` (zero-phase), i.e. a mono low end."""
    from scipy.signal import butter, sosfiltfilt
    mid = (x[:, 0] + x[:, 1]) / 2
    side = (x[:, 0] - x[:, 1]) / 2
    side = sosfiltfilt(butter(4, hz, "high", fs=sr, output="sos"), side)
    return np.stack([mid + side, mid - side], axis=1).astype(np.float32)
