"""Where Bapu keeps model weights and its own data.

Development: relative to the current directory (the checkout): `.cache/` for
weights, `work/` for the recent-projects list.

Portable app: the desktop shell sets `BAPU_HOME` to the program folder and
`BAPU_CACHE_DIR` to it as well, so everything stays in that folder:

    Bapu/
      Bapu.exe
      app/        Bapu's Python code and web UI
      runtime/    Python and its packages (installed on first launch)
      models/     model weights (also the torch hub for MMS_FA)
      data/       recent projects, logs

Each location can be moved with its environment variable.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def home() -> Path | None:
    """The portable program folder, when running from one."""
    h = os.environ.get("BAPU_HOME")
    return Path(h).resolve() if h else None


def cache_dir() -> Path:
    """Root of downloaded weights: `<cache>/models/...` (`.cache/` in a checkout)."""
    return Path(os.environ.get("BAPU_CACHE_DIR", ".cache")).resolve()


def models_dir() -> Path:
    return cache_dir() / "models"


def torch_home() -> Path:
    """TORCH_HOME for torchaudio pipelines such as MMS_FA (inside the models folder)."""
    return models_dir() / "torch"


def data_dir() -> Path:
    """Bapu's own settings and recent-projects list: `<home>/data` (portable) or `work/`."""
    d = os.environ.get("BAPU_DATA_DIR")
    if d:
        return Path(d).resolve()
    h = home()
    return h / "data" if h else Path("work").resolve()


def runtime_info() -> dict[str, Any] | None:
    """What the first-launch setup installed (portable app only)."""
    h = home()
    p = h / "runtime" / "ready.json" if h else None
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p and p.exists() else None
    except ValueError:
        return None
