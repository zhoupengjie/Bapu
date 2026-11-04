import sys
import numpy as np, soundfile as sf, soxr
o, so = sf.read(sys.argv[1], always_2d=True, dtype="float64")
v, sv = sf.read(sys.argv[2], always_2d=True, dtype="float64")
i, si = sf.read(sys.argv[3], always_2d=True, dtype="float64")
o = soxr.resample(o, so, sv, quality="VHQ")
n = min(len(o), len(v), len(i)); o, v, i = o[:n].ravel(), v[:n].ravel(), i[:n].ravel()
A = np.stack([v, i], axis=1)
(a, b), *_ = np.linalg.lstsq(A, o, rcond=None)
r = o - (a * v + b * i)
snr = 10 * np.log10(np.mean(o**2) / np.mean(r**2))
print(f"gain vocals={a:.3f} ({20*np.log10(a):+.2f} dB)  instrumental={b:.3f} ({20*np.log10(b):+.2f} dB)")
print(f"reconstruction SNR after gain fit = {snr:.1f} dB")
