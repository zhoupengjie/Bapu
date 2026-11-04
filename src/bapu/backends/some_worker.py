"""Run SOME in its own process (its modules are named `inference`, `utils`, ...).

Usage: python -I -m bapu.backends.some_worker <args.json>
args: {"some_src", "ckpt", "wav", "out_json"}. Mirrors SOME's infer.py but keeps
the un-rounded pitch. Progress lines on stdout start with "BAPU ".
"""

from __future__ import annotations

import importlib
import json
import pathlib
import sys


def main() -> None:
    args = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
    sys.path.insert(0, args["some_src"])

    import yaml

    from bapu.audio import read_mono

    import inference  # SOME's package
    from utils.slicer2 import Slicer

    ckpt = pathlib.Path(args["ckpt"])
    with open(ckpt.with_name("config.yaml"), encoding="utf8") as f:
        config = yaml.safe_load(f)
    pkg, cls_name = inference.task_inference_mapping[config["task_cls"]].rsplit(".", 1)
    infer_cls = getattr(importlib.import_module(pkg), cls_name)
    model = infer_cls(config=config, model_path=ckpt)
    sr = int(config["audio_sample_rate"])
    print(f"BAPU device {model.device}", flush=True)

    waveform, _ = read_mono(pathlib.Path(args["wav"]), sr)
    chunks = Slicer(sr=sr, max_sil_kept=1000).slice(waveform)
    notes = []
    for i, chunk in enumerate(chunks):
        seg = model.infer([chunk["waveform"]])[0]
        t = float(chunk["offset"])
        for pitch, dur, rest in zip(seg["note_midi"], seg["note_dur"], seg["note_rest"]):
            if not rest:
                notes.append({"start": round(t, 4), "dur": round(float(dur), 4), "pitch": round(float(pitch), 3)})
            t += float(dur)
        print(f"BAPU progress {i + 1} {len(chunks)}", flush=True)

    out = {"sample_rate": sr, "chunks": len(chunks), "notes": notes}
    pathlib.Path(args["out_json"]).write_text(json.dumps(out), encoding="utf-8")


if __name__ == "__main__":
    main()
