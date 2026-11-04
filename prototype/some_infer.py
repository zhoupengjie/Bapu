"""Run SOME on one wav and write a MIDI file plus a JSON note table.

Usage: python -I some_infer.py <some_src_dir> <model.ckpt> <input.wav> <out.mid> <tempo_bpm> <out.json>
Mirrors SOME's infer.py, and additionally keeps the un-rounded note pitch.
"""

import importlib
import json
import pathlib
import sys


def main() -> None:
    some_src, ckpt, wav, out_mid, tempo, out_json = sys.argv[1:7]
    tempo = float(tempo)
    sys.path.insert(0, some_src)

    import librosa
    import numpy as np
    import yaml

    import inference
    from utils.infer_utils import build_midi_file
    from utils.slicer2 import Slicer

    model_path = pathlib.Path(ckpt)
    with open(model_path.with_name("config.yaml"), "r", encoding="utf8") as f:
        config = yaml.safe_load(f)
    infer_cls_name = inference.task_inference_mapping[config["task_cls"]]
    pkg, cls_name = infer_cls_name.rsplit(".", 1)
    infer_cls = getattr(importlib.import_module(pkg), cls_name)
    infer_ins = infer_cls(config=config, model_path=model_path)
    print("device", infer_ins.device, "sample_rate", config["audio_sample_rate"])

    waveform, _ = librosa.load(wav, sr=config["audio_sample_rate"], mono=True)
    slicer = Slicer(sr=config["audio_sample_rate"], max_sil_kept=1000)
    chunks = slicer.slice(waveform)
    segments = infer_ins.infer([c["waveform"] for c in chunks])

    midi_file = build_midi_file([c["offset"] for c in chunks], segments, tempo=tempo)
    midi_file.save(out_mid)

    notes = []
    for chunk, seg in zip(chunks, segments):
        t = float(chunk["offset"])
        for pitch, dur, rest in zip(seg["note_midi"], seg["note_dur"], seg["note_rest"]):
            if not rest:
                notes.append({"start": round(t, 4), "dur": round(float(dur), 4), "pitch": round(float(pitch), 3)})
            t += float(dur)
    with open(out_json, "w", encoding="utf8") as f:
        json.dump({"tempo": tempo, "chunks": len(chunks), "notes": notes}, f)
    print(f"chunks={len(chunks)} notes={len(notes)} midi={out_mid}")


if __name__ == "__main__":
    main()
