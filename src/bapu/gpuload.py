"""GPU load without NVIDIA's driver library: for AMD, Intel and Apple chips.

Windows: the system's own performance counters ("GPU Engine" and "GPU Adapter
Memory", the numbers Task Manager shows), read through PDH with ctypes. They
cover every vendor and every process, including the model workers.
macOS: the GPU's statistics from the IORegistry (`ioreg`, no admin rights);
Apple chips share memory with the CPU, so "VRAM" is the memory the GPU uses
out of all of it.

Each reader returns {"name", "util", "mem_used", "mem_total"} or None when it
cannot tell; it never raises.
"""

from __future__ import annotations

import re
import subprocess
import sys
from typing import Any


class WindowsCounters:
    """Utilization = the busiest engine (summed over processes), like Task Manager;
    memory = dedicated memory in use on the adapter that uses the most."""

    ENGINE = r"\GPU Engine(*)\Utilization Percentage"
    MEMORY = r"\GPU Adapter Memory(*)\Dedicated Usage"

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self.ct = ctypes
        pdh = ctypes.WinDLL("pdh.dll")
        self.pdh = pdh
        pdh.PdhOpenQueryW.argtypes = [wintypes.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p)]
        pdh.PdhAddEnglishCounterW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p)]
        pdh.PdhCollectQueryData.argtypes = [ctypes.c_void_p]
        pdh.PdhGetFormattedCounterArrayW.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                                     ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        for f in (pdh.PdhOpenQueryW, pdh.PdhAddEnglishCounterW, pdh.PdhCollectQueryData, pdh.PdhGetFormattedCounterArrayW):
            f.restype = wintypes.DWORD
        self.query = ctypes.c_void_p()
        if pdh.PdhOpenQueryW(None, 0, ctypes.byref(self.query)):
            raise OSError("PdhOpenQuery failed")
        self.engine, self.memory = ctypes.c_void_p(), ctypes.c_void_p()
        if pdh.PdhAddEnglishCounterW(self.query, self.ENGINE, 0, ctypes.byref(self.engine)) or \
                pdh.PdhAddEnglishCounterW(self.query, self.MEMORY, 0, ctypes.byref(self.memory)):
            raise OSError("no GPU performance counters on this system")
        pdh.PdhCollectQueryData(self.query)  # rate counters need a first reading
        self.adapters = _windows_adapters()

    def _values(self, counter: Any) -> dict[str, float]:
        ct = self.ct
        from ctypes import wintypes

        class Value(ct.Structure):
            _fields_ = [("status", wintypes.DWORD), ("value", ct.c_double)]  # PDH_FMT_COUNTERVALUE (double)

        class Item(ct.Structure):
            _fields_ = [("name", wintypes.LPWSTR), ("value", Value)]

        PDH_FMT_DOUBLE, PDH_MORE_DATA = 0x200, 0x800007D2
        size, count = wintypes.DWORD(0), wintypes.DWORD(0)
        rc = self.pdh.PdhGetFormattedCounterArrayW(counter, PDH_FMT_DOUBLE, ct.byref(size), ct.byref(count), None)
        if rc != PDH_MORE_DATA or not size.value:
            return {}
        buf = (ct.c_byte * size.value)()
        if self.pdh.PdhGetFormattedCounterArrayW(counter, PDH_FMT_DOUBLE, ct.byref(size), ct.byref(count), buf):
            return {}
        items = ct.cast(buf, ct.POINTER(Item))
        return {items[i].name: items[i].value.value for i in range(count.value) if items[i].value.status in (0, 1)}

    def sample(self) -> dict[str, Any] | None:
        if self.pdh.PdhCollectQueryData(self.query):
            return None
        engines: dict[tuple[str, str], float] = {}
        for name, v in self._values(self.engine).items():
            # pid_1234_luid_0x00000000_0x0000D1A3_phys_0_eng_3_engtype_Compute
            m = re.search(r"luid_(0x[0-9A-Fa-f]+_0x[0-9A-Fa-f]+)_phys_\d+_eng_(\d+)", name)
            if m:
                engines[(m.group(1), m.group(2))] = engines.get((m.group(1), m.group(2)), 0.0) + v
        memory: dict[str, float] = {}
        for name, v in self._values(self.memory).items():
            m = re.search(r"luid_(0x[0-9A-Fa-f]+_0x[0-9A-Fa-f]+)", name)
            if m:
                memory[m.group(1)] = memory.get(m.group(1), 0.0) + v
        if not engines and not memory:
            return None
        # the adapter doing the work: most dedicated memory in use (the discrete GPU when there is one)
        luid = max(memory, key=memory.get) if memory else max(engines, key=engines.get)[0]
        util = max((v for (l, _), v in engines.items() if l == luid), default=0.0)
        name, total = self.adapters[0] if self.adapters else ("GPU", 0)
        return {"name": name, "util": round(min(100.0, util)), "mem_used": int(memory.get(luid, 0)), "mem_total": total}


def _windows_adapters() -> list[tuple[str, int]]:
    """Display adapters from the registry, largest dedicated memory first: (name, bytes)."""
    import winreg

    out: list[tuple[str, int]] = []
    base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as cls:
            for i in range(64):
                try:
                    sub = winreg.EnumKey(cls, i)
                except OSError:
                    break
                try:
                    with winreg.OpenKey(cls, sub) as k:
                        name = winreg.QueryValueEx(k, "DriverDesc")[0]
                        size = 0
                        for value in ("HardwareInformation.qwMemorySize", "HardwareInformation.MemorySize"):
                            try:
                                raw = winreg.QueryValueEx(k, value)[0]
                                size = int.from_bytes(raw, "little") if isinstance(raw, bytes) else int(raw)
                                break
                            except OSError:
                                continue
                        out.append((str(name), size))
                except OSError:
                    continue
    except OSError:
        pass
    return sorted(out, key=lambda x: -x[1])


class MacGPU:
    """Apple chips: utilization and the memory the GPU holds, from IOAccelerator's statistics."""

    def __init__(self) -> None:
        import psutil

        self.total = psutil.virtual_memory().total
        self.name = _run(["sysctl", "-n", "machdep.cpu.brand_string"]).strip() or "Apple GPU"
        if self.sample() is None:
            raise OSError("no GPU statistics")

    def sample(self) -> dict[str, Any] | None:
        text = _run(["ioreg", "-r", "-d", "1", "-w", "0", "-c", "IOAccelerator"])
        util = re.search(r'"Device Utilization %"\s*=\s*(\d+)', text)
        if not util:
            return None
        mem = re.search(r'"In use system memory"\s*=\s*(\d+)', text)
        return {"name": self.name, "util": int(util.group(1)), "mem_used": int(mem.group(1)) if mem else 0,
                "mem_total": self.total}


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def reader() -> Any:
    """The fallback reader for this system, or None."""
    try:
        if sys.platform == "win32":
            return WindowsCounters()
        if sys.platform == "darwin":
            return MacGPU()
    except Exception:  # noqa: BLE001 - no counters: no GPU numbers
        return None
    return None
