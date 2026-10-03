"""Server-owned provider authority for authenticated foreground Branch runs."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator

from tinyassets.execution_subject import ExecutionSubject, ExecutionSubjectKind
from tinyassets.platform_runtime_provenance import (
    admitted_cloud_executor_class as _admitted_cloud_class,
)
from tinyassets.platform_runtime_provenance import (
    platform_not_cloud_message,
    resolve_process_cloud_admission,
)
from tinyassets.provider_work_authority import (
    ProviderInvocationCarrier,
    ProviderInvocationSelection,
    ProviderUniverseWorkAuthority,
    ProviderUniverseWorkReceipt,
    ProviderUniverseWorkRoot,
    ProviderWorkAuthorityWriteOutcome,
    ProviderWorkBindingFence,
    ProviderWorkBindingRoot,
    ProviderWorkBindingSeed,
    ProviderWorkBindingService,
    ProviderWorkExecutionClaim,
    ProviderWorkReceiptState,
    provider_work_receipt_id,
)
from tinyassets.providers.base import ModelConfig
from tinyassets.providers.owner_binding import (
    AUTHORITY_HELD_DETAIL as _AUTHORITY_HELD_DETAIL,
)
from tinyassets.providers.owner_binding import (
    CONNECT_PROVIDER_MESSAGE as _CONNECT_PROVIDER_MESSAGE,
)

logger = logging.getLogger(__name__)

RUN_GRAPH_OPERATION = "run_graph"
_SUPPORTED_ROLES = frozenset({"writer", "judge"})
#: Imported, not re-declared: `providers.owner_binding` owns both sentences.
_HELD = _CONNECT_PROVIDER_MESSAGE
_HELD_DETAIL = _AUTHORITY_HELD_DETAIL


def _held_authority_error(cause: BaseException | None = None):
    """The held class, saying what is actually missing.

    Three `except Exception` handlers on this lane raised `_HELD` for every
    refusal they caught, so "Connect your provider" was the only thing a held
    run ever said -- including to owners who had. See `_HELD_DETAIL` for the
    live case. A cause with words of its own keeps them, scrubbed and clipped
    through the router's existing `redacted_failure_detail`: a wrapped cause can
    be an OS or HTTP error, so its text is never assumed credential-free.

    `NoServingProvider` is the one cause that maps back to `_HELD`: it IS "no
    provider is connected", and its own words would read as a second, weaker
    version of the same instruction.
    """
    from tinyassets.exceptions import ProviderAuthorityHeldError
    from tinyassets.provider_serving_binding import NoServingProvider
    from tinyassets.providers.diagnostics import redacted_failure_detail

    if cause is None or isinstance(cause, NoServingProvider):
        return ProviderAuthorityHeldError(_HELD)
    detail = redacted_failure_detail(str(cause).strip())
    if not detail:
        return ProviderAuthorityHeldError(_HELD)
    return ProviderAuthorityHeldError(_HELD_DETAIL + detail)


def _content_digest(payload: object) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _prompt_nodes(snapshot: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    raw = snapshot.get("node_defs", [])
    nodes = raw.values() if isinstance(raw, dict) else raw
    if not isinstance(nodes, (list, tuple)) and not hasattr(nodes, "__iter__"):
        raise PermissionError("immutable Branch node definitions are invalid")
    return tuple(
        node
        for node in nodes
        if isinstance(node, dict) and bool(str(node.get("prompt_template") or "").strip())
    )


def _review_allowance(snapshot: dict[str, Any]) -> int:
    """At most two text attempts per declared effect, within the owner's cap."""
    from tinyassets.agent_review import REVIEW_MAX_ATTEMPTS
    from tinyassets.effectors.authenticated_external_call import (
        EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL,
    )

    raw = snapshot.get("node_defs", [])
    nodes = raw.values() if isinstance(raw, dict) else raw
    return REVIEW_MAX_ATTEMPTS * sum(
        (node.get("effects") or []).count(EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL)
        for node in nodes if isinstance(node, dict)
    )


def _declared_policy_providers(policy: dict[str, Any] | None) -> set[str]:
    providers: set[str] = set()
    if not policy:
        return providers
    for value in policy.values():
        entries = value if isinstance(value, list) else [value]
        for entry in entries:
            use = entry.get("use") if isinstance(entry, dict) else None
            candidate = use if isinstance(use, dict) else entry
            if isinstance(candidate, dict) and candidate.get("provider"):
                providers.add(str(candidate["provider"]).strip())
    return {provider for provider in providers if provider}


def _declared_policy_pins(policy: dict[str, Any] | None) -> set[tuple[str, str]]:
    """Every ``(provider, model_id)`` a policy names, model ``""`` when absent."""
    pins: set[tuple[str, str]] = set()
    for value in (policy or {}).values():
        for entry in value if isinstance(value, list) else [value]:
            use = entry.get("use") if isinstance(entry, dict) else None
            candidate = use if isinstance(use, dict) else entry
            if isinstance(candidate, dict) and str(candidate.get("provider") or "").strip():
                pins.add((
                    str(candidate["provider"]).strip(),
                    str(candidate.get("model_id", candidate.get("model", "")) or "").strip(),
                ))
    return pins


def _work_invocation_allowance(snapshot, *, minimum: int, ceiling: int) -> int:
    """Reuse accepted finite authority for agent rounds, never mint per-round caps."""
    from tinyassets.shared_self import shared_self_requested

    if (type(minimum) is not int or type(ceiling) is not int
            or minimum < 1 or ceiling < minimum):
        raise PermissionError("workflow exceeds the shared invocation allowance")
    return ceiling if shared_self_requested(snapshot) else minimum


def _binding_matches_seed(binding: Any, seed: ProviderWorkBindingSeed) -> bool:
    """Whether an existing run-class binding already expresses ``seed``.

    Binding ids are deterministic per owner/universe/provider/operation class,
    so a past serving assignment leaves the same row the next assignment must
    use.  Creation replay is generation-sensitive and therefore reports that
    healthy existing row as a conflict after its first rebind.  Compare every
    authority-bearing seed field before reusing it; any mismatch is repaired
    through the binding service's fenced rebind path below.
    """
    return all(
        (
            getattr(getattr(binding, "state", None), "value", None) == "active",
            binding.revocation_generation == 0,
            binding.owner_user_id == seed.owner_user_id,
            binding.universe_id == seed.universe_id,
            binding.provider == seed.provider,
            binding.credential_reference_digest == seed.credential_reference_digest,
            binding.allowed_operations == seed.allowed_operations,
            binding.allowed_roles == seed.allowed_roles,
            binding.assignment_generation == seed.assignment_generation,
            binding.assignment_digest == seed.assignment_digest,
            binding.max_invocations == seed.max_invocations,
            binding.max_tokens == seed.max_tokens,
            binding.max_cost_microunits == seed.max_cost_microunits,
            binding.expires_at == seed.expires_at,
        )
    )


def _held_attempt_error(role: str, selected: Any, exc: BaseException):
    """The held class carrying the failed attempt's redacted evidence.

    The armed attempt on ``selected`` failed and ``capacity_boundary`` could not
    prove a side-effect-free capacity failure, so the loop refuses to advance to
    a sibling model. Before this the cause lived only on ``__cause__``; the
    compiler's event and error readers take ``chain_state`` off the OUTER
    exception, so the run record said "held" and nothing else (live 2026-09-21,
    run 07c1611916cc4eb4). The evidence is the router's own per-attempt
    diagnostics -- classified failure, scrubbed and clipped detail, never the
    provider's response body, a credential, or a path -- and the message names
    the owner's model and connection plus the dominant class so the stored
    string says the cause on its own. No diagnostics is recorded as none: an
    empty chain would read as a provider that was never asked.
    """
    from dataclasses import replace

    from tinyassets.exceptions import ProviderAuthorityHeldError
    from tinyassets.providers.diagnostics import (
        ProviderAttemptDiagnostic,
        build_chain_state,
        dominant_failure_class,
        redacted_failure_detail,
    )
    from tinyassets.workspace_git import scrub_text

    # Re-run the router's OWN scrub/clip boundary here rather than trusting the
    # raise site: this is the point where a per-attempt `detail` stops being an
    # in-process diagnostic and becomes a persisted run record a chatbot reads
    # back. `redacted_failure_detail` is idempotent, so a detail the router
    # already scrubbed is unchanged.
    attempts = [
        replace(item, detail=redacted_failure_detail(item.detail))
        for item in (getattr(exc, "attempts", None) or ())
        if type(item) is ProviderAttemptDiagnostic
    ]
    message = ProviderAuthorityHeldError.ATTEMPT_MESSAGE
    if not attempts:
        return ProviderAuthorityHeldError(message)
    cause = dominant_failure_class(attempts) or attempts[-1].skip_class
    # The owner names their own connections and models; scrubbed anyway, because
    # this string is persisted verbatim as the run error.
    message += scrub_text(
        f": {selected.model_id or 'default model'} on {selected.connection_id} ({cause})"
    )
    chain_state = getattr(exc, "chain_state", None)
    if isinstance(chain_state, dict):
        # Keep the cause's own chain context (allowlist, api-key policy) but
        # publish the attempts through the same boundary as the message.
        chain_state = {**chain_state, "attempts": [a.to_dict() for a in attempts]}
    else:
        chain_state = build_chain_state(
            role=role, chain=[selected.connection_id], attempts=attempts,
        )
    return ProviderAuthorityHeldError(message, attempts=attempts, chain_state=chain_state)


class _SeedResolver:
    def __init__(self, seed: ProviderWorkBindingSeed) -> None:
        self.seed = seed

    def resolve(self, root: ProviderWorkBindingRoot) -> ProviderWorkBindingSeed | None:
        return self.seed if self._matches(root) else None

    def resolve_current_in_transaction(
        self,
        _connection: object,
        root: ProviderWorkBindingRoot,
    ) -> ProviderWorkBindingSeed | None:
        return self.resolve(root)

    def _matches(self, root: ProviderWorkBindingRoot) -> bool:
        return (
            root.owner_user_id == self.seed.owner_user_id
            and root.universe_id == self.seed.universe_id
            and root.provider == self.seed.provider
        )


class _ForegroundRunProviderSession:
    def __init__(
        self,
        base_path: str | Path,
        *,
        universe_id: str,
        principal_id: str,
        provider_call: Callable[..., str],
        model_preference_data: dict | None = None,
    ) -> None:
        self._base_path = Path(base_path)
        self._universe_id = universe_id.strip()
        self._universe_dir = self._base_path / self._universe_id
        self._principal_id = principal_id.strip()
        self._provider_call = provider_call
        self._run_id = ""
        self._branch_def_id = ""
        self._branch_version_id = ""
        self._branch_digest = ""
        self._branch_snapshot: dict[str, Any] | None = None
        self._provider = ""
        self._receipt: ProviderUniverseWorkReceipt | None = None
        self._claim: ProviderWorkExecutionClaim | None = None
        self._call_index = 0
        self._lock = threading.Lock()
        self._closed = False
        self._work_candidates = None
        # Retained so a SIBLING run inherits the SAME captured policy version
        # rather than re-reading the store mid-run. See `constructor_inputs`.
        self._model_preference_data = model_preference_data
        # Once per session. A sibling (async sub-branch) session refreshes for
        # itself: it may run before its parent has made any call, and a flag
        # copied from the parent launched the child on a stale sign-in (Codex
        # round 2 on #4082). A later session's refresh is a no-op while the
        # document is fresh; a renewal that does land between sessions is the
        # cross-run case in docs/concerns/2026-09-28-a-renewal-voids-other-
        # running-receipts.md.
        self._sign_ins_refreshed = False

    def _capture_choices(self) -> None:
        """Build this run's advisory order at ADMISSION, not construction.

        Building it in `__init__` moved a node-time refusal to request time:
        with a saved preference and a revoked source, `run_graph` raised before
        a run row existed, so the caller saw `failure_class: unknown` and no
        `run_id` instead of a run that fails `permission_denied:provider_not_bound`.
        Here, `_admit`'s own handler converts the refusal to the held class at
        the point it already occurred, and an explicit saved choice that is not
        fully eligible still REFUSES rather than being quietly replaced by the
        legacy serving binding.
        """
        if self._model_preference_data is None or self._work_candidates is not None:
            return
        from tinyassets.providers.work_candidate_data import prepare_captured_choices

        self._work_candidates = prepare_captured_choices(
            self._base_path, owner=self._principal_id, universe=self._universe_id,
            document=self._model_preference_data,
        )

    def _validate_founder_home(self) -> None:
        from tinyassets.daemon_server import get_founder_home
        from tinyassets.principals import has_named_principal

        if (
            not has_named_principal(self._principal_id)
            or not self._universe_id
            or get_founder_home(self._base_path, self._principal_id) != self._universe_id
        ):
            raise PermissionError("foreground run is not the principal's own command center")

    def _run_record(self) -> dict[str, Any]:
        from tinyassets.runs import get_run

        record = get_run(self._base_path, self._run_id)
        if record is None:
            raise PermissionError("foreground run record is missing")
        return record

    def _review_purpose(self):
        from tinyassets.agent_review import _PURPOSE, _ReviewPurpose

        purpose = _PURPOSE.get()
        if purpose is None:
            return None
        bound_session, depth = _locate_session(purpose.provider_call)
        if (type(purpose) is not _ReviewPurpose or not purpose.active
                or purpose.universe_dir != self._universe_dir
                or not purpose.run_id or purpose.run_id != self._run_id
                or not purpose.action_sha256
                or not (purpose.provider_call is self
                        or (bound_session is self and depth == 1))):
            raise PermissionError("effect review does not belong to this owner and run")
        if not self._branch_snapshot or not _review_allowance(self._branch_snapshot):
            raise PermissionError("effect review has no declared effect in this run")
        if self._run_record().get("owner_user_id") != self._principal_id:
            raise PermissionError("effect review run owner does not match its principal")
        return purpose

    def _validate_run(self, *, allowed_statuses: set[str]) -> None:
        from tinyassets.runs import is_cancel_requested

        record = self._run_record()
        exact = (
            record.get("status") in allowed_statuses,
            record.get("actor") == f"universe:{self._universe_id}",
            record.get("branch_def_id") == self._branch_def_id,
            (record.get("branch_version_id") or "") == self._branch_version_id,
            not is_cancel_requested(self._base_path, self._run_id),
        )
        if not all(exact):
            raise PermissionError("foreground run state or immutable subject changed")

    @property
    def bound_run_id(self) -> str:
        """The run this session is bound to, or "" before prepare()."""
        return self._run_id

    def constructor_inputs(self) -> dict[str, Any]:
        """Exactly what a SIBLING session needs, and nothing more.

        Deliberately excludes `_receipt`, `_claim`, `_branch_snapshot` and
        `_branch_digest`: a child run must admit on its OWN authority against
        its OWN run row, never inherit the parent's.

        `model_preference_data` IS inherited, and the built
        `WorkCandidateData` is NOT. The document is the owner's captured policy
        version; re-reading the store here would let a save landing mid-run give
        a parallel sub-branch a different order from its parent. The candidate
        object is rebuilt because it carries this run's fitted allowance and
        exhaustion state, and because rebuilding is what re-checks current
        authority and revocation. A preference is advisory either way -- it
        carries no invocation authority, and every attempt still admits afresh.
        """
        return {
            "base_path": self._base_path,
            "universe_id": self._universe_id,
            "principal_id": self._principal_id,
            "provider_call": self._provider_call,
            "model_preference_data": self._model_preference_data,
        }

    def prepare(
        self,
        *,
        run_id: str,
        branch: Any,
        branch_version_id: str | None,
        allowed_statuses: set[str],
    ) -> None:
        from tinyassets.exceptions import ProviderAuthorityHeldError

        if self._run_id:
            raise _held_authority_error(
                PermissionError("this provider session is already bound to a run")
            )
        try:
            snapshot = branch.to_dict()
            branch_def_id = str(snapshot.get("branch_def_id") or "").strip()
            if not branch_def_id:
                raise PermissionError("foreground Branch identity is missing")
            self._run_id = run_id.strip()
            self._branch_def_id = branch_def_id
            self._branch_version_id = str(branch_version_id or "").strip()
            self._branch_snapshot = snapshot
            self._branch_digest = _content_digest(snapshot)
            self._validate_run(allowed_statuses=allowed_statuses)
        except ProviderAuthorityHeldError:
            raise
        except Exception as exc:
            raise _held_authority_error(exc) from exc

    def _admit(self) -> None:
        from tinyassets.exceptions import ProviderAuthorityHeldError
        from tinyassets.provider_assignment import (
            load_provider_assignment_in_transaction,
            provider_assignment_admission,
        )
        from tinyassets.provider_serving_binding import (
            _current_serving_authority,
            resolve_serving_agent_binding,
        )
        from tinyassets.storage.provider_work_authority import (
            SQLiteProviderWorkAuthorityStore,
        )

        if not self._run_id or self._branch_snapshot is None:
            raise _held_authority_error(
                PermissionError("this provider session is not bound to a run yet")
            )
        # Enforcement site (C), foreground half (design.md § Enforcement sites).
        # This lane never calls `claim_assigned`, so the claim CAS cannot cover
        # it; admission has to happen here, and it has to happen *here* rather
        # than next to the `executor_class="cloud"` literals below: everything
        # after this point reads the serving agent binding, the per-universe
        # credential reference and the parent provider binding, and mints child
        # authority. Refusing before that keeps an unadmitted process away from
        # credential access and authority mint, not merely away from dispatch.
        #
        # Ordering: this resolve runs before `store.connection()` /
        # `BEGIN IMMEDIATE` opens below, so the bounded metadata read can never
        # happen while the SQLite write lock is held. Inside the transaction the
        # two literal sites read the cached-only peek instead.
        provenance = resolve_process_cloud_admission()
        if not provenance.is_cloud:
            raise PermissionError(
                platform_not_cloud_message(
                    provenance, surface="foreground run provider authority"
                )
            )
        try:
            self._validate_founder_home()
            snapshot = self._branch_snapshot
            branch_author = str(snapshot.get("author") or "").strip()
            if branch_author != self._principal_id:
                raise PermissionError("foreground Branch author is not the principal")
            nodes = _prompt_nodes(snapshot)
            review_slots = _review_allowance(snapshot)
            roles = tuple(
                sorted(
                    {
                        str(node.get("model_hint") or "writer").strip() or "writer"
                        for node in nodes
                    } | ({"writer"} if review_slots else set())
                )
            )
            if not set(roles).issubset(_SUPPORTED_ROLES):
                raise PermissionError("foreground Branch requests an unsupported role")
            if not nodes and self._review_purpose() is None:
                raise PermissionError("foreground provider attempt has no prompt node")
            self._validate_run(allowed_statuses={"running"})
            # AFTER every refusal this lane can decide from stored state, and
            # before `_admit_manifest` fits the order. Capturing the order is
            # credential-bearing outbound IO (a catalogue read on the owner's own
            # grant), so it must not happen for a run `_admit` was going to
            # refuse anyway. It sat above the author check, and running another
            # user's PUBLIC Branch therefore made one discovery request on the
            # requester's source before the refusal -- the same shape as the
            # sign-in refresh Codex found on #4082 and the same fix (Codex
            # refutation of this change, C2, 2026-09-29).
            self._capture_choices()

            agent = resolve_serving_agent_binding(
                self._base_path,
                universe_id=self._universe_id,
                owner_user_id=self._principal_id,
            )
            store = SQLiteProviderWorkAuthorityStore(self._base_path)
            with provider_assignment_admission().shared(self._universe_dir):
                with store.connection() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    try:
                        observed = load_provider_assignment_in_transaction(
                            conn, universe_id=self._universe_id,
                        )
                        if observed is not None and observed.manifest_digest:
                            receipt, claim, default_provider = self._admit_manifest(
                                conn, store, agent, observed, nodes, roles,
                            )
                            self._validate_run(allowed_statuses={"running"})
                            conn.commit()
                            self._provider, self._receipt, self._claim = (
                                default_provider, receipt, claim,
                            )
                            return
                        assignment, parent_binding, _custody = _current_serving_authority(
                            conn,
                            store=store,
                            universe_dir=self._universe_dir,
                            owner_user_id=self._principal_id,
                            universe_id=self._universe_id,
                            agent=agent,
                        )
                        declared = set().union(
                            *(
                                _declared_policy_providers(node.get("llm_policy"))
                                for node in nodes
                            )
                        )
                        if declared - {assignment.provider}:
                            raise PermissionError(
                                "foreground Branch policy is outside the active provider"
                            )
                        if (
                            len(nodes) + review_slots > parent_binding.max_invocations
                            or not set(roles).issubset(parent_binding.allowed_roles)
                            or parent_binding.max_tokens < 1
                            or parent_binding.max_cost_microunits < 1
                        ):
                            raise PermissionError(
                                "foreground Branch exceeds active serving authority"
                            )
                        seed = ProviderWorkBindingSeed(
                            owner_user_id=self._principal_id,
                            universe_id=self._universe_id,
                            provider=assignment.provider,
                            credential_reference_digest=(
                                parent_binding.credential_reference_digest
                            ),
                            allowed_operations=(RUN_GRAPH_OPERATION,),
                            allowed_roles=parent_binding.allowed_roles,
                            assignment_generation=assignment.generation,
                            assignment_digest=assignment.assignment_digest,
                            max_invocations=parent_binding.max_invocations,
                            max_tokens=parent_binding.max_tokens,
                            max_cost_microunits=parent_binding.max_cost_microunits,
                            expires_at=parent_binding.expires_at,
                        )
                        root = ProviderWorkBindingRoot(
                            owner_user_id=seed.owner_user_id,
                            universe_id=seed.universe_id,
                            provider=seed.provider,
                        )
                        child_binding = None
                        service = ProviderWorkBindingService(store, _SeedResolver(seed))
                        issued = service.issue_in_transaction(conn, root)
                        if (
                            issued.outcome is ProviderWorkAuthorityWriteOutcome.CONFLICT
                            and issued.record is not None
                        ):
                            if _binding_matches_seed(issued.record, seed):
                                child_binding = issued.record
                            else:
                                issued = service.rebind_in_transaction(
                                    conn,
                                    ProviderWorkBindingFence(issued.record),
                                    root,
                                )
                        if child_binding is None:
                            if (
                                issued.outcome
                                not in {
                                    ProviderWorkAuthorityWriteOutcome.APPLIED,
                                    ProviderWorkAuthorityWriteOutcome.REPLAYED,
                                }
                                or issued.record is None
                            ):
                                raise PermissionError(
                                    "run child provider binding is unavailable"
                                )
                            child_binding = issued.record
                        subject_ref = self._branch_version_id or (
                            f"{self._branch_def_id}@definition:"
                            f"{int(snapshot.get('version') or 1)}"
                        )
                        authority = ProviderUniverseWorkAuthority(
                            root=ProviderUniverseWorkRoot(
                                work_item_kind="run",
                                work_item_id=self._run_id,
                            ),
                            binding=child_binding,
                            principal_id=self._principal_id,
                            actor_id=f"universe:{self._universe_id}",
                            operation=RUN_GRAPH_OPERATION,
                            role=roles[0],
                            allowed_roles=roles,
                            # Cached-only: a peek, never a resolve, so no socket
                            # opens under the write lock this block holds. The
                            # class below is stamped `cloud` as a literal, so the
                            # value has to be true rather than asserted.
                            executor_class=_admitted_cloud_class(),
                            max_invocations=_work_invocation_allowance(
                                snapshot, minimum=len(nodes) + review_slots,
                                ceiling=child_binding.max_invocations,
                            ),
                            max_tokens=child_binding.max_tokens,
                            max_cost_microunits=child_binding.max_cost_microunits,
                            expires_at=child_binding.expires_at,
                            execution_subject=ExecutionSubject(
                                kind=ExecutionSubjectKind.BRANCH_VERSION,
                                ref=subject_ref,
                                digest=self._branch_digest,
                            ),
                            branch_def_id=self._branch_def_id,
                            branch_version_id=subject_ref,
                            parent_binding_id=parent_binding.binding_id,
                            parent_binding_generation=parent_binding.generation,
                            parent_binding_digest=parent_binding.binding_digest,
                            parent_binding_revocation_generation=(
                                parent_binding.revocation_generation
                            ),
                        )
                        nonce = _content_digest(
                            [
                                self._run_id,
                                self._principal_id,
                                self._universe_id,
                                self._branch_digest,
                                os.getpid(),
                            ]
                        )
                        receipt, claim = store._admit_run_in_transaction(
                            conn,
                            authority=authority,
                            worker_id=f"foreground-run:{os.getpid()}",
                            runtime_id=f"run:{self._run_id}",
                            claim_nonce_digest=nonce,
                            lease_seconds=3600,
                        )
                        self._validate_run(allowed_statuses={"running"})
                        conn.commit()
                    except Exception:
                        conn.rollback()
                        raise
            self._provider = assignment.provider
            self._receipt = receipt
            self._claim = claim
        except ProviderAuthorityHeldError:
            raise
        except Exception as exc:
            raise _held_authority_error(exc) from exc

    def _resolve_declared_pins(self, nodes, accepted):
        """Refuse a node pin naming no single accepted source, listing the refs.

        A bare access method resolves (``providers.model_pins``) against the
        sources this run admitted, model-aware when the run captured the owner's
        catalogue. Only the refusal's words changed for a pin that cannot
        resolve: it used to say "unavailable accepted provider" and name nothing.
        """
        from tinyassets.providers.model_pins import resolve_pin_source

        catalog = getattr(getattr(self, "_work_candidates", None), "catalog", None)
        offered = {
            connection.connection_id: tuple(model.model_id for model in connection.models)
            for connection in (catalog.connections if catalog is not None else ())
        }
        sources = {ref: offered.get(ref) for ref in accepted}
        for node in nodes:
            for provider, model_id in _declared_policy_pins(node.get("llm_policy")):
                if resolve_pin_source(provider, model_id, sources) not in sources:
                    # An exact ref this run did not admit: the existing refusal.
                    raise PermissionError("workflow requests an unavailable accepted provider")

    def _admit_manifest(self, conn, store, agent, assignment, nodes, roles):
        """One aggregate receipt for all nodes, not one full allowance per source."""
        from tinyassets.graph_compiler import _POLICY_PROVIDER_RETRY_BACKOFF_SECONDS
        from tinyassets.provider_serving_binding import _current_selected_member_authority

        bindings = []
        for member in assignment.candidates:
            try:
                _assignment, binding, _custody = _current_selected_member_authority(
                    conn, store=store, universe_dir=self._universe_dir, base_path=self._base_path,
                    owner_user_id=self._principal_id, universe_id=self._universe_id,
                    agent=agent, provider=member.provider,
                )
            except PermissionError:
                continue
            if set(roles) <= set(binding.allowed_roles):
                bindings.append(binding)
        if not bindings:
            raise PermissionError("workflow requests an unavailable accepted provider")
        self._resolve_declared_pins(nodes, {binding.provider for binding in bindings})
        policies = [
            node.get("llm_policy") or self._branch_snapshot.get("default_llm_policy")
            for node in nodes
        ]
        # Share the actual compiler's bounded policy-retry count. Include the
        # requested fallback tail, not a fresh member allowance for each retry.
        max_invocations = sum(
            (1 + len(policy.get("fallback_chain", []))
             + int(bool(policy.get("difficulty_override"))))
            * (1 + len(_POLICY_PROVIDER_RETRY_BACKOFF_SECONDS)) if policy else 1
            for policy in policies
        ) + _review_allowance(self._branch_snapshot)
        if getattr(self, "_work_candidates", None) is not None:
            max_invocations = self._work_candidates.fit(
                self._branch_snapshot,
                ceiling=min(binding.max_invocations for binding in bindings),
                retry_multiplier=1 + len(_POLICY_PROVIDER_RETRY_BACKOFF_SECONDS),
                review_attempts=_review_allowance(self._branch_snapshot),
            )
        max_invocations = _work_invocation_allowance(
            self._branch_snapshot, minimum=max_invocations,
            ceiling=min(binding.max_invocations for binding in bindings),
        )
        subject_ref = self._branch_version_id or (
            f"{self._branch_def_id}@definition:{int(self._branch_snapshot.get('version') or 1)}"
        )
        root = ProviderUniverseWorkRoot(work_item_kind="run", work_item_id=self._run_id)
        candidate = ProviderUniverseWorkReceipt(
            schema_version=4, authority_scope="manifest",
            manifest_digest=assignment.manifest_digest,
            receipt_id=provider_work_receipt_id(universe_id=self._universe_id, root=root),
            receipt_digest="sha256:" + "0" * 64, generation=1,
            state=ProviderWorkReceiptState.ACTIVE, work_item_kind="run", work_item_id=self._run_id,
            binding_id=None, binding_generation=None, binding_digest=None,
            binding_revocation_generation=None, provider=None, credential_reference_digest=None,
            principal_id=self._principal_id, actor_id=f"universe:{self._universe_id}",
            universe_id=self._universe_id, branch_def_id=self._branch_def_id,
            branch_version_id=subject_ref, assignment_generation=assignment.generation,
            assignment_digest=assignment.assignment_digest,
            # Manifest path: same in-transaction cached-only admission as the
            # non-manifest branch. A manifest run mints one aggregate receipt
            # over several member bindings and would otherwise be the way around
            # the check above.
            executor_class=_admitted_cloud_class(),
            allowed_operations=(RUN_GRAPH_OPERATION,), allowed_roles=roles,
            max_invocations=max_invocations,
            max_tokens=min(binding.max_tokens for binding in bindings),
            max_cost_microunits=min(binding.max_cost_microunits for binding in bindings),
            expires_at=min(
                bindings, key=lambda binding: datetime.fromisoformat(
                    binding.expires_at.replace("Z", "+00:00"),
                ),
            ).expires_at,
            created_at=store.timestamp(),
            execution_subject=ExecutionSubject(
                kind=ExecutionSubjectKind.BRANCH_VERSION, ref=subject_ref,
                digest=self._branch_digest,
            ),
        )
        candidate = replace(candidate, receipt_digest=candidate.expected_digest())
        receipt, claim = store._admit_run_in_transaction(
            conn, authority=candidate, manifest_bindings=tuple(bindings),
            worker_id=f"foreground-run:{os.getpid()}", runtime_id=f"run:{self._run_id}",
            claim_nonce_digest=_content_digest([
                self._run_id, self._principal_id, self._universe_id,
                self._branch_digest, os.getpid(),
            ]), lease_seconds=3600,
        )
        default = next(
            (binding.provider for binding in bindings if binding.provider == assignment.provider),
            bindings[0].provider,
        )
        return receipt, claim, default

    def _ensure_admitted(self) -> None:
        if self._receipt is not None:
            return
        with self._lock:
            if self._receipt is None:
                self._refresh_sign_ins()
                self._admit()

    def _refresh_sign_ins(self) -> None:
        """Bring the owner's stored sign-ins current before the receipt pins them.

        The receipt `_admit` mints pins the assignment generation and credential
        digest for the whole run, and a refresh renews the accepted source,
        which moves both. So this runs once, before that mint, and never after
        it. Only for a run `_admit` would accept on these grounds -- the
        principal's own home, a Branch they authored, a run still running;
        anything else is refused by `_admit` with its own words and refreshes
        nothing on the way (Codex refute-review on #4082: another user's public
        Branch refreshed the requester's sign-in and then failed).
        """
        if self._sign_ins_refreshed:
            return
        self._sign_ins_refreshed = True
        try:
            self._validate_founder_home()
            author = str((self._branch_snapshot or {}).get("author") or "").strip()
            if author != self._principal_id:
                return
            self._validate_run(allowed_statuses={"running"})
        except PermissionError:
            return
        from tinyassets.subscription_refresh import refresh_deposited_subscriptions

        refresh_deposited_subscriptions(
            base_path=self._base_path,
            universe_dir=self._universe_dir,
            owner_user_id=self._principal_id,
            universe_id=self._universe_id,
        )

    def _validate_receipt_parent(self, parent_binding: Any, assignment: Any) -> None:
        receipt = self._receipt
        if receipt is None:
            raise PermissionError("foreground run receipt is unavailable")
        common = (
            receipt.principal_id == self._principal_id,
            receipt.actor_id == f"universe:{self._universe_id}",
            receipt.universe_id == self._universe_id,
            receipt.work_item_id == self._run_id,
            receipt.branch_def_id == self._branch_def_id,
            receipt.execution_subject is not None,
            receipt.execution_subject is not None
            and receipt.execution_subject.digest == self._branch_digest,
            receipt.assignment_generation == assignment.generation,
            receipt.assignment_digest == assignment.assignment_digest,
        )
        exact = (
            receipt.manifest_digest == assignment.manifest_digest,
        ) if receipt.authority_scope == "manifest" else (
            receipt.provider == assignment.provider == self._provider,
            receipt.credential_reference_digest
            == assignment.credential_reference_digest,
            receipt.parent_binding_id == parent_binding.binding_id,
            receipt.parent_binding_generation == parent_binding.generation,
            receipt.parent_binding_digest == parent_binding.binding_digest,
            receipt.parent_binding_revocation_generation
            == parent_binding.revocation_generation,
        )
        if not all((*common, *exact)):
            raise PermissionError("foreground run provider authority is stale")

    def _check_agent_authority(self, carrier: ProviderInvocationCarrier) -> str:
        """Fresh fence between agent steps or after a review; never rearm a call."""
        import hmac

        from tinyassets.provider_assignment import provider_assignment_admission
        from tinyassets.provider_serving_binding import (
            _current_selected_member_authority,
            _current_serving_authority,
            resolve_serving_agent_binding,
        )
        from tinyassets.provider_work_authority import _provider_invocation_carrier_seal
        from tinyassets.shared_self import shared_self_requested
        from tinyassets.storage.current_home import check_current_home
        from tinyassets.storage.provider_work_authority import (
            SQLiteProviderWorkAuthorityStore,
            _claim_record,
            _current_work_member,
            _receipt_record,
            _record,
            _reservation_record,
        )

        if (self._closed or self._receipt is None or self._claim is None
                or type(carrier) is not ProviderInvocationCarrier
                or carrier._issuer_pid != os.getpid()
                or not hmac.compare_digest(
                    carrier._seal, _provider_invocation_carrier_seal(carrier),
                )
                or carrier._receipt != self._receipt or carrier._claim != self._claim):
            raise PermissionError("work agent carrier does not match its active run")
        if (self._branch_snapshot is None
                or _content_digest(self._branch_snapshot) != self._branch_digest
                or (not shared_self_requested(self._branch_snapshot)
                    and self._review_purpose() is None)):
            raise PermissionError("work agent immutable subject changed")
        self._validate_founder_home()
        self._validate_run(allowed_statuses={"running"})
        store = SQLiteProviderWorkAuthorityStore(self._base_path)
        agent = resolve_serving_agent_binding(
            self._base_path, universe_id=self._universe_id, owner_user_id=self._principal_id,
        )
        with provider_assignment_admission().shared(self._universe_dir):
            with store.connection() as conn:
                conn.execute("BEGIN")
                check_current_home(conn, self._principal_id, self._universe_id)
                receipt_row = conn.execute(
                    "SELECT * FROM provider_work_receipts WHERE receipt_id = ?",
                    (carrier.work_receipt_id,),
                ).fetchone()
                claim_row = conn.execute(
                    "SELECT * FROM provider_work_execution_claims WHERE claim_id = ?",
                    (self._claim.claim_id,),
                ).fetchone()
                reservation_row = conn.execute(
                    "SELECT * FROM provider_invocation_reservations WHERE reservation_id = ?",
                    (carrier.reservation_id,),
                ).fetchone()
                if receipt_row is None or claim_row is None or reservation_row is None:
                    raise PermissionError("work agent progress authority is unavailable")
                receipt, claim = _receipt_record(receipt_row), _claim_record(claim_row)
                reservation = _reservation_record(reservation_row)
                now = store._now()
                if not all((
                    receipt == self._receipt, claim == self._claim,
                    receipt.state is ProviderWorkReceiptState.ACTIVE,
                    claim.state.value == "active",
                    datetime.fromisoformat(receipt.expires_at.replace("Z", "+00:00")) > now,
                    datetime.fromisoformat(claim.lease_expires_at.replace("Z", "+00:00")) > now,
                    reservation.receipt_id == receipt.receipt_id,
                    reservation.receipt_digest == receipt.receipt_digest,
                    reservation.claim_id == claim.claim_id,
                    reservation.claim_digest == claim.claim_digest,
                    reservation.claim_generation == claim.generation,
                    reservation.selection == carrier._reservation.selection,
                    reservation.operation == carrier.operation == RUN_GRAPH_OPERATION,
                    reservation.role == carrier.role == "writer",
                    reservation.state.value in {"launch_started", "succeeded"},
                )):
                    raise PermissionError("work agent receipt, claim or invocation changed")
                if receipt.authority_scope == "manifest":
                    assignment, binding, _ = _current_selected_member_authority(
                        conn, store=store, universe_dir=self._universe_dir,
                        base_path=self._base_path, owner_user_id=self._principal_id,
                        universe_id=self._universe_id, agent=agent, provider=carrier.provider,
                    )
                    _current_work_member(conn, receipt, reservation.selection, now)
                else:
                    child_row = conn.execute(
                        "SELECT * FROM provider_work_bindings WHERE binding_id = ?",
                        (receipt.binding_id,),
                    ).fetchone()
                    child = _record(child_row) if child_row is not None else None
                    if child is None or not all((
                        child.state.value == "active",
                        child.generation == receipt.binding_generation,
                        child.binding_digest == receipt.binding_digest,
                        child.revocation_generation == receipt.binding_revocation_generation,
                        datetime.fromisoformat(child.expires_at.replace("Z", "+00:00")) > now,
                        reservation.operation in child.allowed_operations,
                        reservation.role in child.allowed_roles,
                    )):
                        raise PermissionError("work agent child binding changed")
                    assignment, binding, _ = _current_serving_authority(
                        conn, store=store, universe_dir=self._universe_dir,
                        owner_user_id=self._principal_id, universe_id=self._universe_id,
                        agent=agent,
                    )
                self._validate_receipt_parent(binding, assignment)
        self._validate_run(allowed_statuses={"running"})
        return self._principal_id

    @contextmanager
    def _authorize_attempt(
        self,
        *,
        role: str,
        prompt: str,
        system: str,
        policy: dict[str, Any] | None,
    ) -> Iterator[tuple[ProviderInvocationCarrier, Path | None, str]]:
        from tinyassets.credential_vault import (
            cleanup_llm_credential_snapshot,
            snapshot_llm_subscription_credential,
        )
        from tinyassets.exceptions import ProviderAuthorityHeldError
        from tinyassets.provider_assignment import provider_assignment_admission
        from tinyassets.provider_serving_binding import (
            _current_selected_member_authority,
            _current_serving_authority,
            _is_open_provider,
            resolve_serving_agent_binding,
        )
        from tinyassets.shared_self import shared_self_requested
        from tinyassets.storage.provider_work_authority import (
            SQLiteProviderWorkAuthorityStore,
        )

        # Agent tool rounds can enter directly, without going through _call.
        # A retained receipt is not process admission. This check is cached-only.
        _admitted_cloud_class()
        snapshot = None
        carrier = None
        try:
            review = self._review_purpose()
            if review is not None:
                from tinyassets.agent_review import SAFETY_REQUIREMENTS

                if role != "writer" or prompt != review.prompt or system != SAFETY_REQUIREMENTS:
                    raise PermissionError("effect review cannot substitute its text-only purpose")
                review.consume()
            if self._closed or self._receipt is None or self._claim is None:
                raise PermissionError("foreground run provider session is not active")
            if role not in self._receipt.allowed_roles:
                raise PermissionError("foreground run provider role is not authorized")
            if (self._receipt.authority_scope != "manifest"
                    and _declared_policy_providers(policy) - {self._provider}):
                raise PermissionError("foreground policy is outside the active provider")
            self._validate_founder_home()
            if self._branch_snapshot is None or (
                _content_digest(self._branch_snapshot) != self._branch_digest
            ):
                raise PermissionError("foreground immutable Branch subject changed")
            self._validate_run(allowed_statuses={"running"})
            # NO pre-launch credential refresh here, deliberately. This lane's
            # receipt is already minted by the time `_authorize_attempt` runs, and
            # it PINS `assignment_generation` + `credential_reference_digest`
            # (:683 and :689 below). A refresh renews the accepted source, which
            # advances the assignment generation -- so refreshing here failed the
            # receipt check it was meant to help, and the resulting PermissionError
            # was swallowed into ProviderAuthorityHeldError at :985, which cannot
            # fall back either. Codex refute-review P1 #3 reproduced both halves.
            #
            # The refresh belongs where nothing is pinned yet: for this lane that
            # is `_refresh_sign_ins`, run once before the receipt mint.
            with self._lock:
                self._call_index += 1
                invocation_index = self._call_index
            prompt_digest = _content_digest([
                role, prompt, system,
                ["effect_review", review.action_sha256] if review else "prompt_node",
            ])
            invocation_key = (
                f"run:{self._run_id}:{invocation_index}:{prompt_digest.removeprefix('sha256:')}"
            )
            agent = resolve_serving_agent_binding(
                self._base_path,
                universe_id=self._universe_id,
                owner_user_id=self._principal_id,
            )
            store = SQLiteProviderWorkAuthorityStore(self._base_path)
            model_snapshot = None
            if self._receipt.authority_scope == "manifest":
                from tinyassets.providers.work_model_selection import prepare_work_model_snapshot

                preferred = (policy or {}).get("preferred", {})
                if not isinstance(preferred, dict):
                    raise PermissionError("workflow model preference is invalid")
                model_snapshot = prepare_work_model_snapshot(
                    base_path=self._base_path, universe_id=self._universe_id,
                    provider=preferred.get("provider") or self._provider,
                    model_id=preferred.get("model_id", preferred.get("model", "")),
                )
            with provider_assignment_admission().shared(self._universe_dir):
                with store.connection() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    try:
                        selection = None
                        if self._receipt.authority_scope == "manifest":
                            preferred = (policy or {}).get("preferred", {})
                            if not isinstance(preferred, dict):
                                raise PermissionError("workflow model preference is invalid")
                            provider = preferred.get("provider") or self._provider
                            model_id = preferred.get("model_id", preferred.get("model", ""))
                            if ("model_id" in preferred and "model" in preferred
                                    and preferred["model_id"] != preferred["model"]):
                                raise PermissionError("workflow model preference is conflicting")
                            current = _current_selected_member_authority(
                                conn, store=store, universe_dir=self._universe_dir,
                                base_path=self._base_path, owner_user_id=self._principal_id,
                                universe_id=self._universe_id, agent=agent, provider=provider,
                            )
                            assignment, parent_binding, custody = current
                            member = next(
                                m for m in assignment.candidates if m.provider == provider
                            )
                            selection = ProviderInvocationSelection(
                                provider=provider, binding_id=parent_binding.binding_id,
                                binding_generation=parent_binding.generation,
                                binding_digest=parent_binding.binding_digest,
                                binding_revocation_generation=parent_binding.revocation_generation,
                                credential_reference_id=custody.reference_id,
                                credential_reference_generation=custody.generation,
                                credential_reference_digest=custody.reference_digest,
                                assignment_generation=assignment.generation,
                                assignment_digest=assignment.assignment_digest,
                                manifest_digest=assignment.manifest_digest,
                                member_digest=member.digest(
                                    self._universe_id, assignment.generation,
                                ),
                                model_id=model_id, executor_id=provider,
                            )
                        else:
                            assignment, parent_binding, custody = _current_serving_authority(
                                conn, store=store, universe_dir=self._universe_dir,
                                owner_user_id=self._principal_id, universe_id=self._universe_id,
                                agent=agent,
                            )
                            provider = assignment.provider
                        self._validate_receipt_parent(parent_binding, assignment)
                        shares = (len(_prompt_nodes(self._branch_snapshot))
                                  + _review_allowance(self._branch_snapshot))
                        token_share = max(
                            1,
                            self._receipt.max_tokens // shares,
                        )
                        cost_share = max(
                            1,
                            self._receipt.max_cost_microunits
                            // shares,
                        )
                        carrier = store._reserve_and_arm_run_carrier_in_transaction(
                            conn,
                            receipt=self._receipt,
                            claim=self._claim,
                            invocation_key=invocation_key,
                            role=role,
                            max_tokens=token_share,
                            max_cost_microunits=cost_share,
                            selection=selection,
                            model_snapshot=model_snapshot,
                            needs_tools=(review is None
                                         and shared_self_requested(self._branch_snapshot)),
                        )
                        if not _is_open_provider(provider):
                            snapshot = snapshot_llm_subscription_credential(
                                universe_dir=self._universe_dir,
                                custody=custody,
                            )
                        self._validate_run(allowed_statuses={"running"})
                        conn.commit()
                    except Exception:
                        conn.rollback()
                        raise
            yield (
                carrier,
                snapshot.directory if snapshot is not None else None,
                provider,
            )
        except ProviderAuthorityHeldError:
            raise
        except Exception as exc:
            if carrier is not None:
                raise
            raise _held_authority_error(exc) from exc
        finally:
            cleanup_llm_credential_snapshot(snapshot)

    def _call(
        self,
        role: str,
        prompt: str,
        system: str,
        config: Any,
        policy: dict[str, Any] | None,
        kwargs: dict[str, Any],
    ) -> tuple[str, str]:
        supplied_operation = kwargs.pop("operation", RUN_GRAPH_OPERATION)
        supplied_context = kwargs.pop("universe_context", None)
        if supplied_operation != RUN_GRAPH_OPERATION:
            raise PermissionError("foreground provider operation cannot be substituted")
        if supplied_context is not None and (
            Path(supplied_context.universe_dir) != self._universe_dir
        ):
            raise PermissionError("foreground provider command center cannot be substituted")
        if supplied_context is not None and any(
            getattr(supplied_context, field, None) is not None
            for field in ("provider_request", "provider_invocation", "served_provider",
                          "agent_model_plan", "model_selection")
        ):
            raise PermissionError("foreground provider authority cannot be substituted")
        if self._closed:
            raise _held_authority_error(
                PermissionError("this run's provider session is already closed")
            )

        review = self._review_purpose()
        if review is not None:
            from tinyassets.agent_review import SAFETY_REQUIREMENTS

            if (role != "writer" or prompt != review.prompt
                    or system != SAFETY_REQUIREMENTS or config is not None
                    or policy is not None or kwargs):
                raise PermissionError("effect review cannot substitute its text-only purpose")
            config = ModelConfig()
        elif self._branch_snapshot is not None and not _prompt_nodes(self._branch_snapshot):
            raise _held_authority_error(
                PermissionError("foreground provider attempt has no prompt node")
            )

        # Enforcement site (C), foreground half, at the call boundary — the
        # mirror of the served lane's gate in `background_served_provider._call`.
        # `_admit()` alone is not enough here, for two independent reasons:
        #
        #  * the injected-stub branch immediately below dispatches to the
        #    caller's callable *before* `_ensure_admitted()` is ever reached, so
        #    an unadmitted process would reach a provider invocation with no
        #    admission at all; and
        #  * `_ensure_admitted()` returns early once `self._receipt` is set, so
        #    on a multi-node run only the first call would be admitted and every
        #    later node would ride the cached receipt. A receipt is a record of
        #    an earlier admission, never a standing permission — the same rule
        #    `runtime_matches_worker_provider` applies to a registration row.
        #
        # Covers both public doors: `__call__` (raw) and `call_with_policy_sync`
        # (structured) both funnel through `_call`. Outside every transaction —
        # `_admit()` opens its own below — so this may resolve; the two
        # in-transaction stamp sites stay cached-only.
        call_provenance = resolve_process_cloud_admission()
        if not call_provenance.is_cloud:
            raise PermissionError(
                platform_not_cloud_message(
                    call_provenance, surface="foreground run provider call"
                )
            )

        # Server-owned in-process callers may inject a provider stub instead of
        # the production call primitive. Give that stub one fail-closed,
        # unarmed invocation: a real ProviderRouter refuses before launch, while a
        # mock returns without creating provider authority or a run receipt.
        if (review is None
                and getattr(self._provider_call, "__module__", "") != "tinyassets.providers.call"):
            from tinyassets.exceptions import ProviderAuthorityHeldError
            from tinyassets.providers.base import UniverseContext

            try:
                response = self._provider_call(
                    prompt,
                    system,
                    role=role,
                    config=config,
                    operation=RUN_GRAPH_OPERATION,
                    universe_context=UniverseContext(
                        universe_dir=self._universe_dir,
                        config=None,
                    ),
                    **kwargs,
                )
            except ProviderAuthorityHeldError:
                pass
            else:
                return response, "mock"

        self._ensure_admitted()
        from tinyassets.shared_self import agent_node, prepare_shared_self_turn

        node = None if review is not None else agent_node(
            self._branch_snapshot, getattr(config, "agent_node_id", ""), self._principal_id,
            node_key=getattr(config, "agent_node_key", ""),
        )
        if node is not None:
            prompt, system, config = prepare_shared_self_turn(
                self._base_path, self._universe_id, self._principal_id, prompt, config, node,
            )
            if config.engine_mcp_enabled:
                from tinyassets.workflow_agent import call_foreground_work_agent

                response_observer = kwargs.pop("response_observer", None)
                metadata_observer = kwargs.pop("_metadata_observer", None)
                if role != "writer" or kwargs:
                    raise PermissionError("workflow agent call cannot substitute execution context")
                return call_foreground_work_agent(
                    self, prompt=prompt, system=system, config=config, policy=policy,
                    **({"response_observer": response_observer}
                       if response_observer is not None else {}),
                    **({"metadata_observer": metadata_observer}
                       if metadata_observer is not None else {}),
                )
        metadata_observer = kwargs.pop("_metadata_observer", None)
        if self._work_candidates is not None:
            return self._call_captured_prompt(
                role, prompt, system, config, policy, kwargs, metadata_observer,
            )
        return self._call_once(role, prompt, system, config, policy, kwargs)

    def _narrowed_exhaustion(self, boundary, kind, narrowings, config):
        """Exclude only the failed MODEL when excluding the source is a guess.

        `capacity_boundary` collapses an unreported window to the whole account
        on purpose -- it never invents independence -- and exposes
        `observed_scope` so the CALLER can decide. A conversation turn already
        does (`AgentTurnCoordinator._narrowed`); a workflow node did not, and
        took the conservative reading as final. On a free account whose ONE
        source holds every model, that rejected every sibling, so a single
        model's 429 ended the run after one attempt while the owner's chat turn
        stepped to the next free model on the identical refusal (live
        2026-09-30, run `c22c1cb12db74d6a`).

        The decision is the platform's single capacity policy, not a second one:
        `free_sibling_retry` answers whether an unknown window buys a sibling,
        the same call and the same bound the conversation path uses. Engine
        inference only -- a native executor runs on ONE subscription, so a limit
        there is a fact about that account rather than a model within it.

        Returns the exhaustion to record and whether it rests on a guess.
        """
        from dataclasses import replace as _replace

        from tinyassets.providers.model_capacity import (
            MAX_FREE_SIBLING_RETRIES,
            free_sibling_retry,
        )

        if kind != "engine_inference" or narrowings >= MAX_FREE_SIBLING_RETRIES:
            return boundary.exhaustion, False
        if not free_sibling_retry(
            scope=boundary.observed_scope,
            failure_class=boundary.failure_class,
            retry_after_s=boundary.retry_after_s,
            # The NODE's own deadline is this work's budget, so a source naming a
            # window longer than the node may live rules the sibling out here for
            # the same reason it does on a turn.
            turn_budget_s=getattr(config, "absolute_cap_s", None),
        ):
            return boundary.exhaustion, False
        return _replace(boundary.exhaustion, scope="model"), True

    def _remember_refusal(self, selected, attempts):
        """Keep the source's refusal of THIS model past this run.

        The same per-owner mark a chat turn writes
        (``AgentTurnCoordinator._remember_refusal``), so both surfaces order the
        model last next time. Per owner, never shared across users: a refusal
        is what this owner's key was told. Best-effort, never the run's failure.
        """
        from tinyassets.storage.refused_models import record_refused_model

        try:
            record_refused_model(
                self._base_path, owner_user_id=self._principal_id,
                connection_id=selected.connection_id, model_id=selected.model_id,
                failure_class="provider_refused",
                detail=str(getattr(attempts[-1], "detail", "") or "") if attempts else "",
            )
        except Exception:  # noqa: BLE001 - bookkeeping, never the failure
            logger.warning("could not remember a refused work model")

    def _forget_refusal(self, selected):
        """The model just answered this owner, so a standing refusal is stale."""
        from tinyassets.storage.refused_models import clear_refused_model

        try:
            clear_refused_model(
                self._base_path, owner_user_id=self._principal_id,
                connection_id=selected.connection_id, model_id=selected.model_id,
            )
        except Exception:  # noqa: BLE001 - an answered node never fails on bookkeeping
            logger.warning("could not clear a refused work model")

    def _cool_abandoned_sources(self, boundaries, *, keeping=None):
        """Cool every source this node leaves hot, however the node ended.

        The withheld cooldown bought exactly one thing: another model on the
        same grant. Once the node is done with a source that purchase is over,
        and a source at a DAILY cap -- which refuses every model -- would
        otherwise have every later run pay the full order again, forever. The
        conversation path settles the same debt in
        `AgentTurnCoordinator._cool_abandoned_source`.

        ``keeping`` is the connection the node actually got its answer from, and
        it is NOT cooled: a source that just served this node is evidently
        working, and cooling it would penalise the very fallback that succeeded.
        That mirrors `_leave_hot_source`, which cools a hot source only once the
        turn moves off it. On a single-source account this is the ordinary
        outcome -- 429 on one model, answered on the next -- so cooling there
        would undo the fix.

        Never raises: a failing node must not be replaced by a cooling error.
        """
        from tinyassets.providers.call import get_provider_router
        from tinyassets.providers.model_capacity import free_sibling_retry

        router = get_provider_router()
        if router is None:
            return
        for boundary in boundaries:
            try:
                connection = boundary.exhaustion.ref.connection_id
                if connection == keeping:
                    continue
                # Only a cooldown the router WITHHELD is owed, which is the
                # chat turn's own test (`AgentTurnCoordinator.
                # _cool_abandoned_source`): an unknown-scope transient refusal.
                # A model-scoped one (one model overloaded, one model refused)
                # was never the source's, and cooling it here put a source the
                # owner's chat was using into cooldown for the next run.
                if not free_sibling_retry(
                    scope=boundary.observed_scope, failure_class=boundary.failure_class,
                ):
                    continue
                router.cool_source(connection, retry_after_s=boundary.retry_after_s,
                                   reason=boundary.failure_class or "")
            except Exception:  # noqa: BLE001 - hygiene, never the failure
                logger.warning("could not cool a spent work source")

    def _call_captured_prompt(self, role, prompt, system, config, policy, kwargs,
                              metadata_observer):
        from tinyassets.exceptions import AllProvidersExhaustedError
        from tinyassets.providers.agent_capacity_boundary import (
            capacity_boundary,
            refusal_boundary,
        )
        from tinyassets.providers.call import get_provider_router

        attempts = 0
        boundaries = ()
        narrowings = 0
        last_capacity = None
        served = None
        # This loop holds the owner's order, so it is entitled to the router's
        # withheld cooldown -- and responsible for settling it, on EVERY exit.
        config = (replace(config, owns_capacity_siblings=True)
                  if isinstance(config, ModelConfig) else config)
        try:
            while True:
                selected = self._work_candidates.next_candidate(policy)
                # Exhaustion, NOT an unbound provider: the owner's own order ran
                # out. Typed so `api/runs` can say so without matching this
                # message. The boundaries this loop validated are the evidence
                # and the last capacity failure stays the cause. Auth/unknown
                # failures never get here: they raise the held class below on
                # the attempt that saw them.
                if selected is None:
                    raise self._work_candidates.exhausted_error(boundaries) from last_capacity
                effective = {**(policy or {}), "preferred": {
                    "provider": selected.connection_id, "model_id": selected.model_id,
                }}
                observed = []
                outer = kwargs.get("response_observer")

                def observe(response):
                    observed.append(response)
                    if outer is not None:
                        outer(response)

                try:
                    attempts += 1
                    result = self._call_once(role, prompt, system, config, effective,
                                             {**kwargs, "response_observer": observe})
                except AllProvidersExhaustedError as exc:
                    refused = refusal_boundary(selected, exc.attempts)
                    if refused is not None:
                        # The source refused THIS model (403/404/410, e.g. an
                        # agentic-harness gate). A fact about one model, never
                        # the source or the account: remember it for the next
                        # run and chat turn, and step to the next model. Live
                        # 2026-10-01 (run 2a67c381980a42de) this held the run.
                        self._remember_refusal(selected, exc.attempts)
                        boundaries += (refused,)
                        last_capacity = exc
                        self._work_candidates.next_candidate(policy, (refused.exhaustion,))
                        continue
                    router = get_provider_router()
                    kind = router.selected_agent_execution_kind(selected) if router else None
                    boundary = capacity_boundary(
                        selected, exc.attempts, execution_kind=kind,
                        native_evidence=(getattr(exc, "native_evidence", ())
                                         if kind == "native_agent" else ()),
                    )
                    if boundary is None:
                        raise _held_attempt_error(role, selected, exc) from exc
                    exhaustion, narrowed = self._narrowed_exhaustion(
                        boundary, kind, narrowings, config,
                    )
                    narrowings += int(narrowed)
                    # The boundary is retained carrying the exhaustion actually
                    # RECORDED, not the one it proposed: `exhausted_error` matches
                    # evidence to exhaustion by value, so keeping the unnarrowed
                    # copy silently dropped the classified failure class and
                    # retry-after from the run's own error the moment a narrowing
                    # happened.
                    boundaries += (replace(boundary, exhaustion=exhaustion),)
                    last_capacity = exc
                    self._work_candidates.next_candidate(policy, (exhaustion,))
                    continue
                served = selected.connection_id
                self._forget_refusal(selected)
                if metadata_observer is not None:
                    metadata = {"attempts": attempts, "model": selected.model_id}
                    if len(observed) == 1:
                        from tinyassets.providers.router import ProviderRouter

                        metadata.update(ProviderRouter._call_meta(observed[0], attempts))
                        metadata["model"] = selected.model_id
                    metadata_observer(metadata)
                return result
        finally:
            # In a `finally`, because the debt does not depend on HOW the loop
            # ends. Settling it only on exhaustion left the window unapplied
            # whenever the node raised instead -- a cancellation or an authority
            # change between attempts -- and whenever a later model SUCCEEDED,
            # which is the ordinary outcome of the fix (Codex refutation R4,
            # 2026-09-30, reproduced).
            self._cool_abandoned_sources(boundaries, keeping=served)

    def _call_once(self, role, prompt, system, config, policy, kwargs):
        with self._authorize_attempt(
            role=role,
            prompt=prompt,
            system=system,
            policy=policy,
        ) as (carrier, snapshot_dir, provider):
            from tinyassets.config import load_universe_config
            from tinyassets.providers.base import ModelConfig, UniverseContext

            call_config = config
            if snapshot_dir is not None:
                if call_config is None:
                    call_config = ModelConfig()
                if not isinstance(call_config, ModelConfig):
                    raise TypeError("foreground provider config must be a ModelConfig")
                call_config = replace(
                    call_config,
                    credential_snapshot_dir=snapshot_dir,
                )
            if isinstance(call_config, ModelConfig):
                # A workflow node call: each provider confines it to the
                # owner's universe in its own way (ModelConfig.workflow_node).
                call_config = replace(call_config, workflow_node=True)
            result = self._provider_call(
                prompt,
                system,
                role=role,
                config=call_config,
                operation=RUN_GRAPH_OPERATION,
                universe_context=UniverseContext(
                    universe_dir=self._universe_dir,
                    config=load_universe_config(self._universe_dir),
                    provider_invocation=carrier,
                ),
                **kwargs,
            )
            if self._review_purpose() is not None:
                # The model answer cannot authorize an effect after a stop or
                # revocation that arrived while the review was in flight.
                self._check_agent_authority(carrier)
            return result, provider

    def __call__(
        self,
        prompt: str,
        system: str = "",
        *,
        role: str = "writer",
        **kwargs: Any,
    ) -> str:
        config = kwargs.pop("config", None)
        policy = kwargs.pop("policy", None)
        response, _provider = self._call(role, prompt, system, config, policy, kwargs)
        return response

    def call_with_policy_sync(
        self,
        role: str,
        prompt: str,
        system: str,
        policy: dict[str, Any] | None,
        config: Any = None,
        difficulty: str = "",
        **kwargs: Any,
    ) -> tuple[str, str, dict[str, Any]]:
        del difficulty
        metadata = {"authority": RUN_GRAPH_OPERATION, "attempts": 1}
        # The captured DOCUMENT, not the order: the order is built during
        # admission, which `_call` below triggers.
        if self._model_preference_data is not None:
            kwargs["_metadata_observer"] = metadata.update
        response, provider = self._call(role, prompt, system, config, policy, kwargs)
        return response, provider, metadata

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._receipt is not None:
            from tinyassets.storage.provider_work_authority import (
                SQLiteProviderWorkAuthorityStore,
            )

            SQLiteProviderWorkAuthorityStore(self._base_path).release_run_claim(
                self._receipt.receipt_id
            )


def captured_work_preference(
    base_path: str | Path, *, universe_id: str, principal_id: str,
) -> dict[str, Any] | None:
    """This run's captured model-choice document -- saved preference or not.

    `openspec/specs/agent-model-selection/spec.md` already promises that a work
    choice needs neither a change to the universe's main serving provider nor a
    rewrite of a private workflow. Only the conversation path honoured it: it
    captured this same document and built the candidate order, while every run
    session was constructed without one and fell through to the single legacy
    serving binding. The owner's selector therefore governed chat and not their
    workflows -- two definitions of one fact. This is the missing read, not a
    new authority: a preference grants nothing, and `_admit` /
    `_authorize_attempt` still decide every invocation on current authority.

    **A document is returned even with NOTHING saved** (`saved: None`), which is
    the automatic mode a chat turn uses when its owner has chosen no model.
    Returning None there left the run on the legacy pin, and the legacy pin
    resolves an unspecified node model as
    `snapshot.default_model_id or definition.model` -- the source's DECLARED
    default. Live 2026-09-30, universe `u-01ky3zh1arr8qth8jee7zx63pq`
    (runs 61184d8f21724915 / 4828ae18e2414e77): an `api_key_http` OpenRouter
    source whose declared `inclusionai/ling-3.0-flash-vl:free` had left the
    account's 632-model catalogue. The owner's chat turn ordered the fresh
    catalogue and ran; every run, automation and agent node of that universe
    pinned the vanished id and failed `authority_held`. One resolver
    (`prepare_owned_model_plan`) now answers "which model" for both surfaces, so
    they cannot disagree again.

    `current` is always None. A tab-local override belongs to an interactive
    turn; a run has no tab, so only the durable saved default and its ordered
    fallbacks apply.

    Returns None only when the scope is not nameable, or when this universe is
    no longer the principal's home. A moved home is NOT swallowed: the session's
    own `_validate_founder_home` refuses the run a step later with the failure
    class it already had, so reading a preference cannot invent a new one. A
    universe on a LEGACY (no-manifest) assignment also keeps its existing path:
    `prepare_owned_model_plan` returns no plan for one, and
    `prepare_captured_choices` passes that through as no captured choices.
    """
    from tinyassets.storage.model_preferences import (
        ModelPreferenceStore,
        PreferenceHomeChanged,
    )

    owner, universe = (principal_id or "").strip(), (universe_id or "").strip()
    if not owner or not universe:
        return None
    try:
        snapshot = ModelPreferenceStore(base_path).get(
            owner, universe, require_current_home=True,
        )
    except (PreferenceHomeChanged, ValueError):
        return None
    return {
        "version": 1,
        "saved": None if snapshot.policy is None else snapshot.policy.document(),
        "observed_generation": snapshot.generation,
        "current": None,
    }


def new_foreground_run_provider_session(
    base_path: str | Path,
    *,
    universe_id: str,
    principal_id: str,
    provider_call: Callable[..., str],
) -> _ForegroundRunProviderSession:
    return _ForegroundRunProviderSession(
        base_path,
        universe_id=universe_id,
        principal_id=principal_id,
        provider_call=provider_call,
        model_preference_data=captured_work_preference(
            base_path, universe_id=universe_id, principal_id=principal_id,
        ),
    )


# How far down a `.provider_call` chain to look for a session before giving up.
# Today's only shape is depth 1 (api/runs.py builds the wrapper directly), so
# anything deeper is an unrecognised chain, not a supported one.
_MAX_WRAPPER_DEPTH = 8


def _locate_session(
    provider_call: Any,
) -> tuple[_ForegroundRunProviderSession | None, int]:
    """The nearest nested session and the depth it was found at.

    `(None, 0)` means no session anywhere in the chain -- an ordinary provider
    call, which must pass through untouched. A depth greater than 1 means a
    session is present but WRAPPED by something this module does not know how
    to rebind; the caller refuses rather than guesses.
    """
    node = provider_call
    for depth in range(1, _MAX_WRAPPER_DEPTH + 1):
        candidate = getattr(node, "provider_call", None)
        if candidate is None:
            return None, 0
        if type(candidate) is _ForegroundRunProviderSession:
            return candidate, depth
        node = candidate
    return None, 0


def _session_from_provider_call(provider_call: Any) -> _ForegroundRunProviderSession | None:
    session, depth = _locate_session(provider_call)
    return session if depth == 1 else None


def _rebind(provider_call: Any, session: _ForegroundRunProviderSession) -> Any:
    """The same wrapper shape around a different session.

    The wrapper is a `UniverseBoundProviderCall`, which enforces one exact
    universe context and operation. The child MUST keep both -- swapping the
    session must not become a way to swap the universe binding.

    That sentence used to be a comment rather than a check. `replace()` was
    called on whatever arrived, so ANY dataclass exposing a real session as
    `.provider_call` was rebound -- and whatever `universe_context` and
    `operation` semantics that type happened to have came along with it. It
    took possession of a real session and the child still revalidated
    owner/run/branch, so it was not a demonstrated cross-tenant mint; it was
    simply an invariant the code asserted and did not enforce. Cross-family
    review 2026-08-27, finding (c).
    """
    from dataclasses import replace

    from tinyassets.providers.call import UniverseBoundProviderCall

    if type(provider_call) is not UniverseBoundProviderCall:
        raise PermissionError(
            "cannot rebind a foreground provider call of type "
            f"{type(provider_call).__name__}: only an exact "
            "UniverseBoundProviderCall carries the command center binding this "
            "rebind is required to preserve"
        )
    try:
        return replace(provider_call, provider_call=session)
    except TypeError:
        # Belt and braces: the exact-type check above should make this
        # unreachable, but a non-dataclass must never fall through to a
        # silently unbound call.
        raise PermissionError(
            "cannot rebind a foreground provider call of type "
            f"{type(provider_call).__name__}"
        ) from None


def prepare_foreground_run_provider(
    provider_call: Any,
    *,
    run_id: str,
    branch: Any,
    branch_version_id: str | None,
    allowed_statuses: set[str],
) -> Any:
    session, depth = _locate_session(provider_call)
    if session is None:
        # No session anywhere in the chain: an ordinary provider call, which
        # this function has no business touching.
        return provider_call
    if depth != 1:
        # A session IS here, but behind a wrapper chain we cannot rebind. The
        # old code returned the call unchanged, which silently handed the CHILD
        # run the PARENT's prepared session -- exactly the authority bleed the
        # sibling mint exists to prevent, reachable by adding one decorator.
        # Nothing constructs this shape today (api/runs.py builds the wrapper
        # directly); refusing keeps it that way rather than trusting it stays
        # true. Cross-family review 2026-08-27, finding (d).
        raise PermissionError(
            "foreground provider session is nested "
            f"{depth} wrappers deep in {type(provider_call).__name__}; "
            "refusing to prepare a run through an unrecognised wrapper chain"
        )

    # An async SUB-BRANCH arrives here carrying the PARENT's already-prepared
    # provider_call: `graph_compiler` passes `provider_call=provider_call`
    # straight into `execute_branch_async` for the child. That used to reach
    # `prepare()`'s "already bound" guard and refuse, so the child run was
    # created FAILED before executing a single node (cross-family review of
    # PR #2559, after it had merged and deployed; the path had no test).
    #
    # The guard stays -- one session must never serve two runs, because its
    # receipt and claim are minted against a single run id. Mint a SIBLING
    # instead, from the constructor inputs only, so the child admits on its own
    # authority and is validated against its own run row. The parent is left
    # untouched for its remaining nodes.
    bound = session.bound_run_id
    if bound and bound != run_id.strip():
        child = _ForegroundRunProviderSession(**session.constructor_inputs())
        child.prepare(
            run_id=run_id,
            branch=branch,
            branch_version_id=branch_version_id,
            allowed_statuses=allowed_statuses,
        )
        return _rebind(provider_call, child)

    session.prepare(
        run_id=run_id,
        branch=branch,
        branch_version_id=branch_version_id,
        allowed_statuses=allowed_statuses,
    )
    return provider_call


def close_foreground_run_provider(provider_call: Any) -> None:
    session = _session_from_provider_call(provider_call)
    if session is not None:
        session.close()


__all__ = [
    "RUN_GRAPH_OPERATION",
    "captured_work_preference",
    "close_foreground_run_provider",
    "new_foreground_run_provider_session",
    "prepare_foreground_run_provider",
]
