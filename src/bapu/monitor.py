"""Machine load for the sidebar: CPU, RAM, GPU, VRAM and network, sampled on request.

GPU numbers come from NVML (the NVIDIA driver) when there is one, otherwise from
the system (bapu.gpuload: Windows performance counters for AMD / Intel, the
IORegistry on Apple chips). Either way they include the model workers that run
in their own processes, not only this server.
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
        self._other: Any = None  # bapu.gpuload reader for other GPUs, False when there is none
        psutil.cpu_percent(None)  # the first call only sets the baseline
        self._net = self._net_now()

    @staticmethod
    def _net_now() -> tuple[float, int, int]:
        try:
            c = psutil.net_io_counters()
            return time.monotonic(), c.bytes_sent, c.bytes_recv
        except Exception:  # noqa: BLE001 - no counters on this system
            return time.monotonic(), 0, 0

    def _net_rate(self) -> dict[str, float]:
        """Bytes per second up and down for the whole machine, since the previous sample."""
        t0, up0, down0 = self._net
        t1, up1, down1 = self._net = self._net_now()
        dt = max(t1 - t0, 1e-3)
        return {"up": max(0, up1 - up0) / dt, "down": max(0, down1 - down0) / dt}

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
                "net": self._net_rate(),
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
            else:
                if self._other is None:
                    from bapu import gpuload

                    self._other = gpuload.reader() or False
                if self._other:
                    try:
                        out["gpu"] = self._other.sample()
                    except Exception:  # noqa: BLE001 - counters hiccup: skip this sample
                        pass
            return out
