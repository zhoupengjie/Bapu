"""Which torch device the model stages use.

NVIDIA (CUDA) is the validated path. AMD cards with a ROCm build of torch also
show up as "cuda". Intel Arc (XPU) and Apple Silicon (MPS) are experimental:
the stages run there, but results have not been compared with CUDA yet.
`BAPU_DEVICE` (cuda / xpu / mps / cpu) overrides the choice.
"""

from __future__ import annotations

import os
from typing import Any


def pick() -> str:
    import torch

    want = os.environ.get("BAPU_DEVICE", "").strip().lower()
    if want:
        return want
    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch, "xpu", None) is not None and torch.xpu.is_available():
        return "xpu"
    if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
        os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")  # ops MPS lacks run on the CPU
        return "mps"
    return "cpu"


def empty_cache(device: str) -> None:
    import torch

    if device.startswith("cuda") and torch.cuda.is_available():
        torch.cuda.empty_cache()
    elif device.startswith("xpu") and getattr(torch, "xpu", None) is not None:
        torch.xpu.empty_cache()
    elif device.startswith("mps") and getattr(torch, "mps", None) is not None:
        torch.mps.empty_cache()


def describe() -> dict[str, Any]:
    """For the 环境 page: the device and its name."""
    try:
        import torch
    except ImportError:
        return {"kind": "none", "name": "没有安装 torch", "torch": None}
    kind = pick()
    name = "CPU"
    try:
        if kind == "cuda":
            name = torch.cuda.get_device_name(0)
        elif kind == "xpu":
            name = torch.xpu.get_device_name(0)
        elif kind == "mps":
            name = "Apple GPU (MPS)"
    except Exception:  # noqa: BLE001
        pass
    build = "rocm" if getattr(torch.version, "hip", None) else "cuda" if torch.version.cuda else "cpu"
    return {"kind": kind, "name": name, "torch": torch.__version__, "build": build,
            "experimental": kind in ("xpu", "mps") or build == "rocm"}
