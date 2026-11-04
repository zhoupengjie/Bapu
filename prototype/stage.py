"""Run one audio-separator model on one file, keeping original levels.

Usage: python -I stage.py <input> <output_dir> <model_dir> <model_filename> <names_json>
names_json maps stem names (any case) to output basenames, e.g. '{"vocals": "lead"}'.
"""

import json
import sys
import time
from pathlib import Path

from audio_separator.separator import Separator


def main() -> None:
    src, out_dir, model_dir = (Path(p) for p in sys.argv[1:4])
    model = sys.argv[4]
    wanted = json.loads(sys.argv[5])
    out_dir.mkdir(parents=True, exist_ok=True)

    # Stem keys differ in case between models; accept every common spelling.
    names: dict[str, str] = {}
    for key, value in wanted.items():
        for variant in {key, key.lower(), key.capitalize(), key.title(), key.upper()}:
            names[variant] = value

    sep = Separator(
        model_file_dir=str(model_dir),
        output_dir=str(out_dir),
        output_format="WAV",
        normalization_threshold=1.0,  # only scale down if a stem would clip
    )
    sep.load_model(model_filename=model)
    t0 = time.perf_counter()
    outputs = sep.separate(str(src), names)
    print(f"elapsed_seconds={time.perf_counter() - t0:.1f}")
    for o in outputs:
        print("output", o)


if __name__ == "__main__":
    main()
