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


def _classify(tracks):
    ref, stems = None, []
    for i, t in enumerate(tracks):
        if t.get("kind") != "wave":
            continue
        name = t["name"].strip()
        if _is_ref(t):
            if ref is not None:
                raise PipeError("More than one track is named REF - keep exactly one.")
            ref = i
        elif name.upper().startswith(OUT_PREFIX) or name.startswith("#"):
            continue
        else:
            stems.append(i)
    if ref is None:
        raise PipeError("No track named 'REF' found. Import your reference song and rename its track to REF.")
    if not stems:
        raise PipeError("Found REF but no stem tracks.")
    return ref, stems


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


def run(opts: pipeline.Options, out_dir: Path | None = None, apply_gains: bool = False) -> int:
    try:
        pipe = AudacityPipe()
    except PipeError as e:
        print(e)
        return 3

    work = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="ammb_"))
    keep = out_dir is not None
    exp = work / "exports"
    exp.mkdir(parents=True, exist_ok=True)
    tracks = pipe.tracks()
    saved = {i: (t.get("mute", 0), t.get("solo", 0)) for i, t in enumerate(tracks) if t.get("kind") == "wave"}

    try:
        ref_i, stem_is = _classify(tracks)
        end = max(t["end"] for t in tracks if t.get("kind") == "wave")
        print(f"Session: {len(stem_is)} stems, REF = track {ref_i + 1}, {end:.1f} s")

        files, low_bit = {}, []
        for i in stem_is + [ref_i]:
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

        rep = pipeline.run([files[i] for i in stem_is], files[ref_i], work, opts,
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

        for i in stem_is + [ref_i]:
            _set_audio(pipe, i, Mute=1)
        pipe.send("SelectNone:")
        print("  Imported FINAL_MASTER. Stems + REF muted; solo REF vs FINAL_MASTER to A/B.")
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
