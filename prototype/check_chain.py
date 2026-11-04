"""Usage: python -I check_chain.py <vocals> <lead> <backing> <lead_dry> <lead_reverb>"""
import sys
import numpy as np, soundfile as sf

def load(p):
    x, sr = sf.read(p, always_2d=True, dtype="float64"); return x, sr

def db(x): return 20 * np.log10(np.maximum(x, 1e-12))

paths = dict(zip(["vocals", "lead", "backing", "lead_dry", "lead_reverb"], sys.argv[1:6]))
data = {}
for k, p in paths.items():
    x, sr = load(p); data[k] = x
n = min(len(x) for x in data.values())
data = {k: v[:n] for k, v in data.items()}
for k, x in data.items():
    print(f"{k:12s} peak={db(np.max(np.abs(x)))[()]:6.1f} dBFS  rms={db(np.sqrt(np.mean(x**2)))[()]:6.1f} dBFS")

def snr(ref, est):
    return 10 * np.log10(np.mean(ref**2) / np.mean((ref - est) ** 2))
print(f"vocals vs lead+backing      SNR={snr(data['vocals'], data['lead'] + data['backing']):.1f} dB")
print(f"lead   vs lead_dry+reverb   SNR={snr(data['lead'], data['lead_dry'] + data['lead_reverb']):.1f} dB")

win = int(0.05 * sr)
def frames(x):
    f = x[: (n // win) * win].reshape(-1, win, x.shape[1]); return db(np.sqrt(np.mean(f**2, axis=(1, 2))))
L, B, Dr = frames(data["lead"]), frames(data["backing"]), frames(data["lead_dry"])
lead_on = L > -45
back_on = B > -45
strong_back = back_on & (B > L - 10)
print(f"frames: lead active {lead_on.mean():.0%}, backing active {back_on.mean():.0%}, "
      f"backing within 10 dB of lead {strong_back.mean():.0%}")

# Merge strong-backing frames into segments of >= 1 s (gaps < 0.5 s bridged).
segs, start, gap = [], None, 0
for i, on in enumerate(strong_back):
    if on:
        start = i if start is None else start; gap = 0
    elif start is not None:
        gap += 1
        if gap > 10:
            end = i - gap
            if (end - start + 1) * 0.05 >= 1.0: segs.append((start, end))
            start, gap = None, 0
if start is not None and (len(strong_back) - start) * 0.05 >= 1.0: segs.append((start, len(strong_back) - 1))
fmt = lambda f: f"{int(f*0.05//60)}:{f*0.05%60:04.1f}"
print("segments with prominent backing vocals (>=1 s):")
for s, e in segs: print(f"  {fmt(s)} - {fmt(e+1)}  ({(e-s+1)*0.05:.1f} s)")
print(f"reverb removed: lead_reverb rms is {db(np.sqrt(np.mean(data['lead_reverb']**2)))[()] - db(np.sqrt(np.mean(data['lead']**2)))[()]:.1f} dB relative to lead")
