"""Machine load for the progress page: CPU, RAM, GPU and VRAM, sampled on request.

GPU numbers come from NVML (the NVIDIA driver), so they include the model
workers that run in their own processes, not only this server.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import psutil


class LoadMonitor:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._gpu: Any = None  # NVML handle, False when there is no NVIDIA GPU / driver
        psutil.cpu_percent(None)  # the first call only sets the baseline

    def _handle(self) -> Any:
        if self._gpu is None:
            try:
                import pynvml

                pynvml.nvmlInit()
                self._gpu = pynvml.nvmlDeviceGetHandleByIndex(0)
            except Exception:  # noqa: BLE001 - no NVIDIA driver, no GPU numbers
                self._gpu = False
        return self._gpu

    def sample(self) -> dict[str, Any]:
        with self._lock:
            vm = psutil.virtual_memory()
            out: dict[str, Any] = {
                "at": time.time(),
                "cpu": psutil.cpu_percent(None),  # since the previous sample
                "cpu_count": psutil.cpu_count(),
                "ram": {"used": vm.total - vm.available, "total": vm.total},
                "gpu": None,
            }
            h = self._handle()
            if h:
                try:
                    import pynvml

                    util = pynvml.nvmlDeviceGetUtilizationRates(h)
                    mem = pynvml.nvmlDeviceGetMemoryInfo(h)
                    gpu: dict[str, Any] = {"name": pynvml.nvmlDeviceGetName(h), "util": util.gpu,
                                           "mem_used": mem.used, "mem_total": mem.total}
                    try:
                        gpu["temp"] = pynvml.nvmlDeviceGetTemperature(h, pynvml.NVML_TEMPERATURE_GPU)
                    except Exception:  # noqa: BLE001
                        pass
                    out["gpu"] = gpu
                except Exception:  # noqa: BLE001 - driver hiccup: skip this sample
                    pass
            return out
