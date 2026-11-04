"""Run Qwen3-ASR in its own process (heavy imports, frees GPU memory on exit).

Usage: python -I -m bapu.backends.qwen_asr_worker <args.json>
args: {"model_dir", "wav", "segments": [[t0, t1], ...], "language", "out_json"}
Writes {"segments": [{"text", "language"}]}; stdout carries only "BAPU ..." lines.
"""

from __future__ import annotations

import json
import pathlib
import sys


def main() -> None:
    args = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
    import torch
    from qwen_asr import Qwen3ASRModel

    from bapu.audio import read_mono

    sr = 16000
    y, _ = read_mono(pathlib.Path(args["wav"]), sr)
    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    dtype = torch.bfloat16 if device != "cpu" else torch.float32
    model = Qwen3ASRModel.from_pretrained(args["model_dir"], dtype=dtype, device_map=device, max_new_tokens=256)
    print(f"BAPU device {device}", flush=True)

    audios = [(y[int(t0 * sr): int(t1 * sr)], sr) for t0, t1 in args["segments"]]
    out, batch = [], 8
    for k in range(0, len(audios), batch):
        res = model.transcribe(audio=audios[k: k + batch], language=args.get("language"))
        out += [{"text": r.text, "language": r.language} for r in res]
        print(f"BAPU progress {min(k + batch, len(audios))} {len(audios)}", flush=True)
    pathlib.Path(args["out_json"]).write_text(json.dumps({"segments": out}, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
