"""1/3-octave spectrum curves for the "Compare spectrum" view.

All curves are mid-channel ((L+R)/2) band power in dB, expressed per OCTAVE
(so they line up with the presets' octave-band numbers). Only the shape is
compared: curves are aligned on their average level between 100 Hz and 10 kHz.
"""

from __future__ import annotations

import numpy as np
from scipy.signal import welch


# ISO 1/3-octave centres 20 Hz - 20 kHz
CENTERS = np.array([20, 25, 31.5, 40, 50, 63, 80, 100, 125, 160, 200, 250, 315, 400, 500, 630, 800,
                    1000, 1250, 1600, 2000, 2500, 3150, 4000, 5000, 6300, 8000, 10000, 12500, 16000, 20000],
                   dtype=float)

REGIONS = [  # (lo, hi, plain name)
    (20, 60, "sub"), (60, 120, "bass / kick"), (120, 250, "upper bass"), (250, 500, "low mids (mud)"),
    (500, 1000, "mids"), (1000, 2000, "upper mids"), (2000, 4000, "presence"), (4000, 6000, "bite"),
    (6000, 10000, "brilliance (hats, sizzle)"), (10000, 20000, "air"),
]


def curve_of(x: np.ndarray, sr: int) -> np.ndarray:
    """Band power per 1/3 octave, +4.77 dB so it reads like octave-band power."""
    mid = x.mean(axis=1) if x.ndim == 2 else x
    f, p = welch(mid, fs=sr, nperseg=8192)
    out = np.empty(len(CENTERS))
    for k, c in enumerate(CENTERS):
        lo, hi = c / 2 ** (1 / 6), min(c * 2 ** (1 / 6), sr / 2)
        m = (f >= lo) & (f < hi)
        out[k] = np.sum(p[m]) * (f[1] - f[0]) if m.any() else 1e-20
    return 10 * np.log10(out + 1e-20) + 10 * np.log10(3)


def curve_of_preset(preset: dict, sr: int = 48000) -> np.ndarray:
    """Measure the synthetic reference itself - exactly what Matchering aims at."""
    import tempfile
    from pathlib import Path
    from . import audio_io
    from .presets import make_reference
    with tempfile.TemporaryDirectory() as d:
        ref = make_reference(preset, Path(d) / "ref.wav", sr, seconds=12)
        x, _ = audio_io.load(ref)
    return curve_of(x, sr)


def _align_level(c: np.ndarray) -> float:
    m = (CENTERS >= 100) & (CENTERS <= 10000)
    return float(np.mean(c[m]))


def align(mix: np.ndarray, target: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Shift both so their 100 Hz - 10 kHz average is 0 dB."""
    return mix - _align_level(mix), target - _align_level(target)


def describe(mix: np.ndarray, target: np.ndarray, top: int = 4, min_db: float = 3.0) -> list[str]:
    """Plain-language list of the biggest differences, largest first."""
    diff = mix - target
    rows = []
    for lo, hi, name in REGIONS:
        m = (CENTERS >= lo) & (CENTERS < hi * 1.01)
        if not m.any():
            continue
        d = float(np.mean(diff[m]))
        if abs(d) >= min_db:
            rows.append((abs(d), f"{name} ({lo:g}-{hi:g} Hz): {abs(d):.0f} dB "
                                 f"{'more' if d > 0 else 'less'} than the target"))
    rows.sort(reverse=True)
    return [r[1] for r in rows[:top]] or ["Within 3 dB of the target everywhere - close match."]
