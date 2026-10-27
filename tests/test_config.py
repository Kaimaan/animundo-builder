"""Tests for builder settings."""

from __future__ import annotations

import pytest

from builder.config import BASETEN_OPEN_MODEL, DEFAULT_MODEL, Config, load
from builder.prices import Prices


def test_defaults() -> None:
    config = Config()
    assert config.model == DEFAULT_MODEL
    assert config.open_model_host == "meta"
    assert config.daily_budget_usd == 20.0
    assert config.job_budget_usd == 8.0
    assert config.total_budget_usd == 80.0
    assert config.ledger_path == "spend-ledger.json"
    assert config.max_new_species == 3
    assert config.max_new_plants == 3
    assert config.prices.input_per_million == 1.25


def test_max_new_species_from_env(monkeypatch) -> None:
    monkeypatch.setenv("BUILDER_MAX_NEW_SPECIES", "5")
    assert load().max_new_species == 5


def test_max_new_plants_from_env(monkeypatch) -> None:
    monkeypatch.setenv("BUILDER_MAX_NEW_PLANTS", "1")
    assert load().max_new_plants == 1


def test_load_reads_environment(monkeypatch) -> None:
    monkeypatch.setenv("BUILDER_MODEL", "muse-spark-1.2")
    monkeypatch.setenv("BUILDER_JOB_BUDGET_USD", "5")
    monkeypatch.setenv("BUILDER_TOTAL_BUDGET_USD", "50")
    monkeypatch.setenv("BUILDER_LEDGER_PATH", "/tmp/ledgers/test.json")
    monkeypatch.setenv("MODEL_API_KEY", "from-env")
    config = load()
    assert config.model == "muse-spark-1.2"
    assert config.job_budget_usd == 5
    assert config.total_budget_usd == 50
    assert config.ledger_path == "/tmp/ledgers/test.json"
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
    assert Config(open_model_host="baseten").open_model_id() == BASETEN_OPEN_MODEL
    assert Config(open_model_host="meta").open_model_id() == DEFAULT_MODEL
    custom = Config(open_model_host="fireworks", open_model="my-glimmer-tune")
    assert custom.open_model_id() == "my-glimmer-tune"
    with pytest.raises(ValueError, match="BUILDER_OPEN_MODEL"):
        Config(open_model_host="fireworks").open_model_id()


def test_open_api_keys() -> None:
    assert Config(open_model_host="meta", model_api_key="m").open_api_key() == "m"
    assert Config(open_model_host="baseten", baseten_api_key="b").open_api_key() == "b"
    assert (
        Config(open_model_host="fireworks", fireworks_api_key="f").open_api_key() == "f"
    )
    assert Config(open_model_host="modal-glimmer").open_api_key() is None


def test_open_settings_from_env(monkeypatch) -> None:
    monkeypatch.setenv("BUILDER_OPEN_MODEL_HOST", "baseten")
    monkeypatch.setenv("BUILDER_OPEN_BASE_URL", "https://example.org/v1")
    monkeypatch.setenv("FIREWORKS_API_KEY", "fw-key")
    config = load()
    assert config.open_model_host == "baseten"
    assert config.open_base_url == "https://example.org/v1"
    assert config.fireworks_api_key == "fw-key"


def test_price_overrides() -> None:
    prices = Prices(input_per_million=9.0)
    assert Config(prices=prices).prices.input_per_million == 9.0
