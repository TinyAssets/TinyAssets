"""Owned native metadata lifecycle; advisory only, never launch authority.

The caller checks the accepted member before and after this IO. This boundary
additionally pins the current deposited credential owner/generation, copies it
for one metadata process, and removes the copy on every exit. No SQL transaction
or assignment admission lock may be held by the caller across enumeration.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tinyassets.credential_vault import (
    LLMCredentialCustodyReference,
    cleanup_llm_credential_snapshot,
    current_llm_subscription_custody,
    snapshot_llm_subscription_credential,
)
from tinyassets.exceptions import ProviderError
from tinyassets.providers.native_catalogue import NativeCatalogue
from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore


def _custody(base, universe, owner, service):
    with SQLiteProviderWorkAuthorityStore(base).connection() as conn:
        conn.execute("BEGIN")
        return current_llm_subscription_custody(
            conn, universe_dir=universe, owner_user_id=owner,
            universe_id=universe.name, service=service,
        )


@dataclass(frozen=True, slots=True)
class NativeDiscoverySnapshot:
    provider: str
    owner_id: str
    universe: Path
    custody: LLMCredentialCustodyReference = field(repr=False)
    observed_at: datetime
    completed_at: datetime
    catalogue: NativeCatalogue

    def assert_current(self):
        self.assert_fresh()
        if _custody(self.universe.parent, self.universe, self.owner_id,
                    self.custody.service) != self.custody:
            raise ProviderError("native model discovery source changed")

    def assert_fresh(self):
        now = datetime.now(timezone.utc)
        if now < self.completed_at or now - self.observed_at > timedelta(minutes=5):
            raise ProviderError("native model discovery expired or source changed")

    def select(self, *, provider, owner, universe, custody, model_id, access, effort=""):
        from tinyassets.providers.native_model_selection import (
            NativeSelection,
            validate_effort_level,
        )

        self.assert_fresh()
        # `hidden` is checked HERE too, not only where the picker is built.
        # Codex found admission matching on id and modality alone, so an id the
        # executor had withdrawn could still launch if it reached selection by
        # another route. Hiding a choice and refusing to run it are two
        # different guarantees and both are wanted.
        matched = next(
            (model for model in self.catalogue.models
             if model.model_id == model_id and "text" in model.input_modalities
             and not model.hidden),
            None,
        )
        if (self.provider != provider or self.owner_id != owner
                or self.universe != Path(universe).resolve() or self.custody != custody
                or matched is None):
            raise PermissionError("native catalogue does not match current model authority")
        # The ONLY place a level is admitted, because it is the only place the
        # advertised set is in hand. A level this model did not advertise is
        # refused rather than dropped: silently running at the executor's
        # default would report a setting the turn did not actually use.
        if validate_effort_level(effort) and effort not in matched.effort_levels:
            raise PermissionError("effort level is outside this model's advertised levels")
        selected = NativeSelection(
            provider, model_id, self.catalogue.default_model_id or "", "executor_enumerated",
            self.observed_at.isoformat(), self.completed_at.isoformat(), custody.reference_digest,
            effort,
        )
        selected.assert_access(access, custody.reference_digest)
        return selected


async def refresh_native_catalogue(
    provider, *, universe_dir, owner_user_id, expected_custody,
):
    """Discover through the registered executor, with exact current custody.

    Unknown enumeration is None, not a synthesized complete catalogue. The
    service identifier is executor registration metadata, not a release table.
    A supplied custody object grants nothing: compare it to the current store
    before copying credentials and again after the subprocess has finished.
    """
    snapshot = None
    try:
        universe = Path(universe_dir).resolve(strict=True)
        if (type(expected_custody) is not LLMCredentialCustodyReference
                or expected_custody.owner_user_id != owner_user_id
                or expected_custody.universe_id != universe.name
                or not provider.native_credential_service
                or provider.native_credential_service != expected_custody.service
                or _custody(universe.parent, universe, owner_user_id,
                            expected_custody.service) != expected_custody):
            raise PermissionError("native discovery custody mismatch")
        observed = datetime.now(timezone.utc)
        snapshot = snapshot_llm_subscription_credential(
            universe_dir=universe, custody=expected_custody,
        )
        async with asyncio.timeout(30):
            catalogue = await provider.enumerate_models(
                universe_dir=universe, credential_snapshot_dir=snapshot.directory,
            )
        if catalogue is None:
            return None
        if type(catalogue) is not NativeCatalogue:
            raise ValueError("executor returned invalid native metadata")
        completed = datetime.now(timezone.utc)
        if not observed <= catalogue.observed_at <= completed:
            raise ValueError("executor returned cached or future native metadata")
        result = NativeDiscoverySnapshot(
            provider.name, owner_user_id, universe, expected_custody,
            observed, completed, catalogue,
        )
        result.assert_current()
        return result
    except (OSError, ValueError, PermissionError, ProviderError):
        raise ProviderError("owned native model discovery unavailable") from None
    finally:
        cleanup_llm_credential_snapshot(snapshot)


async def discover_native_models(*, base_path, owner_user_id, universe_id, provider):
    """Private helper after member authorization, outside its locks/transactions."""
    from tinyassets.providers.call import get_provider_router

    router = get_provider_router()
    executor = None if router is None else router._providers.get(provider)
    service = getattr(executor, "native_credential_service", None)
    if not service:
        return None
    universe = Path(base_path).resolve() / universe_id
    expected = _custody(universe.parent, universe, owner_user_id, service)
    if expected is None:
        raise ProviderError("owned native model discovery unavailable")
    return await refresh_native_catalogue(
        executor, universe_dir=universe, owner_user_id=owner_user_id, expected_custody=expected,
    )


def discover_native_models_sync(**kwargs):
    # Sync callers may already be inside an event loop (workflow bridges).
    # The bounded worker owns and closes its own loop; no host-global loop.
    from concurrent.futures import ThreadPoolExecutor

    def run():
        return asyncio.run(discover_native_models(**kwargs))

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return run()
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(run).result()
