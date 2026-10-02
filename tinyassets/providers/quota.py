"""Cooldown tracking for all providers.

Manages sticky cooldowns: a provider marked unavailable for a fixed duration.
Rate limits are the sources' own, reported back as cooldowns.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

# Cooldown durations in seconds, applied by the router on specific errors.
# Kept short: Claude's rate limits are ~60s, Ollama is always local.
# A 30-min cooldown on a single failure was causing Claude to be skipped
# for the entire session, falling through to Ollama for all writes.
COOLDOWN_UNAVAILABLE = 120    # exit-code-1 / rate-limit => 2 min
COOLDOWN_TIMEOUT = 120        # hung subprocess => 2 min
COOLDOWN_OTHER = 30           # generic error => 30 sec

#: Ceiling on ANY cooldown, including one a source asked for by ``Retry-After``.
#: That header is remote input: unbounded, ``Retry-After: 99999999`` takes a
#: source out for ~3.2 years, which is a source-supplied denial of its owner's
#: own universe. One day is longer than any real rate-limit window and short
#: enough that a buggy or hostile header costs at most a day.
MAX_COOLDOWN_S = 86_400


class QuotaTracker:
    """Tracks cooldowns for every provider."""

    def __init__(self) -> None:
        # Absolute monotonic time when cooldown expires (0 = not in cooldown).
        self._cooldowns: dict[str, float] = {}
        self._daily_details: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def available(self, provider: str) -> bool:
        """Return True if *provider* is not in cooldown."""
        return not self._in_cooldown(provider)

    def cooldown(self, provider: str, seconds: int, *, daily_detail: str = "") -> None:
        """Mark *provider* as unavailable for *seconds* (sticky)."""
        expiry = time.monotonic() + seconds
        if self._cooldowns.get(provider, 0) > expiry and provider in self._daily_details:
            return
        self._cooldowns[provider] = expiry
        if daily_detail:
            self._daily_details[provider] = daily_detail
        else:
            self._daily_details.pop(provider, None)
        logger.info("Provider %s in cooldown for %ds (until %.1f)", provider, seconds, expiry)

    def daily_detail(self, provider: str) -> str:
        return self._daily_details.get(provider, "") if self._in_cooldown(provider) else ""

    def cooldown_remaining(self, provider: str) -> int:
        """Return seconds of cooldown remaining for *provider*, or 0."""
        expiry = self._cooldowns.get(provider, 0.0)
        if expiry == 0.0:
            return 0
        remaining = expiry - time.monotonic()
        return max(0, int(remaining))

    def cooldown_remaining_dict(self, providers: list[str]) -> dict[str, int]:
        """Return {provider: seconds_remaining} for every provider in *providers*.

        Providers not in cooldown appear with value 0.
        """
        return {p: self.cooldown_remaining(p) for p in providers}

    def all_api_providers_in_cooldown(
        self,
        chain: list[str],
        local_providers: set[str] | None = None,
    ) -> bool:
        """Return True when every non-local provider in *chain* is in cooldown.

        *local_providers* defaults to ``{"ollama-local"}``. When this
        returns True, the router is funnelling all traffic to local
        providers — the chain-drain condition that preceded the 2026-04-23
        P0 revert-loop.
        """
        if local_providers is None:
            local_providers = {"ollama-local"}
        api_providers = [p for p in chain if p not in local_providers]
        if not api_providers:
            return False
        return all(self._in_cooldown(p) for p in api_providers)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _in_cooldown(self, provider: str) -> bool:
        expiry = self._cooldowns.get(provider, 0.0)
        if expiry == 0.0:
            return False
        if time.monotonic() >= expiry:
            # Cooldown expired -- clear it.
            self._cooldowns.pop(provider, None)
            self._daily_details.pop(provider, None)
            logger.info("Provider %s cooldown expired", provider)
            return False
        return True

