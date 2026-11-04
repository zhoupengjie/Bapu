"""Sanity-check a vocals/instrumental split against the original mix.

Usage: python -I check.py <original.wav> <vocals.wav> <instrumental.wav>
"""

import sys

import numpy as np
import soundfile as sf


def db(x: float) -> float:
    return 20 * np.log10(max(x, 1e-12))


def load(path: str):
    data, sr = sf.read(path, always_2d=True, dtype="float64")
    return data, sr


def main() -> None:
    orig, sr_o = load(sys.argv[1])
    voc, sr_v = load(sys.argv[2])
    ins, sr_i = load(sys.argv[3])
    print(f"sample_rate original={sr_o} vocals={sr_v} instrumental={sr_i}")
    n = min(len(orig), len(voc), len(ins))
    print(f"seconds original={len(orig)/sr_o:.2f} vocals={len(voc)/sr_v:.2f} instrumental={len(ins)/sr_i:.2f}")
    if sr_v != sr_i:
        print("stem sample rates differ; skipping reconstruction check")
        return
    if sr_o != sr_v:
        import soxr

        orig = soxr.resample(orig, sr_o, sr_v, quality="VHQ")
        print(f"resampled original {sr_o} -> {sr_v} Hz for comparison")
    n = min(len(orig), len(voc), len(ins))
    orig, voc, ins = orig[:n], voc[:n], ins[:n]

    for name, x in (("original", orig), ("vocals", voc), ("instrumental", ins)):
        rms = np.sqrt(np.mean(x**2))
        print(f"{name:12s} peak={db(np.max(np.abs(x))):6.1f} dBFS  rms={db(rms):6.1f} dBFS")

    resid = orig - (voc + ins)
    snr = db(np.sqrt(np.mean(orig**2))) - db(np.sqrt(np.mean(resid**2)))
    print(f"reconstruction: original vs vocals+instrumental SNR={snr:.1f} dB")

    # Fraction of 50 ms windows in which the vocal stem is essentially silent.
    win = int(0.05 * sr_v)
    frames = voc[: (n // win) * win].reshape(-1, win, voc.shape[1])
    frame_db = 20 * np.log10(np.sqrt(np.mean(frames**2, axis=(1, 2))) + 1e-12)
    print(f"vocal frames below -50 dBFS: {np.mean(frame_db < -50):.0%} of {len(frame_db)} x 50 ms")


if __name__ == "__main__":
    main()
