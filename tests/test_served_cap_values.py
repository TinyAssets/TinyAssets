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
