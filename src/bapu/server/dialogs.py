"""Native file / folder dialogs for the local web UI.

A browser never tells the page where a chosen file lives, but Bapu runs on the
user's own machine, so the server can show the system dialog and return the
real path. Each dialog runs in its own short-lived Python process (Tk wants
the main thread) and returns `None` when cancelled.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

KINDS = {
    "audio": ("选择原曲", [("音频或视频", "*.wav *.flac *.mp3 *.m4a *.aac *.ogg *.opus *.mp4 *.mov *.mkv *.webm"),
                          ("所有文件", "*.*")]),
    "lead_dry": ("选择主唱干声", [("音频或视频", "*.wav *.flac *.mp3 *.m4a *.aac *.ogg *.opus *.mp4 *.mov *.mkv *.webm"),
                             ("所有文件", "*.*")]),
    "instrumental": ("选择伴奏", [("音频或视频", "*.wav *.flac *.mp3 *.m4a *.aac *.ogg *.opus *.mp4 *.mov *.mkv *.webm"),
                              ("所有文件", "*.*")]),
    "lyrics": ("选择参考歌词", [("字幕或歌词", "*.ass *.ssa *.lrc *.txt"), ("所有文件", "*.*")]),
    "notes": ("选择 SV 工程或 MIDI", [("SV 工程或 MIDI", "*.svp *.mid *.midi"), ("所有文件", "*.*")]),
    "project": ("打开 Bapu 工程", [("Bapu 工程", "*.bapu")]),
    "folder": ("选择工程文件夹", None),
}

_SCRIPT = r"""
import json, sys, tkinter
from tkinter import filedialog
a = json.loads(sys.argv[1])
root = tkinter.Tk()
root.withdraw()
root.attributes("-topmost", True)
root.update()
opts = {"title": a["title"], "parent": root}
if a.get("initial"):
    opts["initialdir"] = a["initial"]
if a["types"] is None:
    p = filedialog.askdirectory(mustexist=False, **opts)
else:
    p = filedialog.askopenfilename(filetypes=[tuple(t) for t in a["types"]], **opts)
root.destroy()
sys.stdout.write(json.dumps(p or None))
"""


class DialogError(RuntimeError):
    pass


def ask(kind: str, initial: str | None = None) -> str | None:
    """Show the dialog for `kind` (audio / lyrics / notes / project / folder); the chosen path or None."""
    if kind not in KINDS:
        raise DialogError(f"没有这种对话框：{kind}")
    title, types = KINDS[kind]
    if initial and not Path(initial).is_dir():
        initial = str(Path(initial).parent) if Path(initial).parent.is_dir() else None
    args = json.dumps({"title": title, "types": types, "initial": initial}, ensure_ascii=False)
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        r = subprocess.run([sys.executable, "-c", _SCRIPT, args], capture_output=True, text=True,
                           encoding="utf-8", creationflags=flags, timeout=3600)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise DialogError(f"打不开系统对话框：{e}") from None
    if r.returncode != 0:
        raise DialogError("打不开系统对话框（这台机器上可能没有图形界面）")
    path = json.loads(r.stdout or "null")
    return str(Path(path)) if path else None


def suggest_folder(audio: Path) -> Path:
    """Default project folder: next to the song, named after it."""
    audio = Path(audio)
    return audio.parent / f"{audio.stem} - Bapu"
