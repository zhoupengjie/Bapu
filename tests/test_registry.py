"""Model registry: license gate, SHA-256 checks, archives. Uses file:// URLs."""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest

from bapu.models.registry import LicenseNotAccepted, ModelNotReady, Registry, RegistryError


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def blobs(tmp_path: Path):
    src = tmp_path / "upstream"
    src.mkdir()
    weights = b"fake-weights" * 1000
    config = b"fake: config\n"
    (src / "w.ckpt").write_bytes(weights)
    (src / "w.yaml").write_bytes(config)
    return src, weights, config


def write_registry(tmp_path: Path, body: str) -> Registry:
    f = tmp_path / "registry.toml"
    f.write_text(body, encoding="utf-8")
    return Registry.load(f, cache_root=tmp_path / "cache")


def plain_registry(tmp_path: Path, src: Path, weights: bytes, config: bytes, bad_first: bool = False) -> Registry:
    missing = (src / "missing.ckpt").as_uri()
    urls = f'["{missing}", "{(src / "w.ckpt").as_uri()}"]' if bad_first else f'["{(src / "w.ckpt").as_uri()}"]'
    return write_registry(tmp_path, f"""
[[model]]
id = "m"
stage = "separate"
name = "Test model"
backend = "test"
dir = "models/test"
entry = "w.ckpt"
license = {{ name = "CC BY-NC 4.0", commercial = "no" }}

[[model.files]]
name = "w.ckpt"
size = {len(weights)}
sha256 = "{sha(weights)}"
urls = {urls}

[[model.files]]
name = "w.yaml"
sha256 = "{sha(config)}"
urls = ["{(src / "w.yaml").as_uri()}"]
""")


def test_pull_requires_license(tmp_path, blobs):
    reg = plain_registry(tmp_path, *blobs)
    spec = reg.get("m")
    assert reg.status(spec) == "missing"
    with pytest.raises(LicenseNotAccepted):
        reg.pull(spec)
    with pytest.raises(ModelNotReady):
        reg.ensure_ready(spec)


def test_pull_verifies_and_records_acceptance(tmp_path, blobs):
    reg = plain_registry(tmp_path, *blobs, bad_first=True)  # first URL fails, second works
    spec = reg.get("m")
    seen = []
    path = reg.pull(spec, accept_license=True, progress=lambda name, done, total: seen.append(name))
    assert path == tmp_path / "cache" / "models" / "test" / "w.ckpt"
    assert reg.status(spec) == "ready"
    assert reg.license_accepted(spec)
    assert "w.ckpt" in seen
    assert reg.ensure_ready(spec) == path


def test_tampered_file_is_corrupt(tmp_path, blobs):
    reg = plain_registry(tmp_path, *blobs)
    spec = reg.get("m")
    path = reg.pull(spec, accept_license=True)
    path.write_bytes(b"x" * path.stat().st_size)  # same size, different content
    assert reg.status(spec) == "corrupt"


def test_wrong_hash_upstream_is_rejected(tmp_path, blobs):
    src, weights, config = blobs
    reg = plain_registry(tmp_path, src, weights, config)
    (src / "w.ckpt").write_bytes(b"y" * len(weights))
    with pytest.raises(RegistryError):
        reg.pull(reg.get("m"), accept_license=True)
    assert not (tmp_path / "cache" / "models" / "test" / "w.ckpt").exists()


def test_remove(tmp_path, blobs):
    reg = plain_registry(tmp_path, *blobs)
    spec = reg.get("m")
    reg.pull(spec, accept_license=True)
    assert reg.remove(spec) > 0
    assert reg.status(spec) == "missing"


def archive_registry(tmp_path: Path, zip_path: Path, weights: bytes, config: bytes) -> Registry:
    return write_registry(tmp_path, f"""
[[model]]
id = "z"
stage = "transcribe"
name = "Zipped"
backend = "test"
dir = "models/z"
entry = "w.ckpt"
license = {{ name = "MIT", commercial = "yes" }}
archive = {{ url = "{zip_path.as_uri()}" }}

[[model.files]]
name = "w.ckpt"
sha256 = "{sha(weights)}"

[[model.files]]
name = "config.yaml"
sha256 = "{sha(config)}"
""")


def test_archive_files_found_by_name(tmp_path, blobs):
    _, weights, config = blobs
    zp = tmp_path / "pack.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("some_folder/w.ckpt", weights)
        zf.writestr("some_folder/config.yaml", config)
    reg = archive_registry(tmp_path, zp, weights, config)
    spec = reg.get("z")
    reg.pull(spec, accept_license=True)
    assert reg.status(spec) == "ready"
    assert not list((tmp_path / "cache" / "models" / ".downloads").glob("*.part"))


def test_archive_with_unsafe_path_is_refused(tmp_path, blobs):
    _, weights, config = blobs
    zp = tmp_path / "evil.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("../escape.txt", b"x")
        zf.writestr("w.ckpt", weights)
        zf.writestr("config.yaml", config)
    reg = archive_registry(tmp_path, zp, weights, config)
    with pytest.raises(RegistryError):
        reg.pull(reg.get("z"), accept_license=True)
    assert not (tmp_path / "cache" / "escape.txt").exists()


def test_builtin_model_needs_no_files(tmp_path):
    reg = write_registry(tmp_path, """
[[model]]
id = "algo"
stage = "tempo"
name = "Algorithm"
backend = "librosa"
license = { name = "ISC", commercial = "yes" }
""")
    spec = reg.get("algo")
    assert reg.status(spec) == "builtin"
    assert reg.ensure_ready(spec) is None


def test_shipped_registry_loads():
    reg = Registry.load()
    for spec in reg.models.values():
        assert spec.license.name
        for f in spec.files:
            assert len(f.sha256) == 64
    assert {"separate", "lead.karaoke", "lead.dereverb", "tempo", "transcribe"} <= {m.stage for m in reg.models.values()}
