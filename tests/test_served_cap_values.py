"""Invalid YAML cap values never become infinite or one-second runtime caps."""

from types import SimpleNamespace

import pytest

from tinyassets.config import load_universe_config
from tinyassets.universe_intelligence import (
    UNBOUNDED_TURN_SECONDS,
    _sandboxed_config,
    served_absolute_cap_s,
)


@pytest.mark.parametrize(
    "yaml_value", ["null", "0", "-1", "true", "false", ".inf", "-.inf", ".nan", "later", "[]", "{}"]
)
def test_invalid_yaml_caps_keep_the_existing_default(tmp_path, yaml_value):
    (tmp_path / "config.yaml").write_text(f"absolute_cap_s: {yaml_value}\ntemperature: 0.25\n")
    cfg = load_universe_config(tmp_path)
    assert cfg.temperature == 0.25
    assert served_absolute_cap_s(cfg) is None
    assert (
        _sandboxed_config(SimpleNamespace(config=cfg), granted=True).absolute_cap_s
        == UNBOUNDED_TURN_SECONDS
    )


@pytest.mark.parametrize("yaml_value,expected", [("120", 120), ("0.5", 0.5), ('"43"', 43)])
def test_finite_yaml_caps_reach_the_stream_profile(tmp_path, yaml_value, expected):
    (tmp_path / "config.yaml").write_text(f"absolute_cap_s: {yaml_value}\n")
    cfg = load_universe_config(tmp_path)
    model = _sandboxed_config(SimpleNamespace(config=cfg), granted=True)
    assert served_absolute_cap_s(cfg) == expected
    assert model.stream_timeout_profile().absolute_cap_s == expected


@pytest.mark.parametrize("yaml_value", ["1e10", "315360000"])
def test_huge_finite_caps_cannot_overflow_executor_waits(tmp_path, yaml_value):
    from tinyassets.api.status import _working_turn_max_age_s

    (tmp_path / "config.yaml").write_text(f"absolute_cap_s: {yaml_value}\n")
    cfg = load_universe_config(tmp_path)
    model = _sandboxed_config(SimpleNamespace(config=cfg), granted=True)
    assert served_absolute_cap_s(cfg) == UNBOUNDED_TURN_SECONDS
    assert model.stream_timeout_profile().absolute_cap_s == UNBOUNDED_TURN_SECONDS
    assert _working_turn_max_age_s(tmp_path) == UNBOUNDED_TURN_SECONDS + 30


def test_config_writer_round_trips_cap_and_unrelated_preferences(tmp_path):
    from tinyassets.config import write_universe_config_fields

    write_universe_config_fields(tmp_path, absolute_cap_s=120.5, temperature=0.25)
    assert served_absolute_cap_s(load_universe_config(tmp_path)) == 120.5
    write_universe_config_fields(tmp_path, temperature=0.75)
    cfg = load_universe_config(tmp_path)
    assert served_absolute_cap_s(cfg) == 120.5 and cfg.temperature == 0.75
    write_universe_config_fields(tmp_path, absolute_cap_s=None)
    assert served_absolute_cap_s(load_universe_config(tmp_path)) is None
