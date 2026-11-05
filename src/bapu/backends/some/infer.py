# Ported from openvpi/SOME (commit e523eef): inference/me_infer.py,
# inference/base_infer.py, modules/rmvpe/spec.py, utils/infer_utils.py,
# utils/slicer2.py.
# Copyright (c) 2023 Team OpenVPI. MIT License, see LICENSE in this folder.
#
# Only what the released `0119_continuous256_5spk` model needs at inference:
# the model reads a log-mel spectrogram (its pitch input is all zeros, so no
# pitch extractor is needed).

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable

import numpy as np
import torch
import torch.nn.functional as F
import yaml

from bapu.backends.some.model import MidiConforms


# -- log-mel spectrogram (modules/rmvpe/spec.py, keyshift 0) ----------------------------
class MelSpectrogram(torch.nn.Module):
    def __init__(self, n_mel_channels: int, sampling_rate: int, win_length: int, hop_length: int,
                 mel_fmin: float = 0, mel_fmax: float | None = None, clamp: float = 1e-5) -> None:
        super().__init__()
        from librosa.filters import mel

        basis = mel(sr=sampling_rate, n_fft=win_length, n_mels=n_mel_channels, fmin=mel_fmin, fmax=mel_fmax, htk=True)
        self.register_buffer("mel_basis", torch.from_numpy(basis).float())
        self.register_buffer("window", torch.hann_window(win_length), persistent=False)
        self.win_length = win_length
        self.hop_length = hop_length
        self.clamp = clamp

    def forward(self, audio: torch.Tensor) -> torch.Tensor:
        audio = F.pad(audio, (self.win_length // 2, (self.win_length + 1) // 2))
        spec = torch.stft(audio, n_fft=self.win_length, hop_length=self.hop_length, win_length=self.win_length,
                          window=self.window, center=False, return_complex=True).abs()
        return torch.log(torch.clamp(torch.matmul(self.mel_basis, spec), min=self.clamp))


# -- decoding (utils/infer_utils.py) -------------------------------------------------------
def decode_gaussian_blurred_probs(probs: torch.Tensor, vmin: float, vmax: float, deviation: float, threshold: float):
    num_bins = int(probs.shape[-1])
    interval = (vmax - vmin) / (num_bins - 1)
    width = int(3 * deviation / interval)  # 3 sigma
    idx = torch.arange(num_bins, device=probs.device)[None, None, :]
    idx_values = idx * interval + vmin
    center = torch.argmax(probs, dim=-1, keepdim=True)
    start = torch.clip(center - width, min=0)
    end = torch.clip(center + width + 1, max=num_bins)
    weights = probs * ((idx >= start) & (idx < end))
    product_sum = torch.sum(weights * idx_values, dim=2)
    weight_sum = torch.sum(weights, dim=2)
    values = product_sum / (weight_sum + (weight_sum == 0))
    rest = probs.max(dim=-1)[0] < threshold
    return values, rest


def decode_bounds_to_alignment(bounds: torch.Tensor) -> torch.Tensor:
    step = bounds.cumsum(dim=1).round().long()
    inc = torch.diff(step, dim=1, prepend=torch.full((bounds.shape[0], 1), -1, dtype=step.dtype, device=step.device)) > 0
    return inc.long().cumsum(dim=1)


def decode_note_sequence(frame2item: torch.Tensor, values: torch.Tensor, masks: torch.Tensor, threshold: float = 0.5):
    b = frame2item.shape[0]
    space = frame2item.max() + 1
    item_dur = frame2item.new_zeros(b, space).scatter_add(1, frame2item, torch.ones_like(frame2item))[:, 1:]
    item_unmasked = frame2item.new_zeros(b, space).scatter_add(1, frame2item, masks.long())[:, 1:]
    item_masks = item_unmasked / item_dur >= threshold
    values_quant = values.round().long()
    histogram = frame2item.new_zeros(b, space * 128).scatter_add(
        1, frame2item * 128 + values_quant, torch.ones_like(frame2item) * masks
    ).unflatten(1, [space, 128])[:, 1:, :]
    center = histogram.float().argmax(dim=2).to(dtype=values.dtype)
    values_center = torch.gather(F.pad(center, [1, 0]), 1, frame2item)
    near = masks & (values >= values_center - 0.5) & (values <= values_center + 0.5)
    item_valid = frame2item.new_zeros(b, space).scatter_add(1, frame2item, near.long())[:, 1:]
    item_values = values.new_zeros(b, space).scatter_add(1, frame2item, values * near)[:, 1:] / (
        item_valid + (item_valid == 0))
    return item_values, item_dur, item_masks


# -- silence slicer (utils/slicer2.py) ---------------------------------------------------------
def _rms(y: np.ndarray, frame_length: int, hop_length: int) -> np.ndarray:
    y = np.pad(y, (frame_length // 2, frame_length // 2), mode="constant")
    frames = np.lib.stride_tricks.sliding_window_view(y, frame_length)[::hop_length]
    return np.sqrt(np.mean(np.abs(frames) ** 2, axis=-1))


class Slicer:
    """Cut a song at silences (times in ms, threshold in dB)."""

    def __init__(self, sr: int, threshold: float = -40.0, min_length: int = 5000, min_interval: int = 300,
                 hop_size: int = 20, max_sil_kept: int = 5000) -> None:
        if not min_length >= min_interval >= hop_size or not max_sil_kept >= hop_size:
            raise ValueError("need min_length >= min_interval >= hop_size and max_sil_kept >= hop_size")
        min_interval = sr * min_interval / 1000
        self.sr = sr
        self.threshold = 10 ** (threshold / 20.0)
        self.hop_size = round(sr * hop_size / 1000)
        self.win_size = min(round(min_interval), 4 * self.hop_size)
        self.min_length = round(sr * min_length / 1000 / self.hop_size)
        self.min_interval = round(min_interval / self.hop_size)
        self.max_sil_kept = round(sr * max_sil_kept / 1000 / self.hop_size)

    def _chunk(self, waveform: np.ndarray, begin: int, end: int) -> dict[str, Any]:
        return {"offset": begin * self.hop_size / self.sr,
                "waveform": waveform[begin * self.hop_size: min(waveform.shape[0], end * self.hop_size)]}

    def slice(self, waveform: np.ndarray) -> list[dict[str, Any]]:
        if (waveform.shape[0] + self.hop_size - 1) // self.hop_size <= self.min_length:
            return [{"offset": 0, "waveform": waveform}]
        rms = _rms(waveform, self.win_size, self.hop_size)
        tags: list[tuple[int, int]] = []
        silence_start = None
        clip_start = 0
        for i, r in enumerate(rms):
            if r < self.threshold:
                if silence_start is None:
                    silence_start = i
                continue
            if silence_start is None:
                continue
            leading = silence_start == 0 and i > self.max_sil_kept
            middle = i - silence_start >= self.min_interval and i - clip_start >= self.min_length
            if not leading and not middle:
                silence_start = None
                continue
            if i - silence_start <= self.max_sil_kept:
                pos = rms[silence_start: i + 1].argmin() + silence_start
                tags.append((0, pos) if silence_start == 0 else (pos, pos))
                clip_start = pos
            elif i - silence_start <= self.max_sil_kept * 2:
                pos = rms[i - self.max_sil_kept: silence_start + self.max_sil_kept + 1].argmin() + i - self.max_sil_kept
                pos_l = rms[silence_start: silence_start + self.max_sil_kept + 1].argmin() + silence_start
                pos_r = rms[i - self.max_sil_kept: i + 1].argmin() + i - self.max_sil_kept
                if silence_start == 0:
                    tags.append((0, pos_r))
                    clip_start = pos_r
                else:
                    tags.append((min(pos_l, pos), max(pos_r, pos)))
                    clip_start = max(pos_r, pos)
            else:
                pos_l = rms[silence_start: silence_start + self.max_sil_kept + 1].argmin() + silence_start
                pos_r = rms[i - self.max_sil_kept: i + 1].argmin() + i - self.max_sil_kept
                tags.append((0, pos_r) if silence_start == 0 else (pos_l, pos_r))
                clip_start = pos_r
            silence_start = None
        total = rms.shape[0]
        if silence_start is not None and total - silence_start >= self.min_interval:
            end = min(total, silence_start + self.max_sil_kept)
            tags.append((rms[silence_start: end + 1].argmin() + silence_start, total + 1))
        if not tags:
            return [{"offset": 0, "waveform": waveform}]
        chunks = []
        if tags[0][0] > 0:
            chunks.append(self._chunk(waveform, 0, tags[0][0]))
        for a, b in zip(tags, tags[1:]):
            chunks.append(self._chunk(waveform, a[1], b[0]))
        if tags[-1][1] < total:
            chunks.append(self._chunk(waveform, tags[-1][1], total))
        return chunks


# -- model + inference (inference/base_infer.py, inference/me_infer.py) ---------------------------
class SomeTranscriber:
    def __init__(self, ckpt: Path, device: str | None = None) -> None:
        ckpt = Path(ckpt)
        self.config = yaml.safe_load(ckpt.with_name("config.yaml").read_text(encoding="utf-8"))
        c = self.config
        if device is None:
            from bapu.device import pick

            device = pick()
        self.device = device
        self.sr = int(c["audio_sample_rate"])
        self.timestep = c["hop_size"] / c["audio_sample_rate"]
        self.model = MidiConforms(c).eval().to(self.device)
        state = torch.load(ckpt, map_location=self.device, weights_only=True)["state_dict"]
        state = OrderedDict((k[len("model."):], v) for k, v in state.items() if k.startswith("model."))
        self.model.load_state_dict(state, strict=True)
        self.mel = MelSpectrogram(c["units_dim"], self.sr, c["win_size"], c["hop_size"], c["fmin"], c["fmax"]).to(self.device)

    @torch.no_grad()
    def segment(self, waveform: np.ndarray) -> dict[str, np.ndarray]:
        c = self.config
        units = self.mel(torch.from_numpy(waveform).unsqueeze(0).to(self.device)).transpose(1, 2)
        masks = torch.ones(units.shape[:2], dtype=torch.bool, device=self.device)
        probs, bounds = self.model(units, masks)
        probs = probs * masks[..., None]
        bounds = bounds * masks
        frame2item = decode_bounds_to_alignment(bounds) * masks
        midi, rest = decode_gaussian_blurred_probs(probs, vmin=c["midi_min"], vmax=c["midi_max"],
                                                   deviation=c["midi_prob_deviation"], threshold=c["rest_threshold"])
        note_midi, note_dur, note_mask = decode_note_sequence(frame2item, midi, ~rest & masks)
        return {"note_midi": note_midi.squeeze(0).cpu().numpy(),
                "note_dur": note_dur.squeeze(0).cpu().numpy() * self.timestep,
                "note_rest": (~note_mask).squeeze(0).cpu().numpy()}

    def transcribe(self, waveform: np.ndarray, progress: Callable[[int, int], None] | None = None
                   ) -> tuple[int, list[dict[str, float]]]:
        """Notes (start, dur in seconds; fractional MIDI pitch) of a mono waveform at the model's rate."""
        chunks = Slicer(sr=self.sr, max_sil_kept=1000).slice(waveform)
        notes = []
        for i, chunk in enumerate(chunks):
            seg = self.segment(chunk["waveform"])
            t = float(chunk["offset"])
            for pitch, dur, rest in zip(seg["note_midi"], seg["note_dur"], seg["note_rest"]):
                if not rest:
                    notes.append({"start": round(t, 4), "dur": round(float(dur), 4), "pitch": round(float(pitch), 3)})
                t += float(dur)
            if progress:
                progress(i + 1, len(chunks))
        return len(chunks), notes
