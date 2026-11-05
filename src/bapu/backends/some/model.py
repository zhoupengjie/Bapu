# Ported from openvpi/SOME (commit e523eef): modules/model/Gmidi_conform.py,
# modules/conform/Gconform.py, modules/conv/base_conv.py,
# modules/attention/base_attention.py.
# Copyright (c) 2023 Team OpenVPI. MIT License, see LICENSE in this folder.
#
# Inference only. Attribute names are unchanged so the released checkpoint
# loads with strict=True; einops is replaced by view/transpose.

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F


class GLU(nn.Module):
    def __init__(self, dim: int) -> None:
        super().__init__()
        self.dim = dim

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out, gate = x.chunk(2, dim=self.dim)
        return out * gate.sigmoid()


class ConformFFN(nn.Module):
    def __init__(self, dim: int, drop1: float = 0.1, drop2: float = 0.1) -> None:
        super().__init__()
        self.ln1 = nn.Linear(dim, dim * 4)
        self.ln2 = nn.Linear(dim * 4, dim)
        self.drop1 = nn.Dropout(drop1) if drop1 > 0.0 else nn.Identity()
        self.drop2 = nn.Dropout(drop2) if drop2 > 0.0 else nn.Identity()
        self.act = nn.SiLU()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.drop2(self.ln2(self.drop1(self.act(self.ln1(x)))))


class ConformConv(nn.Module):
    def __init__(self, channels: int, kernel_size: int = 31, dropout: float = 0.1, bias: bool = True) -> None:
        super().__init__()
        assert (kernel_size - 1) % 2 == 0
        self.act2 = nn.SiLU()
        self.act1 = GLU(1)
        self.pointwise_conv1 = nn.Conv1d(channels, 2 * channels, kernel_size=1, stride=1, padding=0, bias=bias)
        self.depthwise_conv = nn.Conv1d(channels, channels, kernel_size, stride=1, padding=(kernel_size - 1) // 2,
                                        groups=channels, bias=bias)
        self.norm = nn.BatchNorm1d(channels)
        self.pointwise_conv2 = nn.Conv1d(channels, channels, kernel_size=1, stride=1, padding=0, bias=bias)
        self.drop = nn.Dropout(dropout) if dropout > 0.0 else nn.Identity()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = x.transpose(1, 2)
        x = self.act1(self.pointwise_conv1(x))
        x = self.act2(self.norm(self.depthwise_conv(x)))
        return self.drop(self.pointwise_conv2(x)).transpose(1, 2)


class Attention(nn.Module):
    def __init__(self, dim: int, heads: int = 4, dim_head: int = 32) -> None:
        super().__init__()
        self.heads = heads
        hidden = dim_head * heads
        self.to_q = nn.Linear(dim, hidden, bias=False)
        self.to_kv = nn.Linear(dim, hidden * 2, bias=False)
        self.to_out = nn.Sequential(nn.Linear(hidden, dim))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        b, t, _ = x.shape
        q = self.to_q(x)
        k, v = self.to_kv(x).chunk(2, dim=2)
        q, k, v = (z.view(b, t, self.heads, -1).transpose(1, 2) for z in (q, k, v))  # b t (h c) -> b h t c
        if q.is_cuda and not getattr(torch.version, "hip", None):
            # The original disables the math kernel on CUDA (flash / memory-efficient only). AMD's ROCm
            # build reports "cuda" too, but its Windows wheels may lack those kernels: let it choose.
            from torch.nn.attention import SDPBackend, sdpa_kernel

            with sdpa_kernel([SDPBackend.FLASH_ATTENTION, SDPBackend.EFFICIENT_ATTENTION, SDPBackend.CUDNN_ATTENTION]):
                out = F.scaled_dot_product_attention(q, k, v)
        else:
            out = F.scaled_dot_product_attention(q, k, v)
        return self.to_out(out.transpose(1, 2).reshape(b, t, -1))


class ConformBlock(nn.Module):
    def __init__(self, dim: int, kernel_size: int, conv_drop: float, ffn_latent_drop: float, ffn_out_drop: float,
                 attention_drop: float, attention_heads: int, attention_heads_dim: int) -> None:
        super().__init__()
        self.ffn1 = ConformFFN(dim, ffn_latent_drop, ffn_out_drop)
        self.ffn2 = ConformFFN(dim, ffn_latent_drop, ffn_out_drop)
        self.att = Attention(dim, heads=attention_heads, dim_head=attention_heads_dim)
        self.attdrop = nn.Dropout(attention_drop) if attention_drop > 0.0 else nn.Identity()
        self.conv = ConformConv(dim, kernel_size=kernel_size, dropout=conv_drop)
        self.norm1 = nn.LayerNorm(dim)
        self.norm2 = nn.LayerNorm(dim)
        self.norm3 = nn.LayerNorm(dim)
        self.norm4 = nn.LayerNorm(dim)
        self.norm5 = nn.LayerNorm(dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.ffn1(self.norm1(x)) * 0.5 + x
        x = self.attdrop(self.att(self.norm2(x))) + x
        x = self.conv(self.norm3(x)) + x
        x = self.ffn2(self.norm4(x)) * 0.5 + x
        return self.norm5(x)


class Gcf(nn.Module):
    """Two coupled conformer streams: pitch classes and note boundaries."""

    def __init__(self, dim: int, **block) -> None:
        super().__init__()
        self.att1 = ConformBlock(dim, **block)
        self.att2 = ConformBlock(dim, **block)
        self.glu1 = nn.Sequential(nn.Linear(dim, dim * 2), GLU(2))
        self.glu2 = nn.Sequential(nn.Linear(dim, dim * 2), GLU(2))

    def forward(self, midi: torch.Tensor, bound: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        midi = self.att1(midi)
        bound = self.att2(bound)
        return midi + self.glu2(bound), bound + self.glu1(midi)


class GmidiConform(nn.Module):
    def __init__(self, lay: int, dim: int, indim: int, outdim: int, use_lay_skip: bool = True, kernel_size: int = 31,
                 conv_drop: float = 0.1, ffn_latent_drop: float = 0.1, ffn_out_drop: float = 0.1,
                 attention_drop: float = 0.1, attention_heads: int = 4, attention_heads_dim: int = 64) -> None:
        super().__init__()
        block = dict(kernel_size=kernel_size, conv_drop=conv_drop, ffn_latent_drop=ffn_latent_drop,
                     ffn_out_drop=ffn_out_drop, attention_drop=attention_drop, attention_heads=attention_heads,
                     attention_heads_dim=attention_heads_dim)
        self.inln = nn.Linear(indim, dim)
        self.inln1 = nn.Linear(indim, dim)
        self.outln = nn.Linear(dim, outdim)
        self.cutheard = nn.Linear(dim, 1)
        self.lay = lay
        self.use_lay_skip = use_lay_skip
        self.cf_lay = nn.ModuleList([Gcf(dim, **block) for _ in range(lay)])
        self.att1 = ConformBlock(dim, **block)
        self.att2 = ConformBlock(dim, **block)

    def forward(self, x: torch.Tensor, mask: torch.Tensor | None = None) -> tuple[torch.Tensor, torch.Tensor]:
        x1 = self.inln1(x)
        x = self.inln(x)
        if mask is not None:
            x = x.masked_fill(~mask.unsqueeze(-1), 0)
        for layer in self.cf_lay:
            x, x1 = layer(x, x1)
            if mask is not None:
                x = x.masked_fill(~mask.unsqueeze(-1), 0)
        x, x1 = self.att1(x), self.att2(x1)
        bounds = torch.sigmoid(self.cutheard(x1)).squeeze(-1)
        return self.outln(x), bounds


class MidiConforms(nn.Module):
    """Wrapper whose state-dict prefix (`model.`) matches the checkpoint."""

    def __init__(self, config: dict) -> None:
        super().__init__()
        args = dict(config["midi_extractor_args"])
        args.update(indim=config["units_dim"], outdim=config["midi_num_bins"])
        self.model = GmidiConform(**args)

    def forward(self, x: torch.Tensor, mask: torch.Tensor | None = None) -> tuple[torch.Tensor, torch.Tensor]:
        midi, bounds = self.model(x, mask)
        return torch.sigmoid(midi), bounds
