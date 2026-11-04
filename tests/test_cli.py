from __future__ import annotations

import click
import pytest
from click.testing import CliRunner

from bapu import cli, jobs


def test_slug():
    assert jobs.slug("My Jealousy") == "my-jealousy"
    assert jobs.slug("  示例 歌曲_A ") == "示例-歌曲-a"
    assert jobs.slug("???") == "song"


def test_parse_sets_and_models():
    sets = cli._parse_sets(("tempo.hop_length=256", "separate.normalization_threshold=0.9"))
    assert sets == {"tempo": {"hop_length": 256}, "separate": {"normalization_threshold": 0.9}}
    models = cli._parse_models(("separate=bs-roformer-ep317", "lead.dereverb_model=none"))
    assert models == {"separate": {"model": "bs-roformer-ep317"}, "lead": {"dereverb_model": "none"}}
    merged = jobs.merge({"tempo": {"hop_length": 128, "sample_rate": 22050}}, sets)
    assert merged["tempo"] == {"hop_length": 256, "sample_rate": 22050}


def test_parse_rejects_bad_input():
    with pytest.raises(click.BadParameter):
        cli._parse_sets(("hop_length=256",))
    with pytest.raises(click.BadParameter):
        cli._parse_models(("tempo.sample_rate=x",))


def test_models_list_runs():
    result = CliRunner().invoke(cli.main, ["models", "list"])
    assert result.exit_code == 0, result.output
    assert "bs-roformer-ep317" in result.output
