"""Usage: python -I tempo.py <audio>  -- estimate global tempo and beat grid."""
import sys
import numpy as np, librosa
y, sr = librosa.load(sys.argv[1], sr=22050, mono=True)
onset = librosa.onset.onset_strength(y=y, sr=sr)
tempo, beats = librosa.beat.beat_track(onset_envelope=onset, sr=sr, units="time")
tempo = float(np.atleast_1d(tempo)[0])
ibi = np.diff(beats)
print(f"beat_track tempo={tempo:.2f} BPM, beats={len(beats)}, first beat at {beats[0]:.3f} s")
print(f"median inter-beat interval -> {60/np.median(ibi):.2f} BPM; IBI std={np.std(ibi)*1000:.1f} ms")
# Tempo stability across the song: per-quarter median.
q = np.array_split(ibi, 4)
print("per-quarter BPM:", ", ".join(f"{60/np.median(x):.1f}" for x in q))
# Candidate tempi from the tempogram for half/double ambiguity.
tg = librosa.feature.tempogram(onset_envelope=onset, sr=sr)
freqs = librosa.tempo_frequencies(tg.shape[0], sr=sr)
ac = tg.mean(axis=1)
mask = (freqs > 50) & (freqs < 220)
top = np.argsort(ac[mask])[::-1][:5]
print("tempogram peaks:", ", ".join(f"{freqs[mask][i]:.1f}" for i in top))
