"""Domain registry for the TinyAssets engine.

The DomainRegistry is a simple lookup table for registered domains. This
allows domains to be discovered and loaded by name, either at startup or
dynamically at runtime.

The registry implements the DomainRegistry protocol from tinyassets.protocols.
It is intentionally minimal — domains can also be registered directly via
imports if static discovery is preferred.

Usage:
    from tinyassets.registry import default_registry
    from my_package.skill import MyDomain

    default_registry.register(MyDomain())
    domain = default_registry.get("fantasy_author")
"""

from __future__ import annotations

from typing import Any


class DomainRegistry:
    """Concrete implementation of the DomainRegistry protocol.

    Stores domains in a dict keyed by name. Thread-safe for read operations.
    Write operations (register) should happen at startup before concurrent
    access begins.
    """

    def __init__(self) -> None:
        """Initialize an empty registry."""
        self._domains: dict[str, Any] = {}

    def register(self, domain: Any) -> None:
        """Register a domain with the registry.

        Parameters
        ----------
        domain : Domain
            A domain object implementing the tinyassets.protocols.Domain protocol.
            Must have a config property with a 'name' key.

        Raises
        ------
        ValueError
            If the domain has no config or no name in config.
        """
        if not hasattr(domain, "config"):
            raise ValueError(f"Domain {domain} has no config property")

        config = domain.config
        if isinstance(config, property):
            config = config.fget(domain)

        if not isinstance(config, dict) or "name" not in config:
            raise ValueError(f"Domain config must be a dict with 'name' key, got {config}")

        name = config["name"]
        self._domains[name] = domain

    def get(self, name: str) -> Any | None:
        """Look up a domain by name.

        Parameters
        ----------
        name : str
            The domain name (from its config).

        Returns
        -------
        Domain or None
            The registered domain, or None if not found.
        """
        return self._domains.get(name)

    def list_domains(self) -> list[str]:
        """Return names of all registered domains.

        Returns
        -------
        list[str]
            Sorted list of domain names.
        """
        return sorted(self._domains.keys())


# Module-level default registry for convenience
default_registry = DomainRegistry()


__all__ = ["DomainRegistry", "default_registry"]
