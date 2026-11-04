"""The full DSP pipeline on files. Shared by the offline CLI and the Audacity bridge."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import analysis, audio_io, balancer, presets

SILENT_DBFS = -60.0


@dataclass
class Options:
    lam: float = 0.05             # pull toward the rough mix (0 = pure spectral match)
    bands: int = 10               # 5 or 10
    bounds_db: tuple = (-20.0, 9.5)
    headroom_dbtp: float = -6.0
    master: bool = True
    balance: bool = True          # False = keep your levels, master only
    mono_below_hz: float | None = None   # preset rule: mono low end below this
    ignore_tilt: bool = False     # leave broad tonal tilt to Matchering, balance only the bumps
    compare: bool = False         # also master the untouched (unity-gain) mix for A/B
    quiet: bool = False


def _log(opts: Options, msg: str) -> None:
    if not opts.quiet:
        print(msg, flush=True)


def run(stem_paths: list[Path], ref_path: Path, out_dir: Path, opts: Options | None = None,
        stem_names: list[str] | None = None) -> dict:
    opts = opts or Options()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    names = stem_names or [Path(p).stem for p in stem_paths]
    t0 = time.time()

    # --- load ---------------------------------------------------------------
    _log(opts, f"Loading {len(stem_paths)} stems + reference ...")
    stems, sr = [], None
    for p in stem_paths:
        x, sr_i = audio_io.load(p, sr)
        sr = sr or sr_i
        stems.append(x)
    length = max(x.shape[0] for x in stems)
    stems = [audio_io.pad_to(x, length) for x in stems]
    ref, _ = audio_io.load(ref_path, sr)

    # --- analyse ------------------------------------------------------------
    bands = analysis.BAND_SETS[opts.bands]
    rms = np.array([analysis.rms_dbfs(x) for x in stems])
    active = rms > SILENT_DBFS
    for n, r, a in zip(names, rms, active):
        if not a:
            _log(opts, f"  - '{n}' is near-silent ({r:.1f} dBFS RMS) -> left at 0 dB, not solved")

    _log(opts, f"Analysing {opts.bands} bands (cross-spectral) ...")
    C = analysis.band_cross_power(stems, sr, bands)
    b = analysis.band_power(ref, sr, bands)

    # --- solve --------------------------------------------------------------
    centers = np.array([np.sqrt(lo * min(hi, sr / 2)) for _, lo, hi in bands])
    if opts.balance:
        _log(opts, f"Solving gains (lambda={opts.lam}, bounds {opts.bounds_db[0]}..{opts.bounds_db[1]} dB) ...")
        sol = balancer.solve_gains(C, b, active, lam=opts.lam, bounds_db=opts.bounds_db,
                                   band_centers_hz=centers, ignore_tilt=opts.ignore_tilt)
    else:
        _log(opts, "Balancing off: stems stay at your levels.")
        sol = balancer.unity(C, b, active)

    # --- mix + headroom -----------------------------------------------------
    mix = balancer.mixdown(stems, sol.gains_db)
    if opts.mono_below_hz:
        _log(opts, f"Mono low end below {opts.mono_below_hz:g} Hz ...")
        mix = presets.mono_below(mix, sr, opts.mono_below_hz)
    mix, norm_db = balancer.normalize_true_peak(mix, opts.headroom_dbtp)
    premaster = out_dir / "premaster.wav"
    audio_io.write(premaster, mix, sr)

    mix_lufs = analysis.lufs(mix, sr)
    lin = np.float32(10 ** (norm_db / 20))
    stem_rows = []
    for i, n in enumerate(names):
        g = np.float32(10 ** (sol.gains_db[i] / 20)) * lin
        sl = analysis.lufs(stems[i] * g, sr) if active[i] else None
        stem_rows.append({
            "name": n,
            "gain_db": round(float(sol.gains_db[i]), 2),
            "solved": bool(active[i]),
            "rms_dbfs_raw": round(float(rms[i]), 1),
            "lufs_vs_mix": None if sl is None or sl < -100 else round(sl - mix_lufs, 1),
        })

    report = {
        "sample_rate": sr,
        "seconds_of_audio": round(length / sr, 1),
        "options": {k: getattr(opts, k) for k in ("lam", "bands", "bounds_db", "headroom_dbtp", "ignore_tilt", "balance", "mono_below_hz")},
        "stems": stem_rows,
        "bands": [
            {"name": nm, "hz": [lo, hi],
             "err_before_db": round(float(eb), 2), "err_after_db": round(float(ea), 2)}
            for (nm, lo, hi), eb, ea in zip(bands, sol.band_err_before, sol.band_err_after)
        ],
        "band_rms_err_before_db": round(float(np.sqrt(np.mean(sol.band_err_before ** 2))), 2),
        "band_rms_err_after_db": round(float(np.sqrt(np.mean(sol.band_err_after ** 2))), 2),
        "premaster": str(premaster),
        "premaster_norm_db": round(float(norm_db), 2),
        "premaster_lufs": round(mix_lufs, 1),
        "solver": sol.info,
    }

    # --- optional A/B: same pipeline with every stem at 0 dB -----------------
    unity = None
    if opts.compare:
        umix = balancer.mixdown(stems, np.zeros(len(stems)))
        if opts.mono_below_hz:
            umix = presets.mono_below(umix, sr, opts.mono_below_hz)
        umix, _ = balancer.normalize_true_peak(umix, opts.headroom_dbtp)
        unity = out_dir / "premaster_unity.wav"
        audio_io.write(unity, umix, sr)

    # --- master -------------------------------------------------------------
    if opts.master:
        from .master import run_matchering
        _log(opts, "Mastering with Matchering ...")
        report["matchering"] = run_matchering(premaster, ref_path, out_dir / "final_master.wav",
                                              quiet=opts.quiet)
        if unity is not None:
            _log(opts, "Mastering the unity-gain mix for A/B ...")
            report["matchering_unity"] = run_matchering(
                unity, ref_path, out_dir / "final_master_unity.wav", quiet=True)

    report["total_seconds"] = round(time.time() - t0, 1)
    (out_dir / "report.json").write_text(json.dumps(report, indent=2))
    return report


def format_report(rep: dict) -> str:
    lines = []
    w = max([len(s["name"]) for s in rep["stems"]] + [4])
    lines.append("")
    lines.append(f"  {'STEM':<{w}}  {'GAIN':>8}  {'LOUDNESS vs MIX':>15}")
    lines.append(f"  {'-' * w}  {'-' * 8}  {'-' * 15}")
    for s in rep["stems"]:
        gain = f"{s['gain_db']:+.1f} dB" if s["solved"] else "(silent)"
        lv = f"{s['lufs_vs_mix']:+.1f} LU" if s["lufs_vs_mix"] is not None else "-"
        lines.append(f"  {s['name']:<{w}}  {gain:>8}  {lv:>15}")
    lines.append("")
    lines.append(f"  {'BAND':<12} {'HZ':>13}  {'BEFORE':>7}  {'AFTER':>7}   (dB off the reference shape)")
    for bd in rep["bands"]:
        hz = f"{bd['hz'][0]}-{bd['hz'][1]}"
        lines.append(f"  {bd['name']:<12} {hz:>13}  {bd['err_before_db']:+7.1f}  {bd['err_after_db']:+7.1f}")
    lines.append(f"  {'RMS':<12} {'':>13}  {rep['band_rms_err_before_db']:7.1f}  {rep['band_rms_err_after_db']:7.1f}")
    lines.append("")
    return "\n".join(lines)
