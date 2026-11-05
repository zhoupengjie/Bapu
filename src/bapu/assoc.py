"""Optional: open .bapu files with Bapu on double-click (Windows, current user only).

The portable app never does this by itself; the user turns it on from the 环境
page (or `bapu register`) and can turn it off again. Without it, Windows'
"Open with → always" works too, and projects open from inside Bapu.
"""

from __future__ import annotations

import sys
from pathlib import Path

from bapu.project import EXT

CLASSES = r"Software\Classes"
PROG = "Bapu.Project"


def supported() -> bool:
    return sys.platform == "win32"


def _delete_tree(key: str) -> None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_ALL_ACCESS) as k:
            while True:
                try:
                    _delete_tree(f"{key}\\{winreg.EnumKey(k, 0)}")
                except OSError:
                    break
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
    except FileNotFoundError:
        pass


def _notify() -> None:
    import ctypes

    ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)  # SHCNE_ASSOCCHANGED


def current() -> str | None:
    """The command .bapu files open with now (None: not associated with Bapu)."""
    if not supported():
        return None
    import winreg

    try:
        if winreg.QueryValue(winreg.HKEY_CURRENT_USER, f"{CLASSES}\\{EXT}") != PROG:
            return None
        return winreg.QueryValue(winreg.HKEY_CURRENT_USER, f"{CLASSES}\\{PROG}\\shell\\open\\command")
    except OSError:
        return None


def associate(command: str, icon: str | None = None) -> None:
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, f"{CLASSES}\\{EXT}") as k:
        winreg.SetValue(k, "", winreg.REG_SZ, PROG)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, f"{CLASSES}\\{PROG}") as k:
        winreg.SetValue(k, "", winreg.REG_SZ, "Bapu 工程")
    if icon:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, f"{CLASSES}\\{PROG}\\DefaultIcon") as k:
            winreg.SetValue(k, "", winreg.REG_SZ, icon)
    else:
        _delete_tree(f"{CLASSES}\\{PROG}\\DefaultIcon")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, f"{CLASSES}\\{PROG}\\shell\\open\\command") as k:
        winreg.SetValue(k, "", winreg.REG_SZ, command)
    _notify()


def associate_app(exe: Path) -> str:
    command = f'"{exe}" "%1"'
    associate(command, f'"{exe}",0')
    return command


def remove() -> None:
    _delete_tree(f"{CLASSES}\\{EXT}")
    _delete_tree(f"{CLASSES}\\{PROG}")
    _notify()
