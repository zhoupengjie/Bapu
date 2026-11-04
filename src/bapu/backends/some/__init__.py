"""SOME (Singing-Oriented MIDI Extractor) inference, ported from openvpi/SOME.

Copyright (c) 2023 Team OpenVPI, MIT License (see LICENSE in this folder).
Weights come from the model registry; no external checkout is needed.
"""

from bapu.backends.some.infer import SomeTranscriber

__all__ = ["SomeTranscriber"]
