"""Tests for builder settings."""

from __future__ import annotations

import pytest

from builder.config import BASENTEN_OPEN_MODEL, DEFAULT_MODEL, Config, Prices, load


def test_defaults() -> None:
    config = Config()
    assert config.model == DEFAULT_MODEL
    assert config.open_model_host == "meta"
    assert config.daily_budget_usd == 20.0
    assert config.job_budget_usd == 8.0
    assert config.prices.input_per_million == 1.25


def test_load_reads_environment(monkeypatch) -> None:
    monkeypatch.setenv("BUILDER_MODEL", "muse-spark-1.2")
    monkeypatch.setenv("BUILDER_JOB_BUDGET_USD", "5")
    monkeypatch.setenv("MODEL_API_KEY", "from-env")
    config = load()
    assert config.model == "muse-spark-1.2"
    assert config.job_budget_usd == 5
    assert config.model_api_key == "from-env"


def test_load_reads_toml(tmp_path) -> None:
    toml = tmp_path / "builder.toml"
    toml.write_text('model = "muse-spark-1.2"\njob_budget_usd = 3.0\n')
    config = load(tmp_path)
    assert config.model == "muse-spark-1.2"
    assert config.job_budget_usd == 3.0


def test_environment_wins_over_toml(tmp_path, monkeypatch) -> None:
    (tmp_path / "builder.toml").write_text('model = "muse-spark-1.2"\n')
    monkeypatch.setenv("BUILDER_MODEL", "muse-spark-1.3")
    assert load(tmp_path).model == "muse-spark-1.3"


def test_missing_file_is_fine(tmp_path) -> None:
    assert load(tmp_path / "nothing-here").model == DEFAULT_MODEL


def test_bad_open_host_rejected(monkeypatch) -> None:
    monkeypatch.setenv("BUILDER_OPEN_MODEL_HOST", "somewhere-else")
    with pytest.raises(ValueError, match="open_model_host"):
        load()


def test_open_model_hosts() -> None:
    assert Config(open_model_host="baseten").open_model() == BASENTEN_OPEN_MODEL
    assert Config(open_model_host="meta").open_model() == DEFAULT_MODEL
    with pytest.raises(ValueError, match="Modal"):
        Config(open_model_host="modal").open_model()


def test_price_overrides() -> None:
    prices = Prices(input_per_million=9.0)
    assert Config(prices=prices).prices.input_per_million == 9.0
