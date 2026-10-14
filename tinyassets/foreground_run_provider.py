"""Server-owned provider authority for authenticated foreground Branch runs."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
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
    ProviderUniverseWorkReceipt,
    ProviderUniverseWorkRoot,
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


def _work_invocation_allowance(snapshot, *, minimum: int, ceiling: int) -> int:
    """Reuse accepted finite authority for agent rounds, never mint per-round caps."""
    from tinyassets.shared_self import shared_self_requested

    if (type(minimum) is not int or type(ceiling) is not int
            or minimum < 1 or ceiling < minimum):
        raise PermissionError("workflow exceeds the shared invocation allowance")
    return ceiling if shared_self_requested(snapshot) else minimum


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


class _ForegroundRunProviderSession:
    def __init__(
        self,
        base_path: str | Path,
        *,
        universe_id: str,
        principal_id: str,
        provider_call: Callable[..., str],
        model_preference_data: dict | None = None,
        request_budget=None,
        request_allocation=None,
    ) -> None:
        self._base_path = Path(base_path)
        self._universe_id = universe_id.strip()
        self._universe_dir = self._base_path / self._universe_id
        self._principal_id = principal_id.strip()
        self._provider_call = provider_call
        from tinyassets.providers.model_call import open_allocation
        from tinyassets.request_budget import (
            RunRequestAllocation,
        )

        self._request_budget = request_budget
        self._owns_request_budget = self._request_budget is None
        if self._request_budget is None:
            self._request_budget = open_allocation(
                self._principal_id, self._universe_dir,
                max_cost_microunits=25_000_000,
            )
        self._request_budget.check_scope(self._principal_id, self._universe_id)
        self._request_budget.persist(self._base_path)
        if request_allocation is not None and (
            type(request_allocation) is not RunRequestAllocation
            or request_allocation.budget is not self._request_budget
        ):
            raise PermissionError("run request allocation cannot be substituted")
        self._request_allocation = request_allocation or RunRequestAllocation(
            self._request_budget, owns_budget=self._owns_request_budget,
        )
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

    def _capture_choices(self, admitted_sources: tuple[str, ...]) -> None:
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
        if self._work_candidates is not None:
            return
        from tinyassets.providers.work_candidate_data import prepare_captured_choices

        self._work_candidates = prepare_captured_choices(
            self._base_path, owner=self._principal_id, universe=self._universe_id,
            document=self._model_preference_data or {
                "version": 1, "saved": None, "observed_generation": 0, "current": None,
            }, sources=admitted_sources,
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
        """Stable provider inputs for a sibling; no admitted runtime handles.

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
        The shared request allocation is joined separately, atomically with
        checking that the parent session has not released its participation.
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
            self._request_budget.link("run", self._run_id)
        except ProviderAuthorityHeldError:
            raise
        except Exception as exc:
            raise _held_authority_error(exc) from exc

    def _admit(self) -> None:
        from tinyassets.exceptions import ProviderAuthorityHeldError
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
            roles = tuple(sorted({
                str(node.get("model_hint") or "writer").strip() or "writer" for node in nodes
            }))
            if not set(roles).issubset(_SUPPORTED_ROLES):
                raise PermissionError("foreground Branch requests an unsupported role")
            if not nodes:
                raise PermissionError("foreground provider attempt has no prompt node")
            self._validate_run(allowed_statuses={"running"})
            # AFTER every refusal this lane can decide from stored state, and
            # before the connected model plan fits the order. Capturing it is
            # credential-bearing outbound IO (a catalogue read on the owner's own
            # grant), so it must not happen for a run `_admit` was going to
            # refuse anyway. It sat above the author check, and running another
            # user's PUBLIC Branch therefore made one discovery request on the
            # requester's source before the refusal -- the same shape as the
            # sign-in refresh Codex found on #4082 and the same fix (Codex
            # refutation of this change, C2, 2026-09-29).
            from tinyassets.providers.served_model_plan import connected_sources

            admitted = tuple(sorted(connected_sources(
                self._base_path, self._universe_dir, self._principal_id,
            )))
            if not admitted:
                raise PermissionError("no connected model source for this owner")
            self._capture_choices(admitted)
            default_provider = (
                self._work_candidates.order[0].connection_id
                if self._work_candidates is not None else admitted[0]
            )
            if default_provider not in admitted:
                raise PermissionError(f"source {default_provider!r} is not connected")
            allocation = self._request_budget
            allocation_record = allocation.receipt()
            if allocation_record["closed"]:
                raise PermissionError("run allocation is closed")
            limits = allocation_record["limits"]
            max_invocations = self._work_candidates.fit(
                snapshot, ceiling=limits["max_requests"] or 1000,
                retry_multiplier=1,
            )
            max_invocations = _work_invocation_allowance(
                snapshot, minimum=max_invocations,
                ceiling=limits["max_requests"] or 1000,
            )
            max_tokens = limits["max_tokens"]
            max_cost = limits["max_cost_microunits"]
            if max_tokens < 1 or max_cost < 1:
                raise PermissionError("run allocation has no model allowance")
            subject_ref = self._branch_version_id or (
                f"{self._branch_def_id}@definition:{int(snapshot.get('version') or 1)}"
            )
            root = ProviderUniverseWorkRoot(work_item_kind="run", work_item_id=self._run_id)
            digest = _content_digest(admitted)
            store = SQLiteProviderWorkAuthorityStore(self._base_path)
            candidate = ProviderUniverseWorkReceipt(
                schema_version=5, authority_scope="manifest", admitted_sources=admitted,
                manifest_digest=digest,
                receipt_id=provider_work_receipt_id(universe_id=self._universe_id, root=root),
                receipt_digest="sha256:" + "0" * 64, generation=1,
                state=ProviderWorkReceiptState.ACTIVE, work_item_kind="run",
                work_item_id=self._run_id, binding_id=None, binding_generation=None,
                binding_digest=None, binding_revocation_generation=None,
                principal_id=self._principal_id, actor_id=f"universe:{self._universe_id}",
                universe_id=self._universe_id, branch_def_id=self._branch_def_id,
                branch_version_id=subject_ref, provider=None,
                credential_reference_digest=None, assignment_generation=1,
                assignment_digest=digest, executor_class=_admitted_cloud_class(),
                allowed_operations=(RUN_GRAPH_OPERATION,), allowed_roles=roles,
                max_invocations=max_invocations, max_tokens=max_tokens,
                max_cost_microunits=max_cost,
                expires_at=(datetime.now(timezone.utc) + timedelta(hours=1))
                .isoformat().replace("+00:00", "Z"),
                created_at=store.timestamp(),
                execution_subject=ExecutionSubject(
                    kind=ExecutionSubjectKind.BRANCH_VERSION, ref=subject_ref,
                    digest=self._branch_digest,
                ),
            )
            candidate = replace(candidate, receipt_digest=candidate.expected_digest())
            with store.connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    receipt, claim = store._admit_run_in_transaction(
                        conn, authority=candidate,
                        worker_id=f"foreground-run:{os.getpid()}",
                        runtime_id=f"run:{self._run_id}",
                        claim_nonce_digest=_content_digest([
                            self._run_id, self._principal_id, self._universe_id,
                            self._branch_digest, os.getpid(),
                        ]), lease_seconds=3600,
                    )
                    self._validate_run(allowed_statuses={"running"})
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
            self._provider, self._receipt, self._claim = default_provider, receipt, claim
        except ProviderAuthorityHeldError:
            raise
        except Exception as exc:
            raise _held_authority_error(exc) from exc

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

    def _validate_receipt_scope(self) -> None:
        receipt = self._receipt
        if receipt is None or not all((
            receipt.schema_version == 5,
            receipt.principal_id == self._principal_id,
            receipt.actor_id == f"universe:{self._universe_id}",
            receipt.universe_id == self._universe_id,
            receipt.work_item_id == self._run_id,
            receipt.branch_def_id == self._branch_def_id,
            receipt.execution_subject is not None,
            receipt.execution_subject is not None
            and receipt.execution_subject.digest == self._branch_digest,
            receipt.admitted_sources is not None,
        )):
            raise PermissionError("foreground run connected admission is stale")

    def _check_agent_authority(self, carrier: ProviderInvocationCarrier) -> str:
        """Recheck the active run and live source after a model response."""
        import hmac

        from tinyassets.provider_work_authority import _provider_invocation_carrier_seal
        from tinyassets.shared_self import shared_self_requested
        from tinyassets.storage.current_home import check_current_home
        from tinyassets.storage.provider_work_authority import (
            SQLiteProviderWorkAuthorityStore,
            _claim_record,
            _current_connected_member,
            _receipt_record,
            _reservation_record,
        )

        if (self._closed or self._receipt is None or self._claim is None
                or type(carrier) is not ProviderInvocationCarrier
                or carrier._issuer_pid != os.getpid()
                or not hmac.compare_digest(
                    carrier._seal, _provider_invocation_carrier_seal(carrier)
                )
                or carrier._receipt != self._receipt or carrier._claim != self._claim):
            raise PermissionError("work agent carrier does not match its active run")
        if (self._branch_snapshot is None
                or _content_digest(self._branch_snapshot) != self._branch_digest
                or not shared_self_requested(self._branch_snapshot)):
            raise PermissionError("work agent immutable subject changed")
        self._validate_founder_home()
        self._validate_run(allowed_statuses={"running"})
        self._validate_receipt_scope()
        store = SQLiteProviderWorkAuthorityStore(self._base_path)
        with store.connection() as conn:
            conn.execute("BEGIN")
            check_current_home(conn, self._principal_id, self._universe_id)
            receipt_row = conn.execute(
                "SELECT * FROM provider_work_receipts WHERE receipt_id=?",
                (carrier.work_receipt_id,),
            ).fetchone()
            claim_row = conn.execute(
                "SELECT * FROM provider_work_execution_claims WHERE claim_id=?",
                (self._claim.claim_id,),
            ).fetchone()
            reservation_row = conn.execute(
                "SELECT * FROM provider_invocation_reservations WHERE reservation_id=?",
                (carrier.reservation_id,),
            ).fetchone()
            if receipt_row is None or claim_row is None or reservation_row is None:
                raise PermissionError("work agent progress authority is unavailable")
            receipt, claim = _receipt_record(receipt_row), _claim_record(claim_row)
            reservation = _reservation_record(reservation_row)
            completed_states = {"launch_started", "succeeded"}
            if not all((
                receipt == self._receipt, claim == self._claim,
                receipt.state is ProviderWorkReceiptState.ACTIVE,
                claim.state.value == "active",
                datetime.fromisoformat(receipt.expires_at.replace("Z", "+00:00")) > store._now(),
                datetime.fromisoformat(
                    claim.lease_expires_at.replace("Z", "+00:00")
                ) > store._now(),
                reservation.receipt_id == receipt.receipt_id,
                reservation.receipt_digest == receipt.receipt_digest,
                reservation.claim_id == claim.claim_id,
                reservation.claim_digest == claim.claim_digest,
                reservation.claim_generation == claim.generation,
                reservation.selection == carrier._reservation.selection,
                reservation.operation == carrier.operation == RUN_GRAPH_OPERATION,
                reservation.role == carrier.role == "writer",
                reservation.state.value in completed_states,
            )):
                raise PermissionError("work agent receipt, claim or invocation changed")
            _current_connected_member(conn, self._base_path, receipt, reservation.selection)
        self._validate_run(allowed_statuses={"running"})
        return self._principal_id

    @contextmanager
    def _authorize_attempt(
        self, *, role: str, prompt: str, system: str,
        policy: dict[str, Any] | None,
    ) -> Iterator[tuple[ProviderInvocationCarrier, Path | None, str]]:
        from tinyassets.credential_vault import (
            cleanup_llm_credential_snapshot,
            snapshot_llm_subscription_credential,
        )
        from tinyassets.exceptions import ProviderAuthorityHeldError
        from tinyassets.provider_serving_binding import _is_open_provider
        from tinyassets.providers.work_model_selection import prepare_work_model_snapshot
        from tinyassets.shared_self import shared_self_requested
        from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore

        _admitted_cloud_class()
        snapshot = None
        carrier = None
        try:
            if self._closed or self._receipt is None or self._claim is None:
                raise PermissionError("foreground run provider session is not active")
            if role not in self._receipt.allowed_roles:
                raise PermissionError("foreground run provider role is not authorized")
            self._validate_founder_home()
            self._validate_receipt_scope()
            if (self._branch_snapshot is None
                    or _content_digest(self._branch_snapshot) != self._branch_digest):
                raise PermissionError("foreground immutable Branch subject changed")
            self._validate_run(allowed_statuses={"running"})
            with self._lock:
                self._call_index += 1
                invocation_index = self._call_index
            prompt_digest = _content_digest([
                role, prompt, system, "prompt_node",
            ])
            invocation_key = (
                f"run:{self._run_id}:{invocation_index}:{prompt_digest.removeprefix('sha256:')}"
            )
            preferred = (policy or {}).get("preferred", {})
            if not isinstance(preferred, dict):
                raise PermissionError("workflow model preference is invalid")
            provider = preferred.get("provider") or self._provider
            model_id = preferred.get("model_id", preferred.get("model", ""))
            if ("model_id" in preferred and "model" in preferred
                    and preferred["model_id"] != preferred["model"]):
                raise PermissionError("workflow model preference is conflicting")
            if provider not in self._receipt.admitted_sources:
                raise PermissionError(f"source {provider!r} was not admitted for this run")
            model_snapshot = prepare_work_model_snapshot(
                base_path=self._base_path, universe_id=self._universe_id,
                owner_user_id=self._principal_id, provider=provider, model_id=model_id,
            )
            selection = ProviderInvocationSelection.connected(provider, model_id)
            store = SQLiteProviderWorkAuthorityStore(self._base_path)
            with store.connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    from tinyassets.storage.provider_work_authority import _current_connected_member

                    custody = _current_connected_member(
                        conn, self._base_path, self._receipt, selection,
                    )
                    shares = len(_prompt_nodes(self._branch_snapshot))
                    token_share = max(1, self._receipt.max_tokens // shares)
                    cost_share = max(1, self._receipt.max_cost_microunits // shares)
                    carrier = store._reserve_and_arm_run_carrier_in_transaction(
                        conn, receipt=self._receipt, claim=self._claim,
                        invocation_key=invocation_key, role=role,
                        max_tokens=token_share, max_cost_microunits=cost_share,
                        selection=selection, model_snapshot=model_snapshot,
                        needs_tools=shared_self_requested(self._branch_snapshot),
                    )
                    if not _is_open_provider(provider):
                        snapshot = snapshot_llm_subscription_credential(
                            universe_dir=self._universe_dir, custody=custody,
                        )
                    self._validate_run(allowed_statuses={"running"})
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
            yield carrier, snapshot.directory if snapshot is not None else None, provider
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

        if self._branch_snapshot is not None and not _prompt_nodes(self._branch_snapshot):
            raise _held_authority_error(
                PermissionError("foreground provider attempt has no prompt node")
            )

        if config is None:
            config = ModelConfig()
        if isinstance(config, ModelConfig):
            if config.request_budget not in (None, self._request_budget):
                raise PermissionError("foreground parent request budget cannot be substituted")
            config = replace(config, request_budget=self._request_budget,
                             request_purpose="helper")

        # Enforcement site (C), at the call boundary.
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
        # Every prompt step funnels through `_call`. Outside every transaction —
        # `_admit()` opens its own below — so this may resolve; the two
        # in-transaction stamp sites stay cached-only.
        call_provenance = resolve_process_cloud_admission()
        if not call_provenance.is_cloud:
            raise PermissionError(
                platform_not_cloud_message(
                    call_provenance, surface="foreground run provider call"
                )
            )

        from tinyassets.providers.model_call import require_supported_policy

        require_supported_policy(policy)
        self._ensure_admitted()
        from tinyassets.shared_self import agent_node

        node = agent_node(
            self._branch_snapshot, getattr(config, "agent_node_id", ""), self._principal_id,
            node_key=getattr(config, "agent_node_key", ""),
        )
        if node is not None:
            from tinyassets import activity_runner

            # A run of the universe's Activities branch continues the activity
            # whose record names this run -- found by run id, never from inputs
            # -- and an unlinked one is refused here, before the agent acts.
            activity = (
                activity_runner.linked_activity(
                    self._base_path, self._universe_id, self._run_id)
                if activity_runner.is_activities_branch(
                    self._branch_snapshot, self._universe_id)
                else None
            )
            from tinyassets.control_plane import session

            response_observer = kwargs.pop("response_observer", None)
            metadata_observer = kwargs.pop("_metadata_observer", None)
            if role != "writer" or kwargs:
                raise PermissionError("workflow agent call cannot substitute execution context")
            # The one starter (S2): an agent node is the main agent; an
            # activity's run is the agent its record names.
            return session.start(
                activity["agent_id"] if activity is not None else "main", prompt,
                session.RunStep(
                    session=self, prompt_config=config, policy=policy, node=node,
                    activity=activity, response_observer=response_observer,
                    metadata_observer=metadata_observer,
                ),
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
                router.cool_source(connection, owner=self._principal_id,
                                   retry_after_s=boundary.retry_after_s,
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
        from tinyassets.request_budget import RequestBudgetExceeded, candidate_is_metered_free

        attempts = 0
        boundaries = ()
        narrowings = 0
        last_capacity = None
        served = None
        local_exclusions = set()
        last_budget_stop = None
        # This loop holds the owner's order, so it is entitled to the router's
        # withheld cooldown -- and responsible for settling it, on EVERY exit.
        config = (replace(config, owns_capacity_siblings=True)
                  if isinstance(config, ModelConfig) else config)
        try:
            while True:
                selected = self._work_candidates.next_candidate(
                    policy, local_exclusions=local_exclusions,
                )
                # Exhaustion, NOT an unbound provider: the owner's own order ran
                # out. Typed so `api/runs` can say so without matching this
                # message. The boundaries this loop validated are the evidence
                # and the last capacity failure stays the cause. Auth/unknown
                # failures never get here: they raise the held class below on
                # the attempt that saw them.
                if selected is None:
                    if last_budget_stop is not None:
                        raise last_budget_stop
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
                    from tinyassets.providers.base import UniverseContext

                    # A known local stop must not consume a durable invocation
                    # reservation.
                    accounting_context = UniverseContext(
                        universe_dir=self._universe_dir, config=None, model_selection=selected,
                    )
                    self._request_budget.check_available(
                        source_ref=selected.connection_id,
                        free=candidate_is_metered_free(
                            accounting_context, self._work_candidates.catalog,
                            owner=self._principal_id,
                        ),
                        purpose=config.request_purpose,
                    )
                    attempts += 1
                    result = self._call_once(role, prompt, system, config, effective,
                                             {**kwargs, "response_observer": observe})
                except RequestBudgetExceeded as exc:
                    if exc.reason not in RequestBudgetExceeded.SOURCE_LIMIT_REASONS:
                        raise
                    last_budget_stop = exc
                    local_exclusions.add(selected)
                    continue
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
            from tinyassets.providers.base import UniverseContext

            call_config = config or ModelConfig()
            if not isinstance(call_config, ModelConfig):
                raise TypeError("foreground provider config must be a ModelConfig")
            if call_config.request_budget not in (None, self._request_budget):
                raise PermissionError("foreground parent request budget cannot be substituted")
            # Agent preparation may return a fresh config. Rejoin the run's
            # durable allocation at the last boundary before transport.
            call_config = replace(
                call_config, request_budget=self._request_budget,
                credential_snapshot_dir=snapshot_dir or call_config.credential_snapshot_dir,
                workflow_node=True,
                model_launch_parent=("run", self._run_id,
                                     carrier.work_receipt_id, None)
                if carrier is not None else None,
            )
            result = self._provider_call(
                prompt,
                system,
                role=role,
                config=call_config,
                operation=RUN_GRAPH_OPERATION,
                policy=policy,
                universe_context=UniverseContext(
                    universe_dir=self._universe_dir,
                    config=load_universe_config(self._universe_dir),
                    provider_invocation=carrier,
                ),
                **kwargs,
            )
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

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._request_allocation.close()
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


def _locate_session(
    provider_call: Any,
) -> tuple[_ForegroundRunProviderSession | None, int]:
    """Detect a session even behind an unsupported wrapper, so it is refused."""
    from tinyassets.providers.model_call import BoundModelCall

    node = provider_call
    for depth in range(1, 9):
        candidate = (node.runner if type(node) is BoundModelCall
                     else getattr(node, "provider_call", None))
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

    The wrapper is a `BoundModelCall`, which enforces one exact
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

    from tinyassets.providers.model_call import BoundModelCall

    if type(provider_call) is not BoundModelCall:
        raise PermissionError(
            "cannot rebind a foreground provider call of type "
            f"{type(provider_call).__name__}: only an exact "
            "BoundModelCall carries the command center binding this "
            "rebind is required to preserve"
        )
    try:
        return replace(provider_call, runner=session)
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
        allocation = session._request_allocation.child()
        try:
            child = _ForegroundRunProviderSession(
                **session.constructor_inputs(), request_budget=allocation.budget,
                request_allocation=allocation,
            )
            child.prepare(
                run_id=run_id,
                branch=branch,
                branch_version_id=branch_version_id,
                allowed_statuses=allowed_statuses,
            )
        except BaseException:
            allocation.close()
            raise
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
