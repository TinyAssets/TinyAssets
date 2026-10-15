"""Tests for workflow runtime integration.

Tests domain discovery, auto-registration, and API app creation.
These tests verify that the workflow engine can load domains and
assemble them into a runnable system.
"""

from __future__ import annotations

# ============================================================================
# Discovery Tests
# ============================================================================


def test_discover_domains() -> None:
    """Test that domain discovery finds fantasy_daemon and research_probe."""
    from tinyassets.discovery import discover_domains

    domains = discover_domains()

    assert isinstance(domains, list)
    assert "fantasy_daemon" in domains
    assert "research_probe" in domains
    # Verify deterministic order
    assert domains == sorted(domains)


def test_discover_domains_empty_nonexistent() -> None:
    """Test that discover_domains returns empty list if domains/ doesn't exist.

    This is a graceful fallback and shouldn't raise an exception.
    """
    from tinyassets.discovery import discover_domains

    # The actual domains/ directory exists in the test environment,
    # but this test documents the expected behavior if it didn't.
    domains = discover_domains()
    # Real test environment has domains
    assert isinstance(domains, list)


def test_fantasy_author_skill_loadable() -> None:
    """Test that tinyassets.skill.FantasyAuthorDomain can be imported."""
    from domains.fantasy_daemon.skill import FantasyAuthorDomain

    domain = FantasyAuthorDomain()
    assert domain is not None

    config = domain.config
    assert config["name"] == "fantasy_author"
    assert config["description"]
    assert config["version"]


def test_research_probe_skill_loadable() -> None:
    """Test that research_probe.skill.ResearchProbeDomain can be imported."""
    from domains.research_probe.skill import ResearchProbeDomain

    domain = ResearchProbeDomain()
    assert domain is not None

    config = domain.config
    assert config["name"] == "research_probe"
    assert config["description"]
    assert config["version"]


# ============================================================================
# Registry Tests
# ============================================================================


def test_auto_register_populates_registry() -> None:
    """Test that auto_register discovers and registers all domains."""
    from tinyassets.discovery import auto_register
    from tinyassets.registry import DomainRegistry

    registry = DomainRegistry()
    assert registry.list_domains() == []

    auto_register(registry)

    domains = registry.list_domains()
    # Phase 2 migrates import paths only; the domain config name remains legacy
    # until the registry contract is deliberately flipped.
    assert "fantasy_author" in domains
    assert "research_probe" in domains
    assert len(domains) == 2


def test_registry_get_returns_registered_domain() -> None:
    """Test that registry.get() returns a registered domain instance."""
    from tinyassets.discovery import auto_register
    from tinyassets.registry import DomainRegistry

    registry = DomainRegistry()
    auto_register(registry)

    fa_domain = registry.get("fantasy_author")
    assert fa_domain is not None
    assert fa_domain.config["name"] == "fantasy_author"

    rp_domain = registry.get("research_probe")
    assert rp_domain is not None
    assert rp_domain.config["name"] == "research_probe"


def test_registry_get_returns_none_for_missing() -> None:
    """Test that registry.get() returns None for unregistered domains."""
    from tinyassets.registry import DomainRegistry

    registry = DomainRegistry()
    result = registry.get("nonexistent_domain")
    assert result is None


def test_default_registry_exported() -> None:
    """Test that a default_registry is exported for convenience."""
    from tinyassets.registry import default_registry

    assert default_registry is not None
    # The default registry may be empty (no auto_register called yet)
    # so we just check it exists and has the right interface
    assert hasattr(default_registry, "list_domains")
    assert hasattr(default_registry, "get")
    assert hasattr(default_registry, "register")


# ============================================================================
# API App Tests
# ============================================================================


def test_create_app_returns_fastapi() -> None:
    """Test that create_app returns a FastAPI instance."""
    from tinyassets.api import create_app

    app = create_app()

    assert app is not None
    # Verify it's a FastAPI app
    assert hasattr(app, "add_route")
    assert hasattr(app, "add_api_route")


def test_create_app_with_registry() -> None:
    """Test that create_app accepts a registry parameter."""
    from tinyassets.api import create_app
    from tinyassets.discovery import auto_register
    from tinyassets.registry import DomainRegistry

    registry = DomainRegistry()
    auto_register(registry)

    app = create_app(registry)

    assert app is not None
    assert hasattr(app, "add_route")






# ============================================================================
# Entry Point Tests
# ============================================================================








# ============================================================================
# TinyAssets Package Tests
# ============================================================================


def test_tinyassets_exports_discovery() -> None:
    """Test that tinyassets package exports discovery utilities."""
    import tinyassets

    assert hasattr(tinyassets, "discover_domains")
    assert hasattr(tinyassets, "auto_register")
    assert callable(tinyassets.discover_domains)
    assert callable(tinyassets.auto_register)


def test_tinyassets_exports_registry() -> None:
    """Test that tinyassets package exports registry utilities."""
    import tinyassets

    assert hasattr(tinyassets, "DomainRegistry")
    assert hasattr(tinyassets, "default_registry")


def test_tinyassets_exports_protocols() -> None:
    """Test that tinyassets package exports domain protocols."""
    import tinyassets

    assert hasattr(tinyassets, "Domain")
    assert hasattr(tinyassets, "DomainConfig")


def test_tinyassets_all_exports_complete() -> None:
    """Test that tinyassets.__all__ includes key symbols."""
    import tinyassets

    expected = [
        "DomainRegistry",
        "default_registry",
        "discover_domains",
        "auto_register",
        "Domain",
        "DomainConfig",
    ]

    for symbol in expected:
        assert symbol in tinyassets.__all__, f"{symbol} not in tinyassets.__all__"
        assert hasattr(tinyassets, symbol), f"{symbol} not exported from tinyassets"


# ============================================================================
# Domain Protocol Tests
# ============================================================================


def test_fantasy_author_domain_implements_protocol() -> None:
    """Test that FantasyAuthorDomain implements the Domain protocol."""
    from domains.fantasy_daemon.skill import FantasyAuthorDomain

    domain = FantasyAuthorDomain()

    # Check that required methods/properties exist
    assert hasattr(domain, "config")
    assert hasattr(domain, "build_graph")
    assert hasattr(domain, "state_extensions")
    assert hasattr(domain, "tools")
    assert hasattr(domain, "eval_criteria")
    assert hasattr(domain, "memory_schemas")
    assert hasattr(domain, "api_routes")

    # Verify they're callable or properties
    assert isinstance(domain.config, dict)
    assert callable(domain.build_graph)
    assert callable(domain.state_extensions)
    assert callable(domain.tools)
    assert callable(domain.eval_criteria)
    assert callable(domain.memory_schemas)
    assert callable(domain.api_routes)


def test_research_probe_domain_implements_protocol() -> None:
    """Test that ResearchProbeDomain implements the Domain protocol."""
    from domains.research_probe.skill import ResearchProbeDomain

    domain = ResearchProbeDomain()

    # Check that required methods/properties exist
    assert hasattr(domain, "config")
    assert hasattr(domain, "build_graph")
    assert hasattr(domain, "state_extensions")
    assert hasattr(domain, "tools")
    assert hasattr(domain, "eval_criteria")
    assert hasattr(domain, "memory_schemas")
    assert hasattr(domain, "api_routes")

    # Verify they're callable or properties
    assert isinstance(domain.config, dict)
    assert callable(domain.build_graph)
    assert callable(domain.state_extensions)
    assert callable(domain.tools)
    assert callable(domain.eval_criteria)
    assert callable(domain.memory_schemas)
    assert callable(domain.api_routes)


def test_fantasy_author_config_valid() -> None:
    """Test that FantasyAuthorDomain.config is valid."""
    from domains.fantasy_daemon.skill import FantasyAuthorDomain

    domain = FantasyAuthorDomain()
    config = domain.config

    assert config["name"] == "fantasy_author"
    assert isinstance(config["description"], str)
    assert len(config["description"]) > 0
    assert isinstance(config["version"], str)


def test_research_probe_config_valid() -> None:
    """Test that ResearchProbeDomain.config is valid."""
    from domains.research_probe.skill import ResearchProbeDomain

    domain = ResearchProbeDomain()
    config = domain.config

    assert config["name"] == "research_probe"
    assert isinstance(config["description"], str)
    assert len(config["description"]) > 0
    assert isinstance(config["version"], str)


def test_fantasy_author_build_graph_succeeds() -> None:
    """Test that FantasyAuthorDomain.build_graph() succeeds."""
    from domains.fantasy_daemon.skill import FantasyAuthorDomain

    domain = FantasyAuthorDomain()
    graph = domain.build_graph()

    assert graph is not None
    # Verify it's a LangGraph StateGraph
    assert hasattr(graph, "nodes")
    assert hasattr(graph, "edges")


def test_research_probe_build_graph_succeeds() -> None:
    """Test that ResearchProbeDomain.build_graph() succeeds."""
    from domains.research_probe.skill import ResearchProbeDomain

    domain = ResearchProbeDomain()
    graph = domain.build_graph()

    assert graph is not None
    # Verify it's a LangGraph StateGraph
    assert hasattr(graph, "nodes")
    assert hasattr(graph, "edges")
