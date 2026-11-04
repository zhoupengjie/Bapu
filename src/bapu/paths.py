"""Where Bapu keeps model weights and external checkouts.

Defaults are relative to the current directory (the project checkout), and each
can be moved with an environment variable.
"""

from __future__ import annotations

import os
from pathlib import Path


def cache_dir() -> Path:
    """Root of downloaded weights (`.cache/`, gitignored)."""
    return Path(os.environ.get("BAPU_CACHE_DIR", ".cache")).resolve()


def models_dir() -> Path:
    return cache_dir() / "models"


def torch_home() -> Path:
    """TORCH_HOME for torchaudio pipelines such as MMS_FA."""
    return cache_dir() / "torch"

