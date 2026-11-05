"""Ranged, resumable downloads against a local HTTP server; the 环境 page's queue."""

from __future__ import annotations

import hashlib
import http.server
import os
import threading
import time
from pathlib import Path

import pytest

from bapu import sources
from bapu.downloads import Downloads
from bapu.models import registry as reg_mod
from bapu.models.registry import DownloadPaused, Registry


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, root: Path, ranges: bool = True, delay: float = 0.0):
        self.root, self.ranges, self.delay = root, ranges, delay
        self.requests: list[str | None] = []
        super().__init__(("127.0.0.1", 0), Handler)


class Handler(http.server.BaseHTTPRequestHandler):
    server: Server

    def log_message(self, *a):  # quiet
        pass

    def do_GET(self):
        data = (self.server.root / self.path.lstrip("/")).read_bytes()
        rng = self.headers.get("Range")
        self.server.requests.append(rng)
        start, end = 0, len(data) - 1
        if rng and self.server.ranges:
            a, _, b = rng.removeprefix("bytes=").partition("-")
            start, end = int(a), int(b) if b else len(data) - 1
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
        else:
            self.send_response(200)
        body = data[start:end + 1]
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        for i in range(0, len(body), 1 << 16):
            if self.server.delay:
                time.sleep(self.server.delay)
            try:
                self.wfile.write(body[i:i + (1 << 16)])
            except OSError:
                return


@pytest.fixture
def blob(tmp_path):
    root = tmp_path / "upstream"
    root.mkdir()
    data = os.urandom(3 * (1 << 20) + 12345)
    (root / "big.bin").write_bytes(data)
    return root, data


def serve(root: Path, **kw) -> Server:
    s = Server(root, **kw)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


@pytest.fixture(autouse=True)
def small_segments(monkeypatch):
    monkeypatch.setattr(reg_mod, "MIN_SEGMENT", 1 << 20)  # split the 3 MB test file into ranges
    monkeypatch.setattr(reg_mod, "BLOCK", 1 << 16)


def registry(tmp_path: Path, url: str, data: bytes) -> Registry:
    f = tmp_path / "registry.toml"
    f.write_text(f"""
[[model]]
id = "big"
stage = "separate"
name = "Big"
backend = "test"
dir = "models/big"
entry = "big.bin"
license = {{ name = "MIT", commercial = "yes" }}

[[model.files]]
name = "big.bin"
size = {len(data)}
sha256 = "{hashlib.sha256(data).hexdigest()}"
urls = ["{url}"]
""", encoding="utf-8")
    return Registry.load(f, cache_root=tmp_path / "cache")


def test_ranged_download_in_parallel(tmp_path, blob):
    root, data = blob
    s = serve(root)
    reg = registry(tmp_path, f"http://127.0.0.1:{s.server_port}/big.bin", data)
    reg.pull(reg.get("big"), accept_license=True, threads=4)
    target = tmp_path / "cache" / "models" / "big" / "big.bin"
    assert target.read_bytes() == data
    assert not (target.parent / "big.bin.part.json").exists()
    assert sum(1 for r in s.requests if r and r != "bytes=0-0") == 3  # 3 MB / 1 MB segments
    s.shutdown()


def test_pause_then_resume_keeps_what_arrived(tmp_path, blob):
    root, data = blob
    s = serve(root, delay=0.05)
    reg = registry(tmp_path, f"http://127.0.0.1:{s.server_port}/big.bin", data)
    spec = reg.get("big")
    stop = threading.Event()
    threading.Timer(0.3, stop.set).start()
    with pytest.raises(DownloadPaused):
        reg.pull(spec, accept_license=True, threads=3, stop=stop)
    part = tmp_path / "cache" / "models" / "big" / "big.bin.part"
    have = reg.downloaded(spec)
    assert part.exists() and (part.parent / "big.bin.part.json").exists()
    assert 0 < have < len(data)
    s.requests.clear()
    reg.pull(spec, accept_license=True, threads=3)
    assert (part.parent / "big.bin").read_bytes() == data
    # the resumed requests only ask for what had not arrived
    asked = 0
    for r in s.requests:
        a, _, b = r.removeprefix("bytes=").partition("-")
        asked += int(b) - int(a) + 1
    assert asked == len(data) - have
    assert reg.downloaded(spec) == len(data)
    s.shutdown()


def test_server_without_ranges_falls_back_to_one_stream(tmp_path, blob):
    root, data = blob
    s = serve(root, ranges=False)
    reg = registry(tmp_path, f"http://127.0.0.1:{s.server_port}/big.bin", data)
    reg.pull(reg.get("big"), accept_license=True, threads=8)
    assert (tmp_path / "cache" / "models" / "big" / "big.bin").read_bytes() == data
    s.shutdown()


def test_queue_pause_and_status(tmp_path, blob, monkeypatch):
    root, data = blob
    s = serve(root, delay=0.05)
    reg = registry(tmp_path, f"http://127.0.0.1:{s.server_port}/big.bin", data)
    monkeypatch.setattr(sources, "threads", lambda: 2)
    q = Downloads(reg, ["big"])
    assert q.status()["items"][0]["done"] == 0
    q.start()
    time.sleep(0.4)
    st = q.pause()
    assert st["state"] == "paused"
    for _ in range(50):
        if q._thread is None or not q._thread.is_alive():
            break
        time.sleep(0.1)
    st = q.status()
    assert st["state"] == "paused" and 0 < st["items"][0]["done"] < len(data)
    q.start()
    for _ in range(100):
        if q.status()["state"] == "idle":
            break
        time.sleep(0.1)
    st = q.status()
    assert st["state"] == "idle" and st["items"][0]["status"] == "ready" and st["items"][0]["done"] == len(data)
    s.shutdown()
