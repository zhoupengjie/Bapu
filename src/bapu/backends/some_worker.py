"""Run SOME transcription in its own process (frees GPU memory on exit).

Usage: python -I -m bapu.backends.some_worker <args.json>
args: {"ckpt", "wav", "out_json"}. Uses the ported inference in
`bapu.backends.some`; stdout carries only "BAPU ..." progress lines.
"""

from __future__ import annotations

import json
import pathlib
import sys


def main() -> None:
    args = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
    from bapu.audio import read_mono
    from bapu.backends.some import SomeTranscriber

    model = SomeTranscriber(pathlib.Path(args["ckpt"]))
    print(f"BAPU device {model.device}", flush=True)
    waveform, _ = read_mono(pathlib.Path(args["wav"]), model.sr)
    chunks, notes = model.transcribe(
        waveform, progress=lambda i, n: print(f"BAPU progress {i} {n}", flush=True))
    out = {"sample_rate": model.sr, "chunks": chunks, "notes": notes}
    pathlib.Path(args["out_json"]).write_text(json.dumps(out), encoding="utf-8")


if __name__ == "__main__":
    main()
