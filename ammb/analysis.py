"""Spectral and loudness analysis.

The key idea: the energy of a SUM of stems in band k is not the sum of the
stems' energies (kick and bass overlap, phase matters). It is exactly

    E_k(g) = g^T C_k g

where C_k[i, j] = sum over STFT bins in band k of Re(X_i * conj(X_j)).
We compute every C_k once; the solver then evaluates any gain vector for free.
"""

from __future__ import annotations

import numpy as np
from scipy.signal import lfilter

# (name, low Hz, high Hz)
BANDS_5 = [
    ("sub", 20, 120),
    ("low-mid", 120, 400),
    ("mid", 400, 2500),
    ("presence", 2500, 6000),
    ("air", 6000, 20000),
]

# Finer split: more equations than the 5-band version, so the solver is
# less free to trade stems against each other arbitrarily.
BANDS_10 = [
    ("sub", 20, 60),
    ("bass", 60, 120),
    ("upper-bass", 120, 250),
    ("low-mid", 250, 500),
    ("mid", 500, 1000),
    ("upper-mid", 1000, 2000),
    ("presence", 2000, 4000),
    ("bite", 4000, 6000),
    ("brilliance", 6000, 10000),
    ("air", 10000, 20000),
]

BAND_SETS = {5: BANDS_5, 10: BANDS_10}

EPS = 1e-20


def _band_slices(bands, sr: int, nfft: int):
    freqs = np.fft.rfftfreq(nfft, 1.0 / sr)
    out = []
    for _, lo, hi in bands:
        hi = min(hi, sr / 2)
        idx = np.nonzero((freqs >= lo) & (freqs < hi))[0]
        out.append(slice(int(idx[0]), int(idx[-1]) + 1) if len(idx) else slice(0, 0))
    return out


def band_cross_power(stems: list[np.ndarray], sr: int, bands, nperseg: int = 4096,
                     block_frames: int = 128) -> np.ndarray:
    """Return C with shape [K bands, N stems, N stems] (mean power per STFT frame).

    Left and right channels are analysed separately and added, so a hard-panned
    pair is not mistaken for a phase cancellation the way a mono fold would.
    """
    n_stems = len(stems)
    length = stems[0].shape[0]
    hop = nperseg // 2
    window = np.hanning(nperseg).astype(np.float32)
    slices = _band_slices(bands, sr, nperseg)
    C = np.zeros((len(bands), n_stems, n_stems), dtype=np.float64)

    n_frames = 1 + max(0, -(-(length - nperseg) // hop))
    padded_len = (n_frames - 1) * hop + nperseg
    views = []
    for x in stems:
        if x.shape[0] < padded_len:
            x = np.concatenate([x, np.zeros((padded_len - x.shape[0], x.shape[1]), x.dtype)])
        # [frames, 2, nperseg] strided view, no copy
        views.append(np.lib.stride_tricks.sliding_window_view(x, nperseg, axis=0)[::hop])

    total_frames = 0
    for f0 in range(0, n_frames, block_frames):
        f1 = min(n_frames, f0 + block_frames)
        # spectra[i] -> [frames*2 channels, bins]
        spectra = []
        for v in views:
            frames = v[f0:f1] * window                  # [frames, 2, nperseg]
            X = np.fft.rfft(frames, axis=2)             # [frames, 2, bins]
            spectra.append(X.reshape(-1, X.shape[2]).astype(np.complex64))
        S = np.stack(spectra)                         # [N, 2*frames, bins]
        for k, sl in enumerate(slices):
            if sl.stop <= sl.start:
                continue
            Xb = S[:, :, sl].reshape(n_stems, -1)
            C[k] += (Xb @ Xb.conj().T).real
        total_frames += f1 - f0
    return C / max(total_frames, 1)


def band_power(x: np.ndarray, sr: int, bands, nperseg: int = 4096) -> np.ndarray:
    """Band energies of a single stereo signal, shape [K]."""
    return band_cross_power([x], sr, bands, nperseg)[:, 0, 0]


def rms_dbfs(x: np.ndarray) -> float:
    return 10 * np.log10(np.mean(np.square(x, dtype=np.float64)) + EPS)


def db_power(p) -> np.ndarray:
    return 10 * np.log10(np.asarray(p, dtype=np.float64) + EPS)


# --- ITU-R BS.1770-4 integrated loudness -----------------------------------

def _k_weighting(sr: int):
    # Stage 1: high shelf (head acoustics)
    G, Q, fc = 3.99984385397, 0.7071752369554193, 1681.9744509555319
    K = np.tan(np.pi * fc / sr)
    Vh = 10 ** (G / 20)
    Vb = Vh ** 0.499666774155
    a0 = 1 + K / Q + K * K
    b1 = [(Vh + Vb * K / Q + K * K) / a0, 2 * (K * K - Vh) / a0, (Vh - Vb * K / Q + K * K) / a0]
    a1 = [1.0, 2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0]
    # Stage 2: RLB high-pass
    Q, fc = 0.5003270373253953, 38.13547087613982
    K = np.tan(np.pi * fc / sr)
    a0 = 1 + K / Q + K * K
    b2 = [1.0, -2.0, 1.0]
    a2 = [1.0, 2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0]
    return (b1, a1), (b2, a2)


def lufs(x: np.ndarray, sr: int) -> float:
    """Integrated loudness (LUFS) of a stereo float signal. -inf-ish for silence."""
    (b1, a1), (b2, a2) = _k_weighting(sr)
    y = lfilter(b2, a2, lfilter(b1, a1, x.astype(np.float64), axis=0), axis=0)
    block = int(0.4 * sr)
    step = int(0.1 * sr)
    if y.shape[0] < block:
        return -120.0
    sq = np.square(y)
    csum = np.concatenate([np.zeros((1, sq.shape[1])), np.cumsum(sq, axis=0)])
    starts = np.arange(0, y.shape[0] - block + 1, step)
    z = (csum[starts + block] - csum[starts]) / block      # [blocks, ch]
    power = z.sum(axis=1)                                   # channel weights = 1 (L, R)
    loud = -0.691 + 10 * np.log10(power + EPS)
    gated = power[loud > -70]
    if gated.size == 0:
        return -120.0
    rel = -0.691 + 10 * np.log10(gated.mean()) - 10
    gated2 = power[(loud > -70) & (loud > rel)]
    if gated2.size == 0:
        return -120.0
    return float(-0.691 + 10 * np.log10(gated2.mean()))
