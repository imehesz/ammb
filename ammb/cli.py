"""Command line entry point.

    ammb offline  --stems <folder> (--ref <ref.wav> | --preset <name>) [options]
    ammb audacity [--preset <name>] [--check] [options]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, audio_io, pipeline


def _add_dsp_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--lam", type=float, default=0.05,
                   help="how strongly to stay near the rough mix (0 = pure spectral match; default 0.05)")
    p.add_argument("--bands", type=int, choices=(5, 10), default=10, help="analysis bands (default 10)")
    p.add_argument("--min-db", type=float, default=-20.0, help="lowest gain the solver may set (default -20)")
    p.add_argument("--max-db", type=float, default=9.5, help="highest gain the solver may set (default +9.5)")
    p.add_argument("--headroom", type=float, default=-6.0, help="pre-master true peak in dBTP (default -6)")
    p.add_argument("--ignore-tilt", action="store_true",
                   help="leave broad darker/brighter differences to Matchering's EQ; faders only fix bumps")
    p.add_argument("--no-balance", action="store_true",
                   help="keep your stem levels exactly as they are; only sum + master")
    p.add_argument("--no-master", action="store_true", help="stop after the balanced pre-master")
    p.add_argument("--compare", action="store_true",
                   help="also master the untouched mix (all stems 0 dB) -> final_master_unity.wav for A/B")


def _opts(a) -> pipeline.Options:
    return pipeline.Options(lam=a.lam, bands=a.bands, bounds_db=(a.min_db, a.max_db),
                            headroom_dbtp=a.headroom, ignore_tilt=a.ignore_tilt, balance=not a.no_balance, master=not a.no_master, compare=a.compare)


def cmd_offline(a) -> int:
    if a.list_presets:
        from . import presets
        for n in presets.available():
            print(f"  {n:<18} {presets.load(n)['name']}")
        return 0
    if bool(a.ref) == bool(a.preset):
        print("Give exactly one of --ref <file> or --preset <name>  (see --list-presets).")
        return 2
    ref = Path(a.ref).resolve() if a.ref else None
    if ref is not None and not ref.is_file():
        print(f"Reference not found: {ref}")
        return 2
    stems = []
    for s in a.stems:
        s = Path(s)
        stems.extend(audio_io.list_audio(s) if s.is_dir() else [s])
    stems = [p.resolve() for p in stems if p.resolve() != ref]
    # never treat a reference as a stem: our own synthetic one, or a REF.* sitting in the folder
    stems = [p for p in stems if not p.name.startswith("preset_reference_") and p.stem.lower() != "ref"]
    if not stems:
        print("No stems found (looked for .wav/.flac/.aif/.ogg).")
        return 2
    out = Path(a.out) if a.out else Path("ammb_out")
    opts = _opts(a)
    if a.preset:
        import soundfile as sf
        from . import presets
        ref = presets.prepare(a.preset, out, sf.info(str(stems[0])).samplerate, opts)
    rep = pipeline.run(stems, ref, out, opts)
    print(pipeline.format_report(rep))
    print(f"  Output folder: {out.resolve()}")
    print(f"  Done in {rep['total_seconds']} s")
    return 0


def cmd_audacity(a) -> int:
    from . import bridge
    if a.preset:
        from . import presets
        try:
            presets.load(a.preset)
        except FileNotFoundError as e:
            print(e)
            return 2
    return bridge.run(_opts(a), Path(a.out) if a.out else None, apply_gains=a.apply_gains,
                      preset=a.preset, check_only=a.check)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ammb", description=f"AMMB by M3HX v{__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    off = sub.add_parser("offline", help="balance + master a folder of stems against a reference file")
    off.add_argument("--stems", nargs="+", default=[], help="folder(s) or files of stems")
    off.add_argument("--ref", help="reference track (wav/flac/...)")
    off.add_argument("--preset", help="genre preset instead of a reference (e.g. psytrance)")
    off.add_argument("--list-presets", action="store_true", help="show available presets and exit")
    off.add_argument("--out", help="output folder (default ./ammb_out)")
    _add_dsp_args(off)
    off.set_defaults(func=cmd_offline)

    au = sub.add_parser("audacity", help="run on the open Audacity 3 session (mod-script-pipe)")
    au.add_argument("--preset", help="genre preset as the target (a REF track is then not needed)")
    au.add_argument("--check", action="store_true",
                    help="connect, show what would be exported/ignored, change nothing")
    au.add_argument("--out", help="keep working files + report here (default: temp folder, deleted)")
    au.add_argument("--apply-gains", action="store_true",
                    help="also write the solved gains onto the stem tracks' gain sliders")
    _add_dsp_args(au)
    au.set_defaults(func=cmd_audacity)

    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
