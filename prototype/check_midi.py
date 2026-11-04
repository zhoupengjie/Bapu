"""Usage: python -I check_midi.py <file.mid> <notes.json>"""
import json, sys
import numpy as np, mido

mf = mido.MidiFile(sys.argv[1])
tpb = mf.ticks_per_beat
tempo_us = next((m.tempo for t in mf.tracks for m in t if m.type == "set_tempo"), 500000)
bpm = mido.tempo2bpm(tempo_us)
notes, active, t = [], {}, 0
for m in mf.tracks[0]:
    t += m.time
    if m.type == "note_on" and m.velocity > 0:
        active[m.note] = t
    elif m.type in ("note_off", "note_on"):
        s = active.pop(m.note); notes.append((s, t, m.note))
notes.sort()
on = np.array([n[0] for n in notes]) / tpb; off = np.array([n[1] for n in notes]) / tpb
pitch = np.array([n[2] for n in notes]); dur_q = off - on
sec = 60 / bpm
print(f"tracks={len(mf.tracks)} ticks_per_beat={tpb} tempo={bpm:.2f} BPM notes={len(notes)}")
print(f"overlaps={int(np.sum(on[1:] < off[:-1] - 1e-9))}")
print(f"pitch range: {mido.Message('note_on', note=int(pitch.min())).note} .. {int(pitch.max())} "
      f"({librosa_name(pitch.min()) if False else ''})".rstrip(" ()"))
names = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"]
nm = lambda p: f"{names[p%12]}{p//12-1}"
print(f"pitch range named: {nm(int(pitch.min()))} .. {nm(int(pitch.max()))}, median {nm(int(np.median(pitch)))}")
d_ms = dur_q * sec * 1000
print(f"duration ms: min={d_ms.min():.0f} p10={np.percentile(d_ms,10):.0f} median={np.median(d_ms):.0f} p90={np.percentile(d_ms,90):.0f} max={d_ms.max():.0f}")
print(f"short notes: <60 ms {np.sum(d_ms<60)}, <100 ms {np.sum(d_ms<100)}, <150 ms {np.sum(d_ms<150)}")
gaps_ms = (on[1:] - off[:-1]) * sec * 1000
print(f"gaps: touching (<1 ms) {np.sum(gaps_ms<1)}, 1-50 ms {np.sum((gaps_ms>=1)&(gaps_ms<50))}, 50-300 ms {np.sum((gaps_ms>=50)&(gaps_ms<300))}, >=300 ms {np.sum(gaps_ms>=300)}")
jumps = np.abs(np.diff(pitch))
print(f"interval between consecutive notes: >=7 semitones {np.sum(jumps>=7)}, >=12 {np.sum(jumps>=12)}")
same = np.sum((jumps == 0) & (gaps_ms < 1))
print(f"touching repeated same-pitch neighbours (possible split notes): {same}")
grid = 0.25  # sixteenth
dev = np.abs(on/grid - np.round(on/grid)) * grid * sec * 1000
print(f"onset distance to 1/16 grid at {bpm:.0f} BPM: median {np.median(dev):.0f} ms, within 30 ms {np.mean(dev<30):.0%}")
print(f"first note at {on[0]*sec:.2f} s, last note ends {off[-1]*sec:.2f} s")

data = json.load(open(sys.argv[2], encoding="utf8"))
fp = np.array([n["pitch"] for n in data["notes"]])
frac = np.abs(fp - np.round(fp))
print(f"SOME float pitch: distance to nearest semitone median {np.median(frac)*100:.0f} cents, >30 cents {np.mean(frac>0.3):.0%} of notes")
