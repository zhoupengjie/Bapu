"""Download source: official servers or China mirrors; files are still checked by SHA-256."""

from __future__ import annotations

import pytest

from bapu import sources


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("BAPU_DATA_DIR", str(tmp_path))


def test_default_is_auto_and_official_until_measured():
    assert sources.choice() == "auto" and sources.resolved() == "official"
    assert sources.mirror_urls(["https://huggingface.co/a/b"]) == ["https://huggingface.co/a/b"]


def test_china_puts_mirrors_first_and_keeps_the_originals():
    sources.save(source="china")
    hf = "https://huggingface.co/Qwen/Qwen3-ASR-1.7B/resolve/abc/config.json"
    gh = "https://github.com/openvpi/SOME/releases/download/v1/x.zip"
    other = "https://dl.fbaipublicfiles.com/mms/model.pt"
    assert sources.mirror_urls([hf]) == ["https://hf-mirror.com/Qwen/Qwen3-ASR-1.7B/resolve/abc/config.json", hf]
    assert sources.mirror_urls([gh, other]) == [f"https://ghfast.top/{gh}", gh, other]


def test_auto_follows_what_was_measured(monkeypatch):
    speeds = {sources.table()["probe"]["official"]: 1e5, sources.table()["probe"]["china"]: 5e6}
    monkeypatch.setattr(sources, "_speed", lambda url, **kw: speeds[url])
    assert sources.measure()["best"] == "china"
    assert sources.choice() == "auto" and sources.resolved() == "china"
    monkeypatch.setattr(sources, "_speed", lambda url, **kw: None if "aliyun" in url else 1e6)
    assert sources.measure()["best"] == "official"


def test_every_mirror_entry_has_what_setup_needs():
    t = sources.table()
    for k in ("official", "china"):
        assert t[k]["pypi"].startswith("https://")
        assert t[k]["torch"]["mode"] in ("index", "find-links")
        assert all(t[k]["torch"][v].startswith("https://") for v in ("cuda", "cpu", "xpu"))
