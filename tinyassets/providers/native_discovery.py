"""Owned native metadata lifecycle; advisory only, never launch authority.

The caller checks the accepted member before and after this IO. This boundary
additionally pins the current deposited credential owner/generation, copies it
for one metadata process, and removes the copy on every exit. No SQL transaction
or assignment admission lock may be held by the caller across enumeration.
"""

import asyncio
import os
from dataclasses import dataclass, field, replace
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


def _custody(base, universe, owner, service, *, binding=None):
    if binding is not None:
        from tinyassets.external_agents.home import box_custody

        custody = box_custody(universe, owner, service, binding)
        return custody.reference if custody else None
    from tinyassets.storage_layout import ACTIVE_BOX_LAYOUT

    if ACTIVE_BOX_LAYOUT == "owner-box":
        raise PermissionError("native discovery requires an admitted box binding")
    with SQLiteProviderWorkAuthorityStore(base).connection() as conn:
        # IMMEDIATE so this write waits on the store's busy timeout. A deferred
        # BEGIN upgrades without waiting and loses to a concurrent adoption.
        conn.execute("BEGIN IMMEDIATE")
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
    binding: object | None = field(default=None, repr=False, compare=False)

    def assert_current(self):
        self.assert_fresh()
        if _custody(self.universe.parent, self.universe, self.owner_id,
                    self.custody.service, binding=self.binding) != self.custody:
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
        selected_root = (Path(os.path.abspath(universe)) if self.binding is not None
                         else Path(universe).resolve())
        if (self.provider != provider or self.owner_id != owner
                or self.universe != selected_root or self.custody != custody
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
    provider, *, universe_dir, owner_user_id, expected_custody, binding=None,
):
    """Discover through the registered executor, with exact current custody.

    Unknown enumeration is None, not a synthesized complete catalogue. The
    service identifier is executor registration metadata, not a release table.
    A supplied custody object grants nothing: compare it to the current store
    before copying credentials and again after the subprocess has finished.
    """
    snapshot = None
    try:
        from tinyassets.storage_layout import ACTIVE_BOX_LAYOUT

        universe = Path(os.path.abspath(universe_dir))
        if ACTIVE_BOX_LAYOUT == "owner-box":
            from tinyassets.agent_loop.served_chat import configured_box_provider
            from tinyassets.control_plane.binding import bind_session, current_binding

            binding = binding or current_binding()
            if binding is None:
                box_provider, limits = configured_box_provider()
                if box_provider is not None:
                    binding = bind_session(
                        provider=box_provider, limits=limits, universe_dir=universe,
                        owner=owner_user_id,
                        execution_id=f"catalogue:{owner_user_id}:{universe.name}:{provider.name}",
                    )
            if binding is None:
                raise PermissionError("native discovery requires an admitted box binding")
        else:
            binding = None
            universe = universe.resolve(strict=True)
        if (type(expected_custody) is not LLMCredentialCustodyReference
                or expected_custody.owner_user_id != owner_user_id
                or expected_custody.universe_id != universe.name
                or not provider.native_credential_service
                or provider.native_credential_service != expected_custody.service
                or _custody(universe.parent, universe, owner_user_id,
                            expected_custody.service, binding=binding) != expected_custody):
            raise PermissionError("native discovery custody mismatch")
        observed = datetime.now(timezone.utc)
        from tinyassets.external_agents.registry import PACKAGES

        if provider.name in PACKAGES:
            from tinyassets.control_plane.native_launch import launcher
            from tinyassets.external_agents.home import Custody, box_custody
            from tinyassets.external_agents.runner import run_catalogue

            custody = (box_custody(universe, owner_user_id,
                                   expected_custody.service, binding)
                       if binding is not None else
                       Custody(universe, owner_user_id, expected_custody))
            if custody is None:
                raise PermissionError("native discovery custody mismatch")

            def launch_catalogue(receipt_id):
                if binding is None:
                    from tinyassets.role_external_agent import CellLauncher

                    return CellLauncher(universe_dir=universe, owner_user_id=owner_user_id)
                return launcher(custody, binding=binding,
                                operation_id=f"catalogue:{receipt_id}")

            result = await run_catalogue(
                provider.name,
                launch_catalogue, custody,
            )
            return replace(result, binding=binding) if result is not None else None
        if binding is not None:
            raise PermissionError("native discovery executor must run in owner box")
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
            observed, completed, catalogue, binding,
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
    universe = Path(os.path.abspath(base_path)) / universe_id
    from tinyassets.storage_layout import ACTIVE_BOX_LAYOUT

    binding = None
    if ACTIVE_BOX_LAYOUT == "owner-box":
        from tinyassets.agent_loop.served_chat import configured_box_provider
        from tinyassets.control_plane.binding import bind_session, current_binding

        binding = current_binding()
        if binding is None:
            box_provider, limits = configured_box_provider()
            if box_provider is not None:
                binding = bind_session(
                    provider=box_provider, limits=limits, universe_dir=universe,
                    owner=owner_user_id,
                    execution_id=f"catalogue:{owner_user_id}:{universe_id}:{service}",
                )
    expected = _custody(universe.parent, universe, owner_user_id, service,
                        binding=binding)
    if expected is None:
        raise ProviderError("owned native model discovery unavailable")
    return await refresh_native_catalogue(
        executor, universe_dir=universe, owner_user_id=owner_user_id,
        expected_custody=expected, binding=binding,
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
