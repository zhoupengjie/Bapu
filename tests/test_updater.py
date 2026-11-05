"""Updates: find a newer release, download it through the chosen source, stage it for the desktop shell."""

from __future__ import annotations

import functools
import os
import hashlib
import http.server
import json
import threading
import time

import pytest

from bapu import __version__, updater


@pytest.fixture
def release(tmp_path, monkeypatch):
    """A release served from a local web server."""
    site = tmp_path / "site"
    site.mkdir()
    zip_bytes = b"PK fake update archive " * 1000
    (site / "Bapu-app-99.0.0.zip").write_bytes(zip_bytes)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(site))
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def publish(version="99.0.0", sha=None):
        latest = {"version": version, "notes": "说明", "date": "2026-11-05", "signature": "ab" * 64,
                  "zip": {"name": "Bapu-app-99.0.0.zip", "url": f"{base}/Bapu-app-99.0.0.zip", "size": len(zip_bytes),
                          "sha256": sha or hashlib.sha256(zip_bytes).hexdigest()}}
        (site / "latest.json").write_text(json.dumps(latest), encoding="utf-8")

    monkeypatch.setenv("BAPU_UPDATE_URL", f"{base}/latest.json")
    monkeypatch.setenv("BAPU_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("BAPU_HOME", str(tmp_path / "home"))  # a portable install may update itself
    monkeypatch.setattr(updater, "SELF_UPDATE", True)  # as on Windows (the Mac app does not replace itself)
    yield publish
    srv.shutdown()


def wait(u, states, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if u.status()["state"] in states:
            return u.status()
        time.sleep(0.05)
    raise AssertionError(u.status())


def test_versions_compare_as_numbers():
    assert updater.parse_version("0.10.0") > updater.parse_version("0.9.9")
    assert updater.parse_version("v1.2") == (1, 2)


def test_newer_release_is_found_downloaded_and_staged(release, tmp_path):
    release()
    u = updater.Updater()
    s = u.check()
    assert s["state"] == "available" and s["latest"]["version"] == "99.0.0" and s["current"] == __version__
    u.download()
    s = wait(u, ("ready", "error"))
    assert s["state"] == "ready", s
    staged = json.loads((tmp_path / "data" / "update" / "staged.json").read_text(encoding="utf-8"))
    assert staged["version"] == "99.0.0" and (tmp_path / "data" / "update" / staged["zip"]).exists()
    assert updater.Updater().check()["state"] == "ready"  # after a restart of the service, too


def test_same_version_is_latest(release):
    release(version=__version__)
    assert updater.Updater().check()["state"] == "latest"


def test_a_wrong_sha_is_not_staged(release, tmp_path):
    release(sha="0" * 64)
    u = updater.Updater()
    u.check()
    u.download()
    s = wait(u, ("ready", "error"))
    assert s["state"] == "error" and "SHA-256" in s["error"]
    assert not (tmp_path / "data" / "update" / "staged.json").exists()


def test_dev_checkout_does_not_install(release, monkeypatch):
    release()
    monkeypatch.delenv("BAPU_HOME")
    u = updater.Updater()
    assert u.check()["can_install"] is False
    with pytest.raises(ValueError, match="git pull"):
        u.download()


def test_checks_at_the_same_time_do_not_deadlock(release):
    """The header's daily check and a click on 检查更新 can overlap."""
    release()
    u = updater.Updater()
    u.state = "checking"  # one check already running
    done = []
    th = threading.Thread(target=lambda: done.append(u.check()))
    th.start()
    th.join(timeout=5)
    assert done and done[0]["state"] == "checking"
    u.state = "idle"
    assert u.check()["state"] == "available"


def test_no_release_yet_is_not_an_error(release, monkeypatch):
    release()
    monkeypatch.setenv("BAPU_UPDATE_URL", os.environ["BAPU_UPDATE_URL"].replace("latest.json", "missing.json"))
    s = updater.Updater().check()
    assert s["state"] == "latest" and s["error"] is None


def test_mac_app_only_announces(release, monkeypatch):
    release()
    monkeypatch.setattr(updater, "SELF_UPDATE", False)
    u = updater.Updater()
    assert u.check()["state"] == "available"  # the new version is still announced
    assert u.status()["can_install"] is False
    with pytest.raises(ValueError):
        u.download()
