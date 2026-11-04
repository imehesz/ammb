"""Gain solver + mixdown.

Objective (all in dB, so the quiet bands count as much as the sub):

    min_x  sum_k w_k * (dB(g^T C_k g) - dB(b_k) - c)^2  +  lam * sum_i (x_i - mean(x))^2

    x_i  = gain of stem i in dB, g_i = 10^(x_i/20)
    c    = free overall level offset (raw stems vs. a mastered reference;
           only the SHAPE of the spectrum is matched, not the level)
    lam  = how strongly to stay near your rough mix (0 = pure spectral match)
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import least_squares
from scipy.signal import resample_poly

from .analysis import EPS, db_power


@dataclass
class SolveResult:
    gains_db: np.ndarray                 # per stem (inactive stems -> 0)
    active: np.ndarray                   # bool mask
    band_err_before: np.ndarray          # dB per band, level-offset removed
    band_err_after: np.ndarray
    info: dict = field(default_factory=dict)


def _band_error(x_db: np.ndarray, C: np.ndarray, b_db: np.ndarray, w: np.ndarray) -> np.ndarray:
    g = 10 ** (x_db / 20)
    m = np.einsum("i,kij,j->k", g, C, g)
    r = db_power(np.maximum(m, EPS)) - b_db
    c = np.sum(w * r) / np.sum(w)                       # best level offset in closed form
    return r - c


def solve_gains(C: np.ndarray, b: np.ndarray, active: np.ndarray, lam: float = 0.05,
                bounds_db: tuple[float, float] = (-20.0, 9.5),
                band_weights: np.ndarray | None = None,
                band_centers_hz: np.ndarray | None = None, ignore_tilt: bool = False) -> SolveResult:
    K, N, _ = C.shape
    w = np.ones(K) if band_weights is None else np.asarray(band_weights, dtype=float)
    # bands the reference has (almost) nothing in, e.g. air on a lo-fi ref, are ignored
    b_db = db_power(b)
    w = np.where(b_db < b_db.max() - 80, 0.0, w)
    sw = np.sqrt(w)

    idx = np.nonzero(active)[0]
    Ca = C[:, idx][:, :, idx]

    # ignore_tilt: project out a straight line (dB vs log-frequency) from the error.
    # Broad "darker/brighter than the ref" is left to Matchering's EQ; the faders
    # only chase the bumps that come from one stem being too loud or too quiet.
    P = np.eye(K)
    if ignore_tilt and band_centers_hz is not None:
        lc = np.log2(np.asarray(band_centers_hz, dtype=float))
        V = np.vander(lc - lc.mean(), 2) * sw[:, None]
        P = np.eye(K) - V @ np.linalg.pinv(V)

    def residuals(x):
        r = P @ (sw * _band_error(x, Ca, b_db, w))
        reg = np.sqrt(lam) * (x - x.mean())
        return np.concatenate([r, reg])

    x0 = np.zeros(len(idx))
    if len(idx) == 0:
        err = np.zeros(K)
        return SolveResult(np.zeros(N), active, err, err, {"note": "no active stems"})

    res = least_squares(residuals, x0, bounds=bounds_db, method="trf", x_scale=1.0)
    x = res.x - np.median(res.x)                        # centre: overall level is irrelevant
    x = np.clip(x, *bounds_db)

    gains = np.zeros(N)
    gains[idx] = x
    before = _band_error(x0, Ca, b_db, w)
    after = _band_error(x, Ca, b_db, w)
    return SolveResult(gains, active, before, after,
                       {"cost": float(res.cost), "nfev": int(res.nfev), "status": int(res.status),
                        "band_weights": w.tolist()})


def unity(C: np.ndarray, b: np.ndarray, active: np.ndarray) -> SolveResult:
    """No balancing: every stem stays at 0 dB; still report how far off the target it is."""
    K, N, _ = C.shape
    b_db = db_power(b)
    w = np.where(b_db < b_db.max() - 80, 0.0, np.ones(K))
    idx = np.nonzero(active)[0]
    err = _band_error(np.zeros(len(idx)), C[:, idx][:, :, idx], b_db, w) if len(idx) else np.zeros(K)
    return SolveResult(np.zeros(N), active, err, err, {"note": "balancing off (unity gains)"})


def mixdown(stems: list[np.ndarray], gains_db: np.ndarray) -> np.ndarray:
    out = np.zeros_like(stems[0], dtype=np.float32)
    for x, gdb in zip(stems, gains_db):
        out += x * np.float32(10 ** (gdb / 20))
    return out


def true_peak(x: np.ndarray, oversample: int = 4, chunk: int = 1 << 18) -> float:
    """Approximate true peak (linear) via 4x polyphase oversampling, chunked for memory."""
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    pad = 64
    for s in range(0, x.shape[0], chunk):
        seg = x[max(0, s - pad): s + chunk + pad]
        up = resample_poly(seg, oversample, 1, axis=0)
        peak = max(peak, float(np.max(np.abs(up))))
    return peak


def normalize_true_peak(x: np.ndarray, target_dbtp: float = -6.0) -> tuple[np.ndarray, float]:
    tp = true_peak(x)
    if tp <= 0:
        return x, 0.0
    gain = 10 ** (target_dbtp / 20) / tp
    return (x * np.float32(gain)).astype(np.float32), 20 * np.log10(gain)
