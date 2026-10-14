"""Tests for per-universe config.yaml reader."""

from __future__ import annotations

from tinyassets.config import UniverseConfig, load_universe_config


class TestUniverseConfigDefaults:
    """Default config should have sensible values."""

    def test_default_temperature(self):
        cfg = UniverseConfig()
        assert cfg.temperature == 0.7

    def test_default_timeout(self):
        cfg = UniverseConfig()
        assert cfg.timeout == 300

    def test_default_scenes_target(self):
        cfg = UniverseConfig()
        assert cfg.scenes_target == 3

    def test_default_chapters_target(self):
        cfg = UniverseConfig()
        assert cfg.chapters_target == 1

    def test_default_revision_limit(self):
        cfg = UniverseConfig()
        assert cfg.revision_limit == 1

    def test_default_extra_empty(self):
        cfg = UniverseConfig()
        assert cfg.extra == {}


class TestLoadUniverseConfig:
    """Test loading config.yaml from disk."""

    def test_missing_file_returns_defaults(self, tmp_path):
        cfg = load_universe_config(tmp_path)
        assert cfg.temperature == 0.7
        assert cfg.scenes_target == 3

    def test_reads_yaml_file(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "temperature: 0.9\n"
            "timeout: 120\n"
            "scenes_target: 5\n"
            "chapters_target: 3\n",
            encoding="utf-8",
        )

        cfg = load_universe_config(tmp_path)
        assert cfg.temperature == 0.9
        assert cfg.timeout == 120
        assert cfg.scenes_target == 5
        assert cfg.chapters_target == 3

    def test_partial_override_keeps_defaults(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "temperature: 0.5\n", encoding="utf-8",
        )

        cfg = load_universe_config(tmp_path)
        assert cfg.temperature == 0.5
        assert cfg.timeout == 300  # default
        assert cfg.scenes_target == 3  # default

    def test_unknown_keys_go_to_extra(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "temperature: 0.8\n"
            "custom_setting: true\n"
            "mood: dark\n",
            encoding="utf-8",
        )

        cfg = load_universe_config(tmp_path)
        assert cfg.temperature == 0.8
        assert cfg.extra["custom_setting"] is True
        assert cfg.extra["mood"] == "dark"

    def test_invalid_yaml_returns_defaults(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "{{{{not valid yaml!!!!", encoding="utf-8",
        )

        cfg = load_universe_config(tmp_path)
        assert cfg.temperature == 0.7  # defaults

    def test_non_mapping_yaml_returns_defaults(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "- item1\n- item2\n", encoding="utf-8",
        )

        cfg = load_universe_config(tmp_path)
        assert cfg.temperature == 0.7

    def test_provider_preferences(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "preferred_writer: claude-code\n"
            "preferred_judge: gemini-free\n",
            encoding="utf-8",
        )

        cfg = load_universe_config(tmp_path)
        assert cfg.preferred_writer == "claude-code"
        assert cfg.preferred_judge == "gemini-free"

    def test_word_count_bounds(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "min_words_per_scene: 500\n"
            "max_words_per_scene: 5000\n",
            encoding="utf-8",
        )

        cfg = load_universe_config(tmp_path)
        assert cfg.min_words_per_scene == 500
        assert cfg.max_words_per_scene == 5000

    def test_debate_and_revision(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "debate_enabled: false\n"
            "revision_limit: 0\n",
            encoding="utf-8",
        )

        cfg = load_universe_config(tmp_path)
        assert cfg.debate_enabled is False
        assert cfg.revision_limit == 0

    def test_empty_file_returns_defaults(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("", encoding="utf-8")

        cfg = load_universe_config(tmp_path)
        assert cfg.temperature == 0.7

    def test_string_path(self, tmp_path):
        """Should accept string paths, not just Path objects."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "temperature: 0.3\n", encoding="utf-8",
        )

        cfg = load_universe_config(str(tmp_path))
        assert cfg.temperature == 0.3


class TestRuntimeConfigIntegration:
    """Test that runtime.universe_config is properly typed."""

    def test_runtime_has_default_config(self):
        from tinyassets import runtime_singletons as runtime

        assert isinstance(runtime.universe_config, UniverseConfig)

    def test_reset_restores_default_config(self):
        from tinyassets import runtime_singletons as runtime

        runtime.universe_config = UniverseConfig(temperature=0.1)
        runtime.reset()
        assert runtime.universe_config.temperature == 0.7


class TestServedAbsoluteCap:
    """The file owners edit must feed the same runtime and activity cap."""

    def test_yaml_cap_reaches_granted_runtime_and_activity(self, tmp_path):
        from types import SimpleNamespace

        from tinyassets.api.status import _working_turn_max_age_s
        from tinyassets.universe_intelligence import _sandboxed_config, served_absolute_cap_s

        (tmp_path / "config.yaml").write_text("absolute_cap_s: 120.5\ntemperature: 0.25\n")
        cfg = load_universe_config(tmp_path)
        assert cfg.absolute_cap_s == 120.5 and "absolute_cap_s" not in cfg.extra
        assert cfg.temperature == 0.25
        ctx = SimpleNamespace(config=cfg)
        assert served_absolute_cap_s(cfg) == 120.5
        model = _sandboxed_config(ctx, granted=True)
        assert model.absolute_cap_s == 120.5
        assert model.stream_timeout_profile().absolute_cap_s == 120.5
        assert _sandboxed_config(ctx, granted=False).absolute_cap_s is None
        assert _working_turn_max_age_s(tmp_path) == 150.5

    def test_missing_cap_preserves_unbounded_served_default(self, tmp_path):
        from types import SimpleNamespace

        from tinyassets.universe_intelligence import (
            UNBOUNDED_TURN_SECONDS,
            _sandboxed_config,
            served_absolute_cap_s,
        )

        cfg = load_universe_config(tmp_path)
        assert cfg.absolute_cap_s is None and served_absolute_cap_s(cfg) is None
        model = _sandboxed_config(SimpleNamespace(config=cfg), granted=True)
        assert model.absolute_cap_s == UNBOUNDED_TURN_SECONDS
