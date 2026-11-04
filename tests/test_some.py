"""Ported SOME pieces on synthetic data (no weights needed)."""

from __future__ import annotations

import numpy as np
import torch

from bapu.backends.some.infer import (
    MelSpectrogram, Slicer, decode_bounds_to_alignment, decode_gaussian_blurred_probs, decode_note_sequence,
)
from bapu.backends.some.model import MidiConforms


def test_decode_note_sequence_example():
    # The example from SOME's utils/infer_utils.py.
    frame2item = torch.LongTensor([[1, 1, 1, 1, 2, 2, 3, 3, 3, 0, 0, 0, 0, 0],
                                   [1, 1, 1, 2, 3, 3, 3, 3, 3, 4, 4, 0, 0, 0]])
    values = torch.FloatTensor([[60, 61, 60.5, 63, 57, 57, 50, 55, 54, 0, 0, 0, 0, 0],
                                [50, 51, 50.5, 53, 47, 47, 40, 45, 44, 38, 38, 0, 0, 0]])
    item_values, item_dur, item_masks = decode_note_sequence(frame2item, values, frame2item > 0)
    assert item_dur[0, :3].tolist() == [4, 2, 3]
    assert abs(float(item_values[0, 0]) - 60.25) < 1e-6  # 60 and 60.5 are near the 60 centre; 61 and 63 are not
    assert item_values[0, 1].item() == 57


def test_bounds_and_probs_decoding():
    bounds = torch.tensor([[0.9, 0.0, 0.0, 1.0, 0.0, 0.8]])
    assert decode_bounds_to_alignment(bounds).tolist() == [[1, 1, 1, 2, 2, 3]]
    probs = torch.zeros(1, 2, 128)
    probs[0, 0, 60] = 0.9
    probs[0, 1, 70] = 0.05
    values, rest = decode_gaussian_blurred_probs(probs, vmin=0, vmax=127, deviation=1.0, threshold=0.1)
    assert values[0, 0].item() == 60 and rest.tolist() == [[False, True]]


def test_slicer_cuts_at_silence():
    sr = 8000
    tone = (0.5 * np.sin(2 * np.pi * 220 * np.arange(sr * 6) / sr)).astype(np.float32)
    y = np.concatenate([tone, np.zeros(sr * 2, np.float32), tone])
    chunks = Slicer(sr=sr, max_sil_kept=1000).slice(y)
    assert len(chunks) == 2
    assert chunks[1]["offset"] > 6.0


def test_tiny_model_runs_on_cpu():
    cfg = {"units_dim": 16, "midi_num_bins": 128, "midi_extractor_args": {
        "lay": 1, "dim": 32, "attention_heads": 2, "attention_heads_dim": 8, "kernel_size": 3}}
    model = MidiConforms(cfg).eval()
    mel = MelSpectrogram(16, 8000, 256, 64)
    with torch.no_grad():
        units = mel(torch.randn(1, 4000)).transpose(1, 2)
        probs, bounds = model(units, torch.ones(units.shape[:2], dtype=torch.bool))
    assert probs.shape == (1, units.shape[1], 128) and bounds.shape == (1, units.shape[1])
    assert float(probs.min()) >= 0 and float(probs.max()) <= 1
    # Checkpoint layout: every key sits under "model." like the released weights.
    assert all(k.startswith("model.") for k in model.state_dict())
