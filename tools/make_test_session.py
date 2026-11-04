"""Synthesize a small test session with a KNOWN answer.

Writes 6 stems (drums split in 3, bass, pad, lead) at a deliberately bad
"rough mix" balance, plus REF.wav = the same stems at the TRUE balance,
squashed loud like a master. The solver should recover TRUE_DB (relative
to each other; the overall level is irrelevant).

    uv run python tools/make_test_session.py [out_folder]
"""

import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfilt

SR = 44100
BPM = 120
BARS = 8
BEAT = 60 / BPM
N = int(SR * BEAT * 4 * BARS)
rng = np.random.default_rng(7)
t = np.arange(N) / SR

# dB offsets that turn the rough mix into the "right" mix
TRUE_DB = {"kick": 0.0, "snare": -2.0, "hats": -8.0, "bass": 5.0, "pad": -7.0, "lead": 3.0}


def env(length, attack, decay):
    n = int(length * SR)
    e = np.exp(-np.arange(n) / (decay * SR))
    a = int(attack * SR)
    if a:
        e[:a] *= np.linspace(0, 1, a)
    return e


def place(hit, times):
    out = np.zeros(N)
    for tt in times:
        s = int(tt * SR)
        e = min(N, s + len(hit))
        out[s:e] += hit[: e - s]
    return out


def lp(x, hz, order=4):
    return sosfilt(butter(order, hz, "low", fs=SR, output="sos"), x)


def hp(x, hz, order=4):
    return sosfilt(butter(order, hz, "high", fs=SR, output="sos"), x)


def saw(freq_fn):
    phase = np.cumsum(freq_fn / SR)
    return 2 * (phase % 1.0) - 1


def stereo(x, pan=0.0):
    l = np.cos((pan + 1) * np.pi / 4)
    r = np.sin((pan + 1) * np.pi / 4)
    return np.stack([x * l, x * r], axis=1)


def notes_to_freq(seq, step):
    f = np.zeros(N)
    n = int(step * SR)
    for i in range(0, N, n):
        midi = seq[(i // n) % len(seq)]
        f[i:i + n] = 0 if midi is None else 440 * 2 ** ((midi - 69) / 12)
    return f


def gate(freq, step, length=0.85):
    n = int(step * SR)
    g = np.zeros(N)
    for i in range(0, N, n):
        if freq[i] > 0:
            g[i:i + int(n * length)] = 1
    return lp(g, 200, 2)


def build():
    beats = np.arange(BARS * 4) * BEAT
    # kick: pitch-swept sine
    kl = 0.35
    kt = np.arange(int(kl * SR)) / SR
    kick_hit = np.sin(2 * np.pi * (45 * kt + 60 * (1 - np.exp(-kt * 30)) / 30)) * env(kl, 0.001, 0.12)
    kick = stereo(place(kick_hit, beats))
    # snare: noise + body on 2 and 4
    sl = 0.25
    snare_hit = hp(rng.standard_normal(int(sl * SR)), 800) * env(sl, 0.001, 0.06) \
        + 0.5 * np.sin(2 * np.pi * 190 * np.arange(int(sl * SR)) / SR) * env(sl, 0.001, 0.04)
    snare = stereo(place(snare_hit, beats[1::2]))
    # hats: 8ths, high-passed noise, slightly right
    hl = 0.06
    hat_hit = hp(rng.standard_normal(int(hl * SR)), 7000) * env(hl, 0.0005, 0.015)
    hats = stereo(place(hat_hit, np.arange(BARS * 8) * BEAT / 2), pan=0.3)
    # bass: low-passed saw, 8ths
    bseq = [33, 33, 45, 33, 36, 36, 48, 36, 31, 31, 43, 31, 38, 38, 50, 38]
    bf = notes_to_freq(bseq, BEAT / 2)
    bass = stereo(lp(saw(bf), 500) * gate(bf, BEAT / 2))
    # pad: detuned saw chords, wide
    chords = [[57, 60, 64], [60, 64, 67], [55, 59, 62], [62, 65, 69]]
    pad_l = np.zeros(N)
    pad_r = np.zeros(N)
    bar = int(BEAT * 4 * SR)
    for c, s in enumerate(range(0, N, bar)):
        tt = t[s:s + bar]
        for m in chords[c % 4]:
            f = 440 * 2 ** ((m - 69) / 12)
            pad_l[s:s + bar] += 2 * ((f * 0.997 * tt) % 1) - 1
            pad_r[s:s + bar] += 2 * ((f * 1.003 * tt) % 1) - 1
    pad = np.stack([lp(pad_l, 2500), lp(pad_r, 2500)], axis=1) * 0.3
    # lead: brighter saw melody, 16ths with rests
    lseq = [69, None, 72, 74, 76, None, 74, 72, 69, None, 67, 69, 72, None, None, None]
    lf = notes_to_freq(lseq, BEAT / 2)
    lead = stereo(lp(saw(lf), 6000) * gate(lf, BEAT / 2, 0.7), pan=-0.15)

    stems = {"kick": kick, "snare": snare, "hats": hats, "bass": bass, "pad": pad, "lead": lead}
    # normalise each stem to the same RMS = a flat, characterless "rough mix"
    for k, v in stems.items():
        stems[k] = v / np.sqrt(np.mean(v ** 2)) * 10 ** (-24 / 20)
    return stems


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "test_session")
    out.mkdir(parents=True, exist_ok=True)
    stems = build()
    for k, v in stems.items():
        sf.write(out / f"{k}.wav", v.astype(np.float32), SR, subtype="FLOAT")
    ref = sum(v * 10 ** (TRUE_DB[k] / 20) for k, v in stems.items())
    ref = ref / np.max(np.abs(ref)) * 2.0          # drive into a soft clipper = "mastered"
    ref = np.tanh(ref) * 0.95
    sf.write(out / "REF.wav", ref.astype(np.float32), SR, subtype="FLOAT")
    print(f"Wrote {len(stems)} stems + REF.wav to {out.resolve()}")
    print("Expected relative gains:", TRUE_DB)


if __name__ == "__main__":
    main()
