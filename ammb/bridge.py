"""Audacity 3 session -> export -> balance -> master -> re-import.

Session rules
  * The reference is the wave track named REF (any case).
  * Every other wave track is a stem, except tracks named FINAL_MASTER*
    (our own output, so re-runs work) and tracks whose name starts with '#'.
  * Your fader gain/pan and effects are part of the export, i.e. your rough
    mix is the starting point; solved gains are relative to it.
"""

from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from . import audio_io, pipeline
from .pipe import AudacityPipe, PipeError, q

OUT_PREFIX = "FINAL_MASTER"


def _is_ref(t):
    return t["name"].strip().lower() == "ref"


def _classify(tracks, need_ref: bool = True):
    """-> (ref index or None, stem indices, {index: why it is ignored})"""
    ref, stems, ignored = None, [], {}
    for i, t in enumerate(tracks):
        name = t.get("name", "").strip()
        if t.get("kind") != "wave":
            ignored[i] = f"{t.get('kind', '?')} track"
            continue
        if _is_ref(t):
            if ref is not None:
                raise PipeError("More than one track is named REF - keep exactly one.")
            ref = i
        elif name.upper().startswith(OUT_PREFIX):
            ignored[i] = "earlier AMMB output"
        elif name.startswith("#"):
            ignored[i] = "name starts with #"
        else:
            stems.append(i)
    if ref is None and need_ref:
        raise PipeError("No track named 'REF' found. Import your reference song and rename its "
                        "track to REF, or use a genre preset (--preset techno).")
    if not stems:
        raise PipeError("No stem tracks found.")
    return ref, stems, ignored


def _print_plan(tracks, ref_i, stem_is, ignored, preset, end):
    print(f"Connected to Audacity: {len(tracks)} tracks, project length {end:.1f} s\n")
    for i, t in enumerate(tracks):
        if i in stem_is:
            role = "STEM"
        elif i == ref_i:
            role = "REF (ignored: using preset)" if preset else "REF"
        else:
            role = f"skip ({ignored.get(i, '')})"
        extra = ""
        if t.get("kind") == "wave":
            extra = f"  {t.get('start', 0):6.2f}-{t.get('end', 0):6.2f} s  gain {t.get('gain', 0):+.1f} dB"
            if t.get("mute"):
                extra += "  (muted - still exported)"
        print(f"  {i + 1:2d}. {t.get('name', ''):<24} {role:<28}{extra}")
    target = f"preset '{preset}'" if preset else "the REF track"
    print(f"\n  {len(stem_is)} stems will be exported and mastered against {target}.")


def _set_audio(pipe, index, **kw):
    pipe.send(f"SelectTracks: Track={index} TrackCount=1 Mode=Set")
    args = " ".join(f"{k}={v}" for k, v in kw.items())
    pipe.send(f"SetTrackAudio: {args}")


def _export_track(pipe, tracks, index, end, path):
    # mute everything else as a belt-and-braces guard, select just this track
    for j, t in enumerate(tracks):
        if t.get("kind") == "wave":
            _set_audio(pipe, j, Mute=int(j != index), Solo=0)
    pipe.send(f"SelectTracks: Track={index} TrackCount=1 Mode=Set")
    pipe.send(f"SelectTime: Start=0 End={end:.6f} RelativeTo=ProjectStart")
    pipe.send(f"Export2: Filename={q(path)} NumChannels=2")


def _align(path, track_start, project_end):
    """Audacity may skip leading silence before a track's first clip. Put it back."""
    info = sf.info(str(path))
    dur = info.frames / info.samplerate
    full, trimmed = project_end, project_end - track_start
    if track_start > 0.01 and abs(dur - trimmed) < abs(dur - full):
        data, sr = sf.read(str(path), dtype="float32", always_2d=True)
        pad = np.zeros((int(round(track_start * sr)), data.shape[1]), dtype=np.float32)
        sf.write(str(path), np.concatenate([pad, data]), sr, subtype="FLOAT")
        return True
    return False


def run(opts: pipeline.Options, out_dir: Path | None = None, apply_gains: bool = False,
        preset: str | None = None, check_only: bool = False) -> int:
    try:
        pipe = AudacityPipe()
    except PipeError as e:
        print(e)
        return 3

    if check_only:
        try:
            tracks = pipe.tracks()
            waves = [t for t in tracks if t.get("kind") == "wave"]
            end = max((t["end"] for t in waves), default=0.0)
            ref_i, stem_is, ignored = _classify(tracks, need_ref=not preset)
            _print_plan(tracks, ref_i, stem_is, ignored, preset, end)
            print("  --check: nothing was changed.")
            return 0
        except PipeError as e:
            print(f"\nERROR: {e}")
            return 4
        finally:
            pipe.close()

    work = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="ammb_"))
    keep = out_dir is not None
    exp = work / "exports"
    exp.mkdir(parents=True, exist_ok=True)
    tracks = pipe.tracks()
    saved = {i: (t.get("mute", 0), t.get("solo", 0)) for i, t in enumerate(tracks) if t.get("kind") == "wave"}

    try:
        ref_i, stem_is, ignored = _classify(tracks, need_ref=not preset)
        end = max(t["end"] for t in tracks if t.get("kind") == "wave")
        _print_plan(tracks, ref_i, stem_is, ignored, preset, end)
        print()

        export_is = stem_is + ([] if preset or ref_i is None else [ref_i])
        files, low_bit = {}, []
        for i in export_is:
            t = tracks[i]
            path = exp / (f"ref.wav" if i == ref_i else f"stem_{i:02d}.wav")
            print(f"  exporting '{t['name']}' ...")
            _export_track(pipe, tracks, i, end, path)
            if not path.is_file():
                raise PipeError(f"Export of '{t['name']}' produced no file at {path}")
            if i != ref_i and _align(path, float(t.get("start", 0.0)), end):
                print(f"    (re-aligned: restored {t['start']:.2f} s of leading silence)")
            if sf.info(str(path)).subtype in ("PCM_16", "PCM_U8"):
                low_bit.append(t["name"])
            files[i] = path
        if low_bit:
            print("  WARNING: Audacity exported 16-bit WAVs. For best quality, do one manual\n"
                  "  File > Export > Export Audio as WAV '32-bit float' - Audacity remembers it.")

        # restore the user's mute/solo before we touch anything else
        for i, (m, s) in saved.items():
            _set_audio(pipe, i, Mute=int(bool(m)), Solo=int(bool(s)))

        if preset:
            from . import presets
            ref_file = presets.prepare(preset, work, sf.info(str(files[stem_is[0]])).samplerate, opts)
        else:
            ref_file = files[ref_i]
        rep = pipeline.run([files[i] for i in stem_is], ref_file, work, opts,
                           stem_names=[tracks[i]["name"] for i in stem_is])
        print(pipeline.format_report(rep))

        if not opts.master:
            print(f"  --no-master: pre-master left at {rep['premaster']}")
            return 0

        # --- back into Audacity --------------------------------------------
        imports = [(work / "final_master.wav", OUT_PREFIX)]
        if opts.compare:
            imports.append((work / "final_master_unity.wav", OUT_PREFIX + "_UNITY"))
        for path, name in imports:
            before = len(pipe.tracks())
            pipe.send(f"Import2: Filename={q(path.resolve())}")
            now = pipe.tracks()
            new_i = len(now) - 1 if len(now) > before else None
            if new_i is None:
                raise PipeError(f"Import of {path} did not add a track")
            pipe.send(f"SelectTracks: Track={new_i} TrackCount=1 Mode=Set")
            pipe.send(f"SetTrackStatus: Name={q(name)}")
            _set_audio(pipe, new_i, Mute=int(name != OUT_PREFIX), Solo=0)

        if apply_gains:
            for i, s in zip(stem_is, rep["stems"]):
                old = float(tracks[i].get("gain", 0.0))
                _set_audio(pipe, i, Gain=f"{max(-36.0, min(36.0, old + s['gain_db'])):.2f}")
            print("  Solved gains written to the stem faders.")

        for i in stem_is + ([ref_i] if ref_i is not None else []):
            _set_audio(pipe, i, Mute=1)
        pipe.send("SelectNone:")
        if ref_i is not None:
            print("  Imported FINAL_MASTER. Stems + REF muted; solo REF vs FINAL_MASTER to A/B.")
        else:
            print("  Imported FINAL_MASTER. Stems muted.")
        if opts.compare:
            print("  FINAL_MASTER_UNITY (your mix untouched, same mastering) is imported muted.")
        return 0
    except PipeError as e:
        print(f"\nERROR: {e}")
        try:
            for i, (m, s) in saved.items():
                _set_audio(pipe, i, Mute=int(bool(m)), Solo=int(bool(s)))
        except PipeError:
            pass
        return 4
    finally:
        pipe.close()
        if keep:
            print(f"  Files kept in {work.resolve()}")
        else:
            time.sleep(0.2)
            shutil.rmtree(work, ignore_errors=True)
