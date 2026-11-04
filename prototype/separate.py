"""Separate one audio file into vocals / instrumental with BS-RoFormer (viperx 1297).

Usage: python -I separate.py <input.wav> <output_dir> <model_dir>
"""

import sys
import time
from pathlib import Path

from audio_separator.separator import Separator

MODEL = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"


def main() -> None:
    src, out_dir, model_dir = (Path(p) for p in sys.argv[1:4])
    out_dir.mkdir(parents=True, exist_ok=True)
    model_dir.mkdir(parents=True, exist_ok=True)

    sep = Separator(
        model_file_dir=str(model_dir),
        output_dir=str(out_dir),
        output_format="WAV",
    )
    sep.load_model(model_filename=MODEL)

    stem = src.stem
    names = {
        "Vocals": f"{stem}_vocals_bs-roformer",
        "Instrumental": f"{stem}_instrumental_bs-roformer",
    }
    t0 = time.perf_counter()
    outputs = sep.separate(str(src), names)
    print(f"elapsed_seconds={time.perf_counter() - t0:.1f}")
    for o in outputs:
        print("output", o)


if __name__ == "__main__":
    main()
