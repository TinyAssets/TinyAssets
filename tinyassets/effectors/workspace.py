"""The ``workspace`` effect sink: create, check out, push and discard a repository.

Every git operation -- and every directory a run ends up holding -- happens in
the OWNER's own cell (``workspace_worker`` -> ``role_remote_git``), because
only that owner can make a directory a node sandbox will mount and only the
credential broker ever holds the token. This adapter is the daemon side and
does the parts that are the daemon's: it checks the connection grant's scope
and the typed consent, admits the job against the pool, asks the cell to
perform exactly one operation inside the lease the pool admitted, then opens
that lease's descriptors and publishes the capability.

Nothing here spawns git, stages a clone or resolves a credential; a host path
never crosses into the cell, and the lease is named relative to the command
center.

Design D0/D1/D4/D5/D6 of the ``workspace-node`` change, as rebuilt by the
per-owner isolation cutover. Never raises: every refusal is a secret-free
evidence dict carrying one actionable ``error_kind``.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import secrets
from pathlib import Path
from typing import Any, Callable

from tinyassets.storage.workspace_authority import (
    CONSENT_CHECKOUT,
    CONSENT_PROVISION,
    CONSENT_PUSH,
    WORKSPACE_SINK,
    GitScopeError,
    connection_access_mode,
    connection_git_host,
    connection_hosts,
    has_git_scope,
    normalize_repo,
    workspace_consent_destination,
)
from tinyassets.workspace_intents import record_push_intent, settle_push_intent
from tinyassets.workspace_pool import AdmissionObservation

logger = logging.getLogger(__name__)

#: One path-safe branch segment. The remote ref is built, never taken: a slug
#: with a slash, a space or a leading dash would name a different branch than
#: the packet appears to.
_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")

#: The sink name. One spelling, owned by the authority module, so the rail that
#: WRITES a consent and the sink that READS it cannot drift apart.
EXTERNAL_WRITE_SINK_WORKSPACE = WORKSPACE_SINK

#: The operations the sink offers, and the consent each one needs.
#:
#: ``create`` needs none, and takes no connection at all: it makes an EMPTY
#: directory in the universe's own storage. There is no credential to authorize
#: and no far side to reach, so the gates that exist for a credentialed clone
#: would be asking the owner to approve their own scratch space. It is bounded
#: by exactly the same lease, pool, quota and hourly-job machinery.
_CONSENT_FOR_OP = {
    "create": "",  # the universe's own space: nothing to authorize
    "checkout": CONSENT_CHECKOUT,
    "push": CONSENT_PUSH,
    "discard": "",  # discarding what you already hold needs no new consent
}

#: The operations that need a credentialed connection. Everything else runs on
#: the universe's own storage with no authority to check.
_CONNECTED_OPS = frozenset({"checkout", "push"})

#: The connection scope each operation requires, bound to (host, owner/name).
_SCOPE_FOR_OP = {"checkout": "git_read", "push": "git_write"}

#: (sink, verb) pairs that could NOT have changed the far side. Only a
#: checkout: it reads a repository and changes nothing anywhere.
#:
#: ``discard`` is deliberately NOT here (Codex round 2, #12). It destroys a
#: generation and enqueues an irreversible wipe -- local, but a change, and the
#: settlement asks "could this run have changed anything", not "did it touch
#: the network".
#: ``create`` joins it: an empty directory in this universe's own storage
#: reaches nothing and changes nothing anywhere.
WORKSPACE_READ_EFFECTS = frozenset(
    {
        (EXTERNAL_WRITE_SINK_WORKSPACE, "checkout"),
        (EXTERNAL_WRITE_SINK_WORKSPACE, "create"),
    }
)

_MAX_BUNDLE_BYTES = 512 * 1024 * 1024
#: What one checkout may move before the pool refuses it (D4's lease bound).
_DEFAULT_MAX_CHECKOUT_BYTES = 4 * 1024 * 1024 * 1024
#: The one directory inside a lease that becomes ``/workspace``. ONE name for
#: every operation: the lease layout is what the pool wipes, the outbox
#: reclaims and the compiler binds, and a second spelling would be a second
#: shape for all three to know about. Named for its first use.
_CONTENT_DIR = "repo"


class _Refused(Exception):
    """An operation refused with one actionable kind. Never leaves this module."""

    def __init__(self, kind: str, error: str, **extra: Any) -> None:
        self.kind = kind
        self.error = error
        self.extra = extra
        super().__init__(error)


# --------------------------------------------------------------------------- #
# Packet
# --------------------------------------------------------------------------- #


def _parse_packet(value: Any) -> dict[str, Any] | None:
    """Return the packet iff ``value`` is a workspace packet, else None."""
    if isinstance(value, dict):
        packet = value
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped or not stripped.startswith("{"):
            return None
        try:
            packet = json.loads(stripped)
        except (TypeError, ValueError):
            return None
        if not isinstance(packet, dict):
            return None
    else:
        return None
    if packet.get("sink") != EXTERNAL_WRITE_SINK_WORKSPACE:
        return None
    return packet


def _find_packet(
    *, output_keys: list[str], run_state: dict[str, Any]
) -> tuple[str | None, dict[str, Any] | None]:
    for key in output_keys or []:
        if not isinstance(key, str) or key not in run_state:
            continue
        packet = _parse_packet(run_state.get(key))
        if packet is not None:
            return key, packet
    return None, None


def packet_op(*, output_keys: list[str], run_state: dict[str, Any]) -> str | None:
    """The op a node's workspace packet declares, for the dispatcher's
    classification of an effect refused before the wire."""
    _key, packet = _find_packet(output_keys=output_keys, run_state=run_state)
    if packet is None:
        return None
    op = _str_field(packet, "op")
    return op or None


def _str_field(source: Any, key: str) -> str:
    if not isinstance(source, dict):
        return ""
    value = source.get(key)
    return value.strip() if isinstance(value, str) else ""


def _split_repo(repo: str) -> tuple[str, str]:
    """``owner/name`` -> the two halves, through the authority module's grammar.

    ``normalize_repo`` is the ONE parser. A second reading of what counts as a
    repository is how a scope bound to one repo comes to cover another, so this
    module does not have its own.
    """
    try:
        normalized = normalize_repo(repo)
    except GitScopeError as exc:
        raise _Refused("invalid_packet", f"packet.repo is not 'owner/name': {exc}") from None
    owner, _, name = normalized.partition("/")
    return owner, name


def repo_key_for(host: str, owner: str, name: str) -> str:
    """The pool's path-safe key for one repository."""
    return f"{host}--{owner}--{name}".replace("/", "-")


#: A create workspace's name: one component, no ``--``, so a created workspace
#: and a checked-out repository can never share a pool key. ``repo_key_for``
#: always joins with ``--``; this never contains one, and that is the whole
#: separation.
_WORKSPACE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

#: The key every unnamed scratch workspace shares. Scratch leases are keyed by
#: lease id on disk, so this only groups them in the pool's ledger.
DEFAULT_WORKSPACE_KEY = "scratch"


def workspace_key_for(slug: str) -> str:
    """The pool key for a CREATED workspace, validated.

    ``ws.<slug>``. Kept in a namespace of its own so that naming a workspace
    ``owner/name`` cannot address, replace or discard the generation a real
    checkout of that repository published.
    """
    text = (slug or "").strip()
    if not _WORKSPACE_KEY_RE.match(text) or "--" in text:
        raise _Refused(
            "invalid_packet",
            "packet.workspace_key must be 1-64 chars of [A-Za-z0-9._-], start "
            "alphanumeric and contain no '--'",
        )
    return f"ws.{text}"


def scratch_pool_root(universe_dir: Path) -> Path:
    """The scratch pool: ``<center>/workspaces/scratch``, INSIDE the center.

    It used to be ``<data>/scratch``, shared between every command center. The
    owner split ended that: a node sandbox mounts a workspace only when the
    directory is the owner's own, only the owner's cell can create such a
    directory, and a cell is bound to its command center and nothing above it.
    So the pool moved in, beside the permanent generations it has always sat
    next to (``workspaces/<repo-key>/<gen>``) -- a repository key always
    carries ``--`` and a created workspace's key always starts ``ws.``, so the
    name ``scratch`` can never be either.

    Exported because the pool ADMITS against this path and the cell CREATES
    against it; two spellings of one directory is a lease admitted in one place
    and written in another.
    """
    from tinyassets.workspace_owner_pool import SCRATCH_DIR
    from tinyassets.workspace_pool import WORKSPACES_DIR

    return Path(universe_dir) / WORKSPACES_DIR / SCRATCH_DIR


def universe_workspace_root(universe_dir: Path) -> Path:
    """The universe root the pool derives permanent paths FROM.

    ``workspace_pool.universe_paths`` appends ``workspaces/<repo-key>/<gen>``
    itself, so this is the universe directory -- passing
    ``<universe>/workspaces`` here produced ``workspaces/workspaces/...``.
    """
    return Path(universe_dir)


# --------------------------------------------------------------------------- #
# Authority
# --------------------------------------------------------------------------- #


def _universe_id(base_path: str | Path | None) -> str:
    if base_path is None:
        return ""
    try:
        return Path(base_path).name.strip()
    except (TypeError, ValueError):
        return ""


def _data_root(base_path: str | Path | None) -> Path | None:
    """The data root (``base_path.parent``); the broker's tree lives under it."""
    if base_path is None:
        return None
    try:
        return Path(base_path).parent
    except (TypeError, ValueError):
        return None


def _universe_short(universe_id: str) -> str:
    """The branch-name component for a universe. Path-safe by construction."""
    cleaned = "".join(c for c in universe_id if c.isalnum() or c in "-_")
    return (cleaned[:12] or "universe").lower()


def _read_connection(
    *, data_root: Path, connection_id: str, universe_id: str, grant_id: str,
    principal: str = "",
) -> tuple[Any, Any]:
    """The grant and the trusted connection resource, or a refusal.

    Mirrors ``authenticated_external_call``'s isolation gate exactly, because it
    is the same broker transaction: the grant must exist, be live, be owned by
    ``principal`` and be bound to the RUNNING universe. The resource (not the
    redacted view) is needed for the credential REFERENCE -- never the secret,
    which only the broker process resolves.
    """
    from tinyassets.broker.ledger_queries import authorized_connection
    from tinyassets.storage.outbound_connections import (
        GrantResolutionError,
        ProxyRequestError,
    )

    if not principal:
        raise _Refused("no_universe_authority", "workspace requires an admitted owner")
    try:
        grant, resource, _ = authorized_connection(
            Path(data_root), principal=principal, command_center=universe_id,
            grant_id=grant_id, connection_id=connection_id,
        )
    except GrantResolutionError:
        raise _Refused(
            "connection_authority_unavailable", "connection authority refused",
        ) from None
    except ProxyRequestError:
        raise _Refused("broker_unavailable", "credential broker unavailable") from None
    return grant, resource


def transport_host_for(resource: Any) -> str:
    """The git host this connection may reach, from the STORED connection.

    Never from the packet. The packet used to supply ``host`` while the scope
    check ignored it, so a packet could point a scoped credential at a host the
    owner never allowlisted (Codex round 3, P0 #1).

    WHICH host is the connection's business, not the platform's: its declared
    ``git_host`` when the owner set one, otherwise its one endpoint host. There
    is no per-service default. ``connection_git_host`` owns that derivation and
    every other surface reads it from there, so the consent the rail writes and
    the transport the sink builds cannot name two different hosts.
    """
    host = connection_git_host(resource)
    if not host:
        several = len({h for h in connection_hosts(resource) if h}) > 1
        raise _Refused(
            "host_not_allowlisted",
            "the connection declares several hosts and no git_host; a git "
            "transport needs exactly one"
            if several
            else "the connection declares no host a git scope may reach",
        )
    return host


def _require_packet_host_agrees(packet: dict[str, Any], host: str) -> None:
    """A packet may restate the derived host, never choose a different one."""
    stated = _str_field(packet, "host")
    if stated and stated.lower() != host.lower():
        raise _Refused(
            "invalid_packet",
            "packet.host names a different host than the connection allows",
        )


def _require_scope(resource: Any, op: str, host: str, repo: str) -> None:
    """The connection must carry the op's git scope bound to this repository.

    ``has_git_scope`` owns the grammar (``git_read:owner/name``), the exact
    repo binding, the host check and the revoked-connection rule. This module
    does not re-implement any of it: two readings of one scope string is how a
    scope silently widens.
    """
    needed = _SCOPE_FOR_OP.get(op, "")
    if not needed:
        return
    if not has_git_scope(resource, needed, repo):
        raise _Refused(
            "scope_not_granted",
            f"the connection does not carry {needed} for this repository",
        )
    del host


def _consent_destination(consent: str, repo: str, connection_id: str, host: str) -> str:
    """The consent key, built by the authority module and never here.

    The key is `(operation, connection, repo)`: the same repository through a
    DIFFERENT connection is a different consent, because the credential behind
    it is different.
    """
    return workspace_consent_destination(
        consent, repo, connection_id=connection_id, host=host
    )


def _require_consent(
    universe_dir: Path,
    op: str,
    host: str,
    repo: str,
    connection_id: str,
    access_mode: str = "exact",
) -> None:
    consent = _CONSENT_FOR_OP.get(op, "")
    if not consent:
        return
    if str(access_mode).strip().lower() == "full":
        # The owner granted the CHANNEL, so every repository this key reaches on
        # this connection's git host is consented, and no per-repository row is
        # needed (full-channel-access D3.3). The connection and host checks that
        # got us here stay exact: the caller already proved the connection is
        # live, bound to this universe, and pointed at this host.
        return
    try:
        destination = _consent_destination(consent, repo, connection_id, host)
    except GitScopeError as exc:
        raise _Refused("invalid_packet", f"consent destination could not be built: {exc}")
    try:
        from tinyassets.storage.effector_consents import is_consent_active

        active = is_consent_active(
            universe_dir, sink=WORKSPACE_SINK, destination=destination
        )
    except Exception:
        logger.exception("workspace consent lookup crashed")
        active = False
    if not active:
        raise _Refused(
            "missing_consent",
            f"no active {consent} consent for {destination}",
            destination=destination,
            consent=consent,
        )


def _check_provision_consent(
    universe_dir: Path,
    host: str,
    repo: str,
    connection_id: str,
    access_mode: str = "exact",
) -> bool:
    """Provisioning is separately consented; its absence is NOT a checkout
    failure (D-spec scenario: the checkout completes, provisioning does not).

    A ``full`` channel pre-authorizes it. The caller obtains access_mode from
    the resolved connection record, never the workflow packet."""
    if str(access_mode).strip().lower() == "full":
        return True
    try:
        from tinyassets.storage.effector_consents import is_consent_active

        return is_consent_active(
            universe_dir,
            sink=WORKSPACE_SINK,
            destination=_consent_destination(CONSENT_PROVISION, repo, connection_id, host),
        )
    except Exception:
        logger.exception("workspace provision consent lookup crashed")
        return False


# --------------------------------------------------------------------------- #
# Filesystem seams (the pool lane owns these; imported lazily so this module
# does not break while that branch is unmerged, and so tests can inject)
# --------------------------------------------------------------------------- #


def _fs():
    from tinyassets import workspace_fs

    return workspace_fs


def _close_handles(*handles: Any) -> None:
    """Close whatever descriptors these are, once, never raising.

    Every failure path after a handle is opened comes through here: a lease
    whose descriptors stay open is a lease the outbox cannot reclaim.
    """
    for handle in handles:
        if isinstance(handle, int) and not isinstance(handle, bool):
            try:
                os.close(handle)
            except OSError:
                pass


def _posix_only(exc: NotImplementedError) -> _Refused:
    """The no-follow layer refuses off POSIX; say so as a workspace refusal.

    It is a real, permanent property of the host -- not a crash. Letting a
    ``NotImplementedError`` reach the dispatcher reports ``effector_crashed``,
    which reads as a bug in the sink rather than "this host cannot run
    workspaces at all".
    """
    return _Refused(
        "workspace_checkout_failed",
        f"the workspace sink needs POSIX openat semantics; this host is "
        f"{os.name!r} ({exc})",
    )


@contextlib.contextmanager
def _fs_refusals(what: str):
    """No-follow layer failures while *what* are refusals, never crashes.

    The layer refuses by RAISING: ``UnsafePoolPath`` (an ``OSError``) for a
    link, a traversal, a name it will not create or a directory that is not the
    one it made, and ``ValueError`` for a name that is not one safe component.
    Every one of those means "this run cannot have a workspace", which the
    contract already has a recoverable code for. Letting them reach the
    dispatcher reports ``effector_crashed``, which tells the graph author their
    sink is broken when in fact their checkout was refused -- and that is how
    the generation-directory bug surfaced on Ubuntu CI (run 33355481278).

    ``NotImplementedError`` keeps its own mapping: that one says the HOST
    cannot run workspaces at all, which is a different sentence to write.
    """
    try:
        yield
    except _Refused:
        raise
    except NotImplementedError as exc:
        raise _posix_only(exc) from None
    except (OSError, ValueError) as exc:
        raise _Refused(
            "workspace_checkout_failed",
            f"the workspace directory could not be created ({what}): "
            f"{type(exc).__name__}: {exc}",
        ) from None


def _lease_names(base_path: Path, lease_path: Path) -> tuple[list[str], str]:
    """``(parent components, lease name)`` relative to the command center.

    The ONE place the pool's absolute lease path becomes the relative name the
    cell is given. The cell is bound to the command center and resolves these
    components through its own descriptors, so a host path never crosses.
    """
    try:
        relative = Path(lease_path).relative_to(base_path)
    except ValueError:
        raise _Refused(
            "workspace_checkout_failed",
            "the pool admitted a lease outside this command center",
        ) from None
    parts = relative.parts
    if len(parts) < 2 or any(part in ("", ".", "..") for part in parts):
        raise _Refused(
            "workspace_checkout_failed", "the pool admitted a lease with no owner subtree",
        )
    return list(parts[:-1]), parts[-1]


def _open_cell_lease(lease_path: Path) -> tuple[Any, Any]:
    """Open the lease the OWNER CELL created, and its content directory.

    The daemon no longer creates either: only the owner's cell can make a
    directory the owner owns, and a node sandbox mounts nothing else. What the
    daemon still does is hold the descriptors -- the capability the effect
    chain hands to a node is a dup of these, so a discard racing a run closes
    the originals rather than letting a path be re-resolved.
    """
    fs = _fs()
    with _fs_refusals("opening the lease the owner cell created"):
        lease_fd = fs.open_dir_nofollow(str(lease_path))
    try:
        with _fs_refusals(f"opening {_CONTENT_DIR!r} in the lease"):
            return lease_fd, fs.open_subdir_nofollow(lease_fd, _CONTENT_DIR)
    except BaseException:
        _close_handles(lease_fd)
        raise


def _operation_id(run_id: str, node_id: str, op: str) -> str:
    """A DETERMINISTIC id for one ledger operation.

    Deterministic so a retried push charges the hour once: the pool's
    reservation is idempotent per operation id, and a random one would let two
    attempts of the same node bill twice.
    """
    return f"{run_id}:{node_id}:{op}"


def _reserve_operation(
    base_path: Path,
    *,
    universe_id: str,
    run_id: str,
    operation_id: str,
    max_bytes: int,
    refusal: str,
) -> None:
    """Charge the hourly ledger BEFORE the operation moves anything.

    A push and a discard hold no lease, so nothing else charges for them; an
    operation that cannot be admitted must not proceed.
    """
    from tinyassets import workspace_pool

    try:
        workspace_pool.reserve_operation_bytes(
            _pool_db(base_path),
            universe_id=universe_id,
            run_id=str(run_id),
            operation_id=operation_id,
            max_bytes=int(max_bytes),
        )
    except Exception as exc:
        kind = _pool_error_kind(exc)
        raise _Refused(
            kind if kind in _POOL_KINDS else refusal,
            f"the operation was not admitted: {_pool_detail(exc)}",
        ) from None


def _reconcile_operation(base_path: Path, operation_id: str, measured: int) -> None:
    """Settle the reservation down to what actually moved. Never raises: an
    unreconciled operation keeps its maximum, which is the strict side."""
    from tinyassets import workspace_pool

    try:
        workspace_pool.reconcile_operation_bytes(
            _pool_db(base_path), operation_id, max(0, int(measured))
        )
    except Exception:
        logger.exception("could not reconcile operation %s", operation_id)


@contextlib.contextmanager
def _acquired(chain: Any, node_key: str):
    """Hold the capability for the length of ONE use.

    ``acquire_workspace`` hands back a mount holding ``os.dup``s of the
    descriptors, which is what makes this worth doing: a parallel ``discard``
    closes the ORIGINALS, the next checkout opens directories and gets the same
    fd numbers back, and a holder still using them would be reading another
    branch's repository. A dup cannot be reused while it is held.

    ``None`` means never delivered or already revoked, decided inside the
    chain's lock so an acquisition cannot straddle a revoke.
    """
    acquired = chain.acquire_workspace(node_key)
    if acquired is None:
        raise _Refused(
            "no_matching_packet",
            f"the workspace '{node_key}' was revoked before this operation",
        )
    try:
        yield acquired.mount
    finally:
        acquired.release()


def _owe_wipe(base_path: Path, lease: Any, *, run_id: str, universe_id: str) -> None:
    """Enqueue the lease for wipe after a checkout failed past admission.

    Never raises: this runs on a failure path, and losing the ORIGINAL refusal
    to a bookkeeping error would report the wrong problem.
    """
    from tinyassets import workspace_pool

    try:
        conn = workspace_pool._connect(_pool_db(base_path))
        try:
            conn.execute("BEGIN IMMEDIATE")
            workspace_pool.enqueue_discard(
                conn,
                run_id=str(run_id),
                universe_id=universe_id,
                storage_class=getattr(lease, "storage_class", "scratch"),
                lease=lease,
                repo_key=getattr(lease, "repo_key", None),
                generation=getattr(lease, "generation", None),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.exception("could not enqueue the failed checkout's lease for wipe")


# --------------------------------------------------------------------------- #
# Operations
# --------------------------------------------------------------------------- #


def _descriptor_or_none(handle: Any) -> int | None:
    """A real POSIX descriptor, or None.

    The lease handles are ints on Linux, which is where this runs. On a
    non-POSIX dev host there is no descriptor to bind, and the mount falls back
    to the path there. That is a dev-host difference, stated rather than
    hidden: production is Linux and always takes the descriptor.
    """
    if os.name != "posix":
        return None
    return handle if isinstance(handle, int) and not isinstance(handle, bool) else None


def _agent_for(run_id: str, node_id: str) -> str:
    """Who the broker records as acting, for one workspace operation.

    The route's authority check wants an acting agent, and a run's identity is
    the honest one here: the node that declared the effect, in the run that
    drove it. Digested so an arbitrary graph node id cannot shape the field.
    """
    digest = hashlib.sha256(f"{run_id}\0{node_id}".encode()).hexdigest()[:16]
    return f"workspace-run-{digest}"


def _pool_db(base_path: Path) -> Path:
    from tinyassets import runs

    return runs.runs_db_path(base_path)


def _provision_checkout(
    requested: Any, *, base_path: Path, resource: Any, host: str, repo: str,
    lease: Any, lease_fd: int, repo_fd: int, run_id: str, node_id: str,
    universe_id: str, timeout_seconds: float, should_cancel: Callable[[], bool] | None,
    principal: str,
) -> dict[str, Any]:
    """Consent -> complete manifest admission -> fresh reservation -> execution.

    Refused admission preserves an unmodified checkout. Once execution starts,
    failure prevents publication and the checkout owner owes its lease a wipe.
    """
    from tinyassets.workspace_provision import ProvisionRefused
    from tinyassets.workspace_provision_execution import execute_provision
    from tinyassets.workspace_resolver import read_provision_manifests

    def refuse(reason: str, detail: str) -> dict[str, Any]:
        return {"provision": "workspace_provision_refused", "provision_reason": reason,
                "provision_detail": detail}

    if not _check_provision_consent(
        base_path, host, repo, str(getattr(resource, "connection_id", "")),
        access_mode=connection_access_mode(resource),
    ):
        return refuse("missing_consent", "workspace_provision consent is required")
    if (type(requested) is not dict or set(requested) - {"python", "node"}
            or ("python" in requested and (type(requested["python"]) is not str
                                           or not requested["python"]))
            or ("node" in requested and type(requested["node"]) is not bool)
            or not (requested.get("python") or requested.get("node"))):
        return refuse("invalid_request", "provision requires a Python manifest path or node: true")
    try:
        manifests = read_provision_manifests(
            repo_fd, python_path=requested.get("python"), node=requested.get("node", False))
    except ProvisionRefused as exc:
        evidence = refuse(exc.reason, "dependency manifest is outside the supported grammar")
        if exc.line_no is not None:
            evidence["provision_line"] = exc.line_no
        return evidence

    # Every actual acquisition attempt needs its own maximum reservation. A
    # deterministic retried id may have already been reconciled DOWN and cannot
    # fund another download. No automatic retry is performed here.
    operation_id = f"{run_id}:{node_id}:provision:{secrets.token_hex(16)}"
    bound = lease.reserved_bytes
    _reserve_operation(
        base_path, universe_id=universe_id, run_id=run_id, operation_id=operation_id,
        max_bytes=bound, refusal="workspace_provision_failed")
    result = execute_provision(
        manifests, lease_fd=lease_fd, repo_fd=repo_fd, max_transfer_bytes=bound,
        universe_dir=base_path, principal=principal,
        storage_bound=bound, timeout_s=timeout_seconds,
        cancelled=should_cancel if should_cancel is not None else lambda: False)
    _reconcile_operation(base_path, operation_id, result.bytes_to_charge)
    if result.failure:
        raise _Refused("workspace_provision_failed", "dependency installation did not complete",
                       provision_reason=result.failure)
    return {"provision": "completed", "provision_bytes": result.bytes_to_charge,
            "provision_digests": {key: plan.digest for key, plan in
                                  (("python", manifests.python), ("node", manifests.node))
                                  if plan is not None}}


def _checkout(
    *,
    packet: dict[str, Any],
    node_id: str,
    base_path: Path,
    run_id: str,
    universe_id: str,
    resource: Any,
    chain: Any,
    host: str,
    repo: str,
    execute: Any,
    timeout_seconds: float,
    admission: AdmissionObservation,
    principal: str = "",
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    from tinyassets import workspace_pool

    owner, name = _split_repo(repo)
    ref = _str_field(packet, "ref") or "HEAD"
    storage = _str_field(packet, "storage") or "scratch"
    if storage not in ("scratch", "universe"):
        raise _Refused("invalid_packet", "packet.storage must be 'scratch' or 'universe'")
    repo_key = repo_key_for(host, owner, name)
    db = _pool_db(base_path)

    # The startup barrier: finish every outbox entry an earlier process left
    # before admitting anything new. Once per process and cheap after that, but
    # a run that is the FIRST thing to touch the runs DB must still not admit
    # past an unreconciled entry.
    try:
        from tinyassets import runs as _runs

        _runs.ensure_workspace_reconciled(base_path)
    except Exception as exc:
        # Fail CLOSED. Admitting past a barrier that could not run is admitting
        # on top of whatever an earlier process left owed.
        logger.exception("workspace startup reconciliation failed")
        raise _Refused(
            "workspace_pool_busy",
            f"startup reconciliation failed: {type(exc).__name__}",
        ) from None

    # The lease bound is the platform's, never the packet's: a packet-chosen
    # reservation is a packet choosing its own quota. For a PERMANENT workspace
    # it is also never more than the owning account can still hold.
    storage_reservation, bound = _fit_permanent(storage, base_path, universe_id, repo_key, db)

    def _admit(*, wait_s: float = 0.0) -> Any:
        return workspace_pool.admit(
            db,
            universe_id=universe_id,
            connection_id=str(getattr(resource, "connection_id", "")),
            repo_key=repo_key,
            storage_class=storage,
            run_id=str(run_id),
            max_bytes=bound,
            pool_root=scratch_pool_root(base_path),
            universe_root=universe_workspace_root(base_path),
            wait_s=wait_s,
            observation=admission,
            # The bounded wait is the one place this node parks on another
            # run's lock. A cancel arriving there must end the wait, not be
            # discovered after the deadline has already bought a lease.
            should_cancel=should_cancel,
            **_universe_quota_kwargs(storage, bound),
        )

    admitted = False
    try:
        try:
            lease = _admit()
        except Exception as exc:
            if is_cancellation(exc):
                raise
            kind = _pool_error_kind(exc)
            if kind not in _SWEEPABLE_REFUSALS:
                raise _Refused(kind, f"workspace not admitted: {_pool_detail(exc)}") from None
            # A lock or a pool slot held by a run that has already finished is
            # owed to the outbox, not genuinely in use. Sweep ONCE and retry ONCE:
            # a loop here would turn a real contention into a stall, and the
            # periodic sweeper is what handles everything this misses.
            try:
                from tinyassets import runs as _runs

                _runs._workspace_sweep_once(base_path, claimant=f"adapter:{run_id}")
            except Exception:
                logger.exception("workspace sweep before retry failed")
                raise _Refused(kind, f"workspace not admitted: {_pool_detail(exc)}") from None
            try:
                lease = _admit(wait_s=max(0.0, float(timeout_seconds)))
            except Exception as retry_exc:
                if is_cancellation(retry_exc):
                    raise
                raise _Refused(
                    _pool_error_kind(retry_exc),
                    f"workspace not admitted: {_pool_detail(retry_exc)}",
                ) from None
        admitted = True
    finally:
        if not admitted:  # every give-up path, cancellation included
            _settle_permanent(storage_reservation, published=False)

    # ONE owner for everything created after admission (Codex round 3, P1 #7).
    # `owned` holds what this call opened; the mount takes them ONLY after a
    # successful registration, and anything still owned at the end is closed.
    # An unpublished failure also owes the lease a wipe -- a lease nobody holds
    # and nobody wipes is a leak the pool cannot see.
    owned: list[Any] = []
    published = False
    generation_bytes: int | None = None
    try:
        lease_parent, lease_name = _lease_names(base_path, Path(lease.path))
        answer = execute(
            {
                "op": "checkout",
                "universe_dir": str(base_path),
                "principal": principal,
                "agent": _agent_for(run_id, node_id),
                "grant_id": _str_field(packet, "grant_id"),
                "connection_id": str(getattr(resource, "connection_id", "")),
                "host": host,
                "owner_repo": repo,
                "ref": ref,
                "storage": storage,
                "lease_parent": lease_parent,
                "lease_name": lease_name,
                "checkout_ref": f"tiny/{_universe_short(universe_id)}/checkout",
            }
        )
        if not answer.get("ok"):
            raise _Refused(
                "workspace_checkout_failed",
                str(answer.get("error") or "checkout failed"),
                stderr_class=str(answer.get("stderr_class") or ""),
            )
        # The cell created the lease, its repository and its content as the
        # owner, and told us the relative name it used. It must be the one the
        # pool admitted: anything else is not the lease this run holds.
        if str(answer.get("lease") or "") != "/".join((*lease_parent, lease_name)):
            raise _Refused(
                "workspace_checkout_failed",
                "the owner cell answered for a different lease than the one admitted",
            )
        repo_dir = Path(lease.path) / _CONTENT_DIR
        lease_fd, repo_fd = _open_cell_lease(Path(lease.path))
        owned.extend((lease_fd, repo_fd))

        provision_evidence = {}
        if packet.get("provision") is not None:
            provision_evidence = _provision_checkout(
                packet["provision"], base_path=base_path, resource=resource,
                host=host, repo=repo, lease=lease, lease_fd=lease_fd, repo_fd=repo_fd,
                principal=principal,
                run_id=run_id, node_id=node_id, universe_id=universe_id,
                timeout_seconds=timeout_seconds, should_cancel=should_cancel)
        # A cancellation arriving between the final stage and publication still
        # cannot expose the new capability. The same root-owned predicate flows
        # through the entire effect call; no workspace-local run DB is consulted.
        if should_cancel is not None and should_cancel():
            raise _Refused("workspace_provision_failed", "checkout cancelled before publication",
                           provision_reason="cancelled")

        measured = int(answer.get("bytes") or 0)
        try:
            workspace_pool.reconcile_bytes(db, lease.lease_id, measured)
        except Exception:
            logger.exception("workspace byte reconciliation failed for %s", lease.lease_id)

        replaced = None
        if storage == "universe":
            # Provisioning may have grown the generation past the bytes the
            # transfer moved: measure what will be published, and refuse it
            # (discarded, the previous generation untouched) if it outgrew what
            # the account could hold.
            generation_bytes = _require_generation_fits(lease, bound)
            replaced = _publish(
                db, lease, universe_id=universe_id, repo_key=repo_key, run_id=run_id
            )

        connection_id = str(getattr(resource, "connection_id", ""))
        mount = _register_mount(
            chain,
            node_id,
            lease=lease,
            repo_dir=repo_dir,
            lease_fd=lease_fd,
            repo_fd=repo_fd,
            host=host,
            repo=repo,
            connection_id=connection_id,
            grant_id=_str_field(packet, "grant_id"),
        )
        # Ownership transfers to the mount ONLY now: from here the chain closes
        # them (on revoke or at settle), and this call must not.
        published = True
        owned.clear()
    finally:
        _settle_permanent(storage_reservation, published=published, actual=generation_bytes)
        if not published:
            _close_handles(*owned)
            _owe_wipe(base_path, lease, run_id=run_id, universe_id=universe_id)

    evidence: dict[str, Any] = {
        "op": "checkout",
        "repo": repo,
        "ref": ref,
        "resolved_sha": str(answer.get("resolved_sha") or ""),
        "bytes": measured,
        "storage": storage,
        "lease_generation": lease.generation,
    }
    if replaced is not None:
        evidence["replaced_generation"] = replaced
    evidence.update(provision_evidence)
    del mount
    return evidence


def _fit_permanent(
    storage: str, base_path: Path, universe_id: str, repo_key: str, db: Path,
) -> tuple[Any, int]:
    """``(storage_reservation, lease bound)`` for one workspace admission.

    Scratch: the pool's lease bound, never charged to anyone. Permanent: the
    OWNING ACCOUNT's storage decides -- the bound is what still fits (plus the
    generation this checkout replaces, whose discard is owed at publication),
    capped at the lease bound. account-storage-quota D6: the old fixed 4 GiB
    reservation against a flat 16 GiB quota refused every permanent workspace on
    a 2 GiB free account, empty or not. Raises `_Refused` when even the
    minimum does not fit, before any lease exists.
    """
    if storage != "universe":
        return None, _DEFAULT_MAX_CHECKOUT_BYTES
    from tinyassets import storage_accounting, workspace_pool
    from tinyassets.universe_owner import owner_of

    data_root = Path(base_path).parent
    credit = 0
    conn = workspace_pool._connect(db)
    try:
        workspace_pool.ensure_schema(conn)
        published = workspace_pool.published_generation(
            conn, universe_id=universe_id, repo_key=repo_key,
        )
    finally:
        conn.close()
    if published is not None:
        old_path, _ = workspace_pool.universe_paths(
            universe_workspace_root(base_path), repo_key, published,
        )
        credit = storage_accounting._walk_bytes(Path(old_path))
    try:
        return storage_accounting.reserve_fitted(
            data_root,
            account_id=owner_of(data_root, universe_id),
            scope_id=universe_id,
            store="workspaces",
            cap=_DEFAULT_MAX_CHECKOUT_BYTES,
            credit=credit,
        )
    except storage_accounting.StorageRefused as refused:
        # The effector result reaches whoever drove this run -- possibly a
        # collaborator in the owner's universe -- so only the charged account
        # sees its numbers (gpt-6-astra, PR #4167). No bound actor: redacted.
        record = storage_accounting.visible_record(refused)
        raise _Refused(record.pop("failure_class"), record.pop("error"), **record) from None


def _universe_quota_kwargs(storage: str, bound: int) -> dict[str, Any]:
    """The pool's own per-universe check, fed the ACCOUNT-derived bound: with no
    separate "used" number it only stops two in-flight permanent leases from
    sharing one bound. The account pool (`_fit_permanent`) is the one quota."""
    if storage != "universe":
        return {}
    return {"universe_quota_bytes": int(bound), "universe_used_bytes_fn": lambda _uid: 0}


def _settle_permanent(
    storage_reservation: Any, *, published: bool, actual: int | None = None,
) -> None:
    """Commit the permanent workspace's reservation once published -- at the
    MEASURED generation size, not the bound, so an empty created workspace is
    not billed its 4 GiB ceiling -- and release it otherwise. Never raises."""
    if storage_reservation is None:
        return
    from tinyassets import storage_accounting

    if published:
        try:
            amount = storage_reservation.bytes if actual is None else min(
                int(actual), storage_reservation.bytes,
            )
            storage_accounting.commit(storage_reservation, amount)
        except Exception:  # noqa: BLE001 -- measurement settles it
            logger.exception("workspace storage reservation could not be committed")
    else:
        storage_accounting.release(storage_reservation)


def _require_generation_fits(lease: Any, bound: int) -> int:
    """Measure the new generation -- provisioning included -- and refuse it if it
    outgrew its reservation, before publication (D6). Returns its size."""
    from tinyassets import storage_accounting

    size = storage_accounting._walk_bytes(Path(lease.path))
    if size > bound:
        raise _Refused(
            "storage_quota_exceeded",
            f"the workspace grew to {size} bytes, past the {bound} bytes its "
            "account storage could hold; nothing was published",
        )
    return size


def _publish(db: Path, lease: Any, *, universe_id: str, repo_key: str, run_id: str) -> int | None:
    """Switch the authoritative generation and owe the previous one a discard."""
    from tinyassets import workspace_pool

    conn = workspace_pool._connect(db)
    try:
        conn.execute("BEGIN IMMEDIATE")
        replaced = workspace_pool.publish_generation(
            conn,
            universe_id=universe_id,
            repo_key=repo_key,
            generation=lease.generation,
            path=lease.path,
        )
        if replaced is not None:
            workspace_pool.enqueue_discard(
                conn,
                run_id=str(run_id),
                universe_id=universe_id,
                storage_class="universe",
                repo_key=repo_key,
                generation=replaced,
            )
        conn.commit()
        return replaced
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _create(
    *,
    packet: dict[str, Any],
    node_id: str,
    base_path: Path,
    run_id: str,
    universe_id: str,
    chain: Any,
    execute: Any,
    timeout_seconds: float,
    admission: AdmissionObservation,
    principal: str = "",
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """An EMPTY workspace, born from nothing but the universe's own storage.

    The sink's other operations start from a git clone, which made a repository
    the only way to get a filesystem: a workflow that renders video, scrapes a
    site or assembles a dataset could not get one at all. It is the same lease,
    the same pool, the same quota, the same hourly job and the same outbox
    release -- only the population step is missing, because there is nothing to
    populate it with. The node writes it.

    No connection, no scope, no consent: there is no credential in play and no
    far side to reach. A gate here would ask the owner to approve their own
    scratch directory.
    """
    from tinyassets import workspace_pool

    storage = _str_field(packet, "storage") or "scratch"
    if storage not in ("scratch", "universe"):
        raise _Refused("invalid_packet", "packet.storage must be 'scratch' or 'universe'")
    stated_key = _str_field(packet, "workspace_key")
    if storage == "universe":
        if not stated_key:
            raise _Refused(
                "invalid_packet",
                "packet.workspace_key is required for a permanent workspace: it is "
                "the name a later run opens it by",
            )
        repo_key = workspace_key_for(stated_key)
    else:
        # Scratch lives and dies with the run, so a name would only be
        # decoration. Said out loud rather than silently ignored.
        repo_key = workspace_key_for(DEFAULT_WORKSPACE_KEY)
    db = _pool_db(base_path)

    try:
        from tinyassets import runs as _runs

        _runs.ensure_workspace_reconciled(base_path)
    except Exception as exc:
        logger.exception("workspace startup reconciliation failed")
        raise _Refused(
            "workspace_pool_busy",
            f"startup reconciliation failed: {type(exc).__name__}",
        ) from None

    if storage == "universe":
        # Publishing REPLACES, and the replaced generation is enqueued for an
        # irreversible wipe. For a checkout that is correct -- the remote is
        # the source of truth and the old copy is disposable. For a created
        # workspace the content exists nowhere else, so a second create under
        # the same name would destroy the work the first one holds. Refuse and
        # say so; re-opening an existing permanent workspace is a capability
        # this release does not have.
        conn = workspace_pool._connect(db)
        try:
            existing = workspace_pool.published_generation(
                conn, universe_id=universe_id, repo_key=repo_key
            )
        finally:
            conn.close()
        if existing is not None:
            raise _Refused(
                "workspace_exists",
                f"a permanent workspace named {stated_key!r} already exists "
                f"(generation {existing}); creating it again would replace and wipe "
                "it, and re-opening one is not available in this release",
            )

    # A created workspace starts empty, but its bound is still what the owning
    # account can hold -- and its admission is refused if even the minimum does
    # not fit (D6). Growth during the run is measured afterwards (founder Q5).
    storage_reservation, bound = _fit_permanent(storage, base_path, universe_id, repo_key, db)

    def _admit(*, wait_s: float = 0.0) -> Any:
        return workspace_pool.admit(
            db,
            universe_id=universe_id,
            # No connection: a created workspace is not credentialed. The pool
            # keys its ledger by universe, so an empty id charges the same
            # universe the same way.
            connection_id="",
            repo_key=repo_key,
            storage_class=storage,
            run_id=str(run_id),
            max_bytes=bound,
            pool_root=scratch_pool_root(base_path),
            universe_root=universe_workspace_root(base_path),
            wait_s=wait_s,
            observation=admission,
            # Same contended lock, same pool, same wait: a created workspace is
            # not a lesser admission and must stop for a cancel too.
            should_cancel=should_cancel,
            **_universe_quota_kwargs(storage, bound),
        )

    admitted = False
    try:
        try:
            lease = _admit()
        except Exception as exc:
            if is_cancellation(exc):
                raise
            kind = _pool_error_kind(exc)
            if kind not in _SWEEPABLE_REFUSALS:
                raise _Refused(kind, f"workspace not admitted: {_pool_detail(exc)}") from None
            try:
                from tinyassets import runs as _runs

                _runs._workspace_sweep_once(base_path, claimant=f"adapter:{run_id}")
            except Exception:
                logger.exception("workspace sweep before retry failed")
                raise _Refused(kind, f"workspace not admitted: {_pool_detail(exc)}") from None
            try:
                lease = _admit(wait_s=max(0.0, float(timeout_seconds)))
            except Exception as retry_exc:
                if is_cancellation(retry_exc):
                    raise
                raise _Refused(
                    _pool_error_kind(retry_exc),
                    f"workspace not admitted: {_pool_detail(retry_exc)}",
                ) from None
        admitted = True
    finally:
        if not admitted:
            _settle_permanent(storage_reservation, published=False)

    owned: list[Any] = []
    published = False
    try:
        # Even an empty workspace is created by the OWNER's cell: a node
        # sandbox mounts a directory only when the owner owns it, and the
        # daemon cannot make one. No connection, no route, no socket.
        lease_parent, lease_name = _lease_names(base_path, Path(lease.path))
        answer = execute(
            {
                "op": "create",
                "universe_dir": str(base_path),
                "principal": principal,
                "storage": storage,
                "lease_parent": lease_parent,
                "lease_name": lease_name,
            }
        )
        if not answer.get("ok"):
            raise _Refused(
                "workspace_checkout_failed",
                str(answer.get("error") or "the workspace directory was not created"),
                stderr_class=str(answer.get("stderr_class") or ""),
            )
        content_dir = Path(lease.path) / _CONTENT_DIR
        lease_fd, content_fd = _open_cell_lease(Path(lease.path))
        owned.extend((lease_fd, content_fd))

        # Nothing was transferred, and the hourly ledger has to be TOLD that:
        # the admission reserved the full lease bound, and an unreconciled
        # reservation would keep charging for bytes this workspace never used.
        try:
            workspace_pool.reconcile_bytes(db, lease.lease_id, 0)
        except Exception:
            logger.exception("workspace byte reconciliation failed for %s", lease.lease_id)

        replaced = None
        if storage == "universe":
            replaced = _publish(
                db, lease, universe_id=universe_id, repo_key=repo_key, run_id=run_id
            )

        # No host, no repo, no connection, no grant: this capability carries no
        # authority because none was used to make it. `push` reads exactly that
        # and refuses -- there is no remote to push to.
        _register_mount(
            chain,
            node_id,
            lease=lease,
            repo_dir=content_dir,
            lease_fd=lease_fd,
            repo_fd=content_fd,
            host="",
            repo="",
            connection_id="",
            grant_id="",
        )
        published = True
        owned.clear()
    finally:
        # Created empty: charged what it holds now (nothing), not its bound.
        _settle_permanent(storage_reservation, published=published, actual=0)
        if not published:
            _close_handles(*owned)
            _owe_wipe(base_path, lease, run_id=run_id, universe_id=universe_id)

    evidence: dict[str, Any] = {
        "op": "create",
        "storage": storage,
        "bytes": 0,
        "lease_generation": lease.generation,
        "workspace_key": stated_key if storage == "universe" else "",
    }
    if replaced is not None:  # unreachable while a second create is refused
        evidence["replaced_generation"] = replaced
    return evidence


def _push(
    *,
    packet: dict[str, Any],
    node_id: str,
    base_path: Path,
    run_id: str,
    universe_id: str,
    resource: Any,
    chain: Any,
    host: str,
    repo: str,
    execute: Any,
    ancestors: set[str] | None = None,
    principal: str = "",
) -> dict[str, Any]:
    commit_sha = _str_field(packet, "commit_sha")
    slug = _str_field(packet, "branch_slug")
    if not commit_sha:
        raise _Refused("invalid_packet", "packet.commit_sha is required for a push")
    if not _SLUG_RE.match(slug) or ".." in slug or slug.endswith(".lock"):
        raise _Refused("invalid_packet", "packet.branch_slug must be a single path-safe segment")
    mount = _resolve_mount(chain, packet, node_id, ancestors=ancestors)
    # The DESTINATION and the AUTHORITY come from the capability, never from
    # the packet: the checkout is what was consented to, and a packet naming a
    # different repository is a packet trying to reuse this credential
    # somewhere else (Codex round 2, #6).
    host = mount.host or host
    repo = mount.repo or repo
    _require_packet_agrees_with_mount(packet, mount)
    resource = _connection_for_mount(base_path, mount, fallback=resource, principal=principal)
    remote_ref = f"refs/heads/tiny/{_universe_short(universe_id)}/{slug}"

    held_lease = getattr(mount, "lease", None)
    if held_lease is None or not getattr(held_lease, "path", ""):
        raise _Refused("workspace_push_refused", "this workspace holds no lease to push from")
    lease_parent, lease_name = _lease_names(base_path, Path(held_lease.path))

    # The hourly ledger sees the push BEFORE any bytes move: a push holds no
    # lease, so without this it charged nothing at all (Codex round 3, P1 #4).
    operation_id = _operation_id(run_id, node_id, "push")
    _reserve_operation(
        base_path,
        universe_id=universe_id,
        run_id=run_id,
        operation_id=operation_id,
        max_bytes=_MAX_BUNDLE_BYTES,
        refusal="workspace_push_refused",
    )

    intent = record_push_intent(
        base_path,
        run_id=str(run_id),
        node_id=node_id,
        connection_id=mount.connection_id,
        repo=repo,
        remote_ref=remote_ref,
        sha=commit_sha,
        host=host,
        grant_id=mount.grant_id,
        universe_id=universe_id,
        expected_old_sha=_str_field(packet, "expected_old_sha") or None,
    )
    # Hold the capability for the whole operation: the cell reads the export
    # bundle out of this lease, and a discard racing it must not be able to
    # take the directory away underneath.
    with _acquired(chain, mount.node_id):
        answer = execute(
            {
                "op": "push",
                "universe_dir": str(base_path),
                "principal": principal,
                "agent": _agent_for(run_id, node_id),
                "grant_id": mount.grant_id,
                "connection_id": mount.connection_id,
                "host": host,
                "owner_repo": repo,
                "remote_ref": remote_ref,
                "commit_sha": commit_sha,
                "lease_parent": lease_parent,
                "lease_name": lease_name,
                "max_bundle_bytes": _MAX_BUNDLE_BYTES,
            }
        )
    # A TIMEOUT is not a failure: the send may have landed. It stays claimable
    # as `unknown` and the startup reconciler asks the remote (P1 #5).
    if answer.get("ok"):
        state = "done"
    elif str(answer.get("stderr_class") or "") == "timeout":
        state = "unknown"
    else:
        state = "failed"
    settle_push_intent(
        base_path, intent, state, observed_sha=str(answer.get("observed_sha") or "") or None
    )
    _reconcile_operation(base_path, operation_id, int(answer.get("bytes") or 0))
    if not answer.get("ok"):
        raise _Refused(
            "workspace_push_refused",
            str(answer.get("error") or "push refused"),
            stderr_class=str(answer.get("stderr_class") or ""),
            observed_sha=str(answer.get("observed_sha") or ""),
            remote_ref=remote_ref,
            intent_state=state,
        )
    return {
        "op": "push",
        "repo": repo,
        "remote_ref": remote_ref,
        "sha": commit_sha,
        "bytes": int(answer.get("bytes") or 0),
        "reconciled": bool(answer.get("reconciled")),
    }


def _discard(
    *,
    packet: dict[str, Any],
    node_id: str,
    base_path: Path,
    run_id: str,
    universe_id: str,
    chain: Any,
    ancestors: set[str] | None = None,
) -> dict[str, Any]:
    from tinyassets import workspace_pool

    mount = _resolve_mount(chain, packet, node_id, ancestors=ancestors)
    _require_packet_agrees_with_mount(packet, mount)
    # A discard holds no lease reservation of its own once it starts, so the
    # hourly ledger sees it here -- before anything is mutated.
    _reserve_operation(
        base_path,
        universe_id=universe_id,
        run_id=run_id,
        operation_id=_operation_id(run_id, node_id, "discard"),
        max_bytes=0,
        refusal="workspace_discard_failed",
    )
    # ACQUIRE, then revoke, then owe the bytes. Acquiring first means the facts
    # the outbox entry is built from are read off a capability this call HOLDS,
    # so a parallel discard cannot retire it underneath; revoking inside the
    # hold blocks any new acquisition while the originals stay open until this
    # release, and only then are the bytes owed.
    with _acquired(chain, mount.node_id) as held:
        _revoke_mount(chain, held.node_id or mount.node_id)
        db = _pool_db(base_path)
        conn = workspace_pool._connect(db)
        try:
            conn.execute("BEGIN IMMEDIATE")
            workspace_pool.enqueue_discard(
                conn,
                run_id=str(run_id),
                universe_id=universe_id,
                storage_class=held.storage_class,
                lease=held.lease if held.storage_class == "scratch" else None,
                repo_key=held.repo_key,
                generation=held.generation,
            )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise _Refused(
                "workspace_discard_failed", f"discard could not be recorded: {exc}"
            ) from None
        finally:
            conn.close()
    return {
        "op": "discard",
        "repo": mount.repo_key,
        "storage": mount.storage_class,
        "lease_generation": mount.generation,
    }


# --------------------------------------------------------------------------- #
# The chain's workspace registry
# --------------------------------------------------------------------------- #


def _register_mount(
    chain: Any,
    node_id: str,
    *,
    lease: Any,
    repo_dir: Path,
    lease_fd: Any,
    repo_fd: Any,
    host: str,
    repo: str,
    connection_id: str,
    grant_id: str,
) -> Any:
    """Publish the capability, bound to the authority it was created under.

    The bind source is the REPOSITORY's descriptor, not the lease root's:
    ``/workspace`` must BE the repository. The lease handle is kept too,
    because push reads ``repo/.tiny-export/<sha>.bundle``, a path beneath the
    lease rather than beneath the repo.
    """
    from tinyassets.effectors import WorkspaceMount

    descriptor = _descriptor_or_none(repo_fd)
    mount = WorkspaceMount(
        node_id=node_id,
        bind_source=(
            f"/proc/self/fd/{descriptor}" if descriptor is not None else str(repo_dir)
        ),
        pass_fds=(descriptor,) if descriptor is not None else (),
        repo_fd=repo_fd,
        lease_fd=lease_fd,
        lease=lease,
        storage_class=lease.storage_class,
        repo_key=lease.repo_key,
        generation=lease.generation,
        host=host,
        repo=repo,
        connection_id=connection_id,
        grant_id=grant_id,
    )
    register = getattr(chain, "register_workspace", None)
    if register is None:
        raise _Refused("workspace_checkout_failed", "this run cannot hold a workspace")
    register(node_id, mount)
    return mount


def _require_packet_agrees_with_mount(packet: dict[str, Any], mount: Any) -> None:
    """A packet may restate the capability's repo/connection, never change it.

    Silently preferring the mount would be safe but confusing; refusing says
    the packet is wrong about what it is operating on.
    """
    stated_repo = _str_field(packet, "repo")
    if stated_repo and mount.repo:
        try:
            if normalize_repo(stated_repo) != normalize_repo(mount.repo):
                raise _Refused(
                    "invalid_packet",
                    "packet.repo names a different repository than the workspace",
                )
        except GitScopeError:
            raise _Refused("invalid_packet", "packet.repo is not 'owner/name'") from None
    stated_connection = _str_field(packet, "connection_id")
    if stated_connection and mount.connection_id and stated_connection != mount.connection_id:
        raise _Refused(
            "invalid_packet",
            "packet.connection_id names a different connection than the workspace",
        )


def _connection_for_mount(
    base_path: Path, mount: Any, *, fallback: Any, principal: str = "",
) -> Any:
    """The connection the CHECKOUT ran under, re-read and re-checked.

    Re-read rather than remembered: a connection revoked between the checkout
    and the push must stop the push.
    """
    if not mount.connection_id or not mount.grant_id:
        return fallback
    data_root = _data_root(base_path)
    if data_root is None:
        return fallback
    _grant, resource = _read_connection(
        data_root=data_root,
        connection_id=mount.connection_id,
        universe_id=_universe_id(base_path),
        grant_id=mount.grant_id,
        principal=principal,
    )
    return resource


def _resolve_mount(
    chain: Any,
    packet: dict[str, Any],
    node_id: str,
    *,
    ancestors: set[str] | None = None,
) -> Any:
    """The workspace a push/discard names, through the run's effect chain ONLY.

    Never through state and never through ``$ta.ref``: a capability that can be
    named in state is a capability user text can forge.

    And it must be an ANCESTOR. The chain is run-global, so without this a node
    on a parallel branch could name a workspace it has no graph relationship
    to; ``ancestors`` is the compiler's graph relation, not the subset of
    ancestors that produced HTTP results (``None`` means no ancestry is known -- legacy
    post-run dispatch -- and is not treated as a refusal).
    """
    target = _str_field(packet, "workspace")
    if not target:
        raise _Refused("invalid_packet", "packet.workspace must name the checkout node")
    # The registry answers "absent" with None under either name; what to DO
    # about absent is the caller's, and an effect adapter's answer is a
    # structured refusal, never a raise into the completion path.
    lookup = getattr(chain, "workspace_mount_or_none", None)
    mount = lookup(target) if lookup is not None else None
    if mount is None:
        raise _Refused(
            "no_matching_packet",
            f"node '{node_id}' names workspace '{target}', which this run does not hold",
        )
    if ancestors is not None and target not in ancestors:
        raise _Refused(
            "no_matching_packet",
            f"node '{node_id}' names workspace '{target}', which is not one of its "
            "graph ancestors",
        )
    return mount


def _revoke_mount(chain: Any, node_id: str) -> None:
    revoke = getattr(chain, "revoke_workspace", None)
    if revoke is not None:
        revoke(node_id)


# --------------------------------------------------------------------------- #
# Pool error mapping
# --------------------------------------------------------------------------- #

#: The pool already refuses with exactly the D6 kinds, so its code passes
#: through unchanged. Translating it would only be a place for the two
#: vocabularies to drift -- and a mistranslation reads as the wrong advice
#: ("you are over quota" when the truth is "another run holds the lock").
_POOL_KINDS = frozenset(
    {"workspace_busy", "workspace_pool_busy", "workspace_quota_exceeded"}
)

#: Refusals a sweep can clear: a lock or a pool slot still recorded against a
#: run that already finished. A quota is NOT here -- being over budget is not
#: something cleanup fixes, and retrying would just fail twice.
_SWEEPABLE_REFUSALS = frozenset({"workspace_busy", "workspace_pool_busy"})


def is_cancellation(exc: BaseException) -> bool:
    """The owner stopped this run, so it is not a workspace failure.

    Name-matched, the same duck-typing `runs._is_cancel_exception` and
    `graph_compiler._is_cancel_exception` already use in both directions, so
    this module needs no import of either. Every broad catch between the pool
    and the run must consult it: a cancellation reclassified as
    ``effector_crashed`` or ``workspace_checkout_failed`` tells the owner their
    workflow broke when in fact they stopped it -- and hides the stop from the
    run's own terminal status.
    """
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        if type(cur).__name__ in ("RunCancelledError", "NodeCancelledError"):
            return True
        seen.add(id(cur))
        cur = cur.__cause__ or cur.__context__
    return False


def _pool_error_kind(exc: Exception) -> str:
    code = str(getattr(exc, "code", "") or "").strip()
    if code in _POOL_KINDS:
        return code
    # An unrecognised refusal is not silently called a quota problem.
    return "workspace_checkout_failed"


def _pool_detail(exc: Exception) -> str:
    detail = str(getattr(exc, "detail", "") or "").strip()
    return detail or f"{type(exc).__name__}"


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def run_workspace_effector(
    *,
    node_id: str,
    output_keys: list[str],
    run_state: dict[str, Any],
    base_path: str | Path | None = None,
    run_id: str = "",
    dry_run: bool | None = None,
    allowed_state_keys: list[str] | set[str] | None = None,
    ancestors: set[str] | None = None,
    chain: Any = None,
    execute: Any = None,
    timeout_seconds: float = 0.0,
    should_cancel: Callable[[], bool] | None = None,
    execution_context=None,
) -> dict[str, Any]:
    """Dispatch one ``workspace`` packet. Raises only a CANCELLATION.

    Every failure comes back as an ``error``/``error_kind`` result, including a
    crash. The single exception is the owner stopping the run: that is not a
    result this node can carry, because a cancelled run has no node outcome to
    report - it unwinds to ``status=cancelled``. See `is_cancellation`.

    ``chain`` and ``execute`` are injected by tests; in production the chain is
    the run's active :class:`EffectChain` and ``execute`` spawns the worker.
    """
    del allowed_state_keys
    admission = AdmissionObservation()
    try:
        result = _run(
            node_id=node_id,
            output_keys=output_keys,
            run_state=run_state,
            base_path=base_path,
            run_id=run_id,
            dry_run=dry_run,
            chain=chain,
            execute=execute,
            ancestors=ancestors,
            timeout_seconds=timeout_seconds,
            admission=admission,
            should_cancel=should_cancel,
            execution_context=execution_context,
        )
    except _Refused as refused:
        result = {"error": refused.error, "error_kind": refused.kind, **refused.extra}
    except Exception as exc:  # noqa: BLE001 - never raise from the completion path
        if is_cancellation(exc):
            raise
        logger.exception("workspace effector crashed for node %s", node_id)
        result = {"error": f"effector crashed: {exc}", "error_kind": "effector_crashed"}
    # Include observations even when population fails after a contended admission.
    # No real attempt (early refusal, dry run, historical result) means unknown.
    if admission.attempts:
        result["workspace_admission"] = admission.snapshot()
    return result


def _run(
    *,
    node_id: str,
    output_keys: list[str],
    run_state: dict[str, Any],
    base_path: str | Path | None,
    run_id: str,
    dry_run: bool | None,
    chain: Any,
    execute: Any,
    admission: AdmissionObservation,
    ancestors: set[str] | None = None,
    timeout_seconds: float = 0.0,
    should_cancel: Callable[[], bool] | None = None,
    execution_context=None,
) -> dict[str, Any]:
    matched_key, packet = _find_packet(output_keys=output_keys, run_state=run_state)
    if packet is None:
        return {
            "error": (
                f"node '{node_id}' declared effects=[{EXTERNAL_WRITE_SINK_WORKSPACE}] but no "
                "output_key held a parseable workspace packet"
            ),
            "error_kind": "no_matching_packet",
        }
    op = _str_field(packet, "op")
    if op not in _CONSENT_FOR_OP:
        raise _Refused(
            "invalid_packet",
            "packet.op must be one of " + ", ".join(sorted(_CONSENT_FOR_OP)) + f": {op!r}",
        )

    repo = _str_field(packet, "repo")
    universe_id = _universe_id(base_path)
    data_root = _data_root(base_path)
    if not universe_id or data_root is None or base_path is None:
        return {
            "error": "no command center authority is bound to this run",
            "error_kind": "no_universe_authority",
            "matched_output_key": matched_key,
        }
    universe_dir = Path(base_path)
    host = ""
    from tinyassets.auth.middleware import current_identity_or_none

    # The admitted owner routes every broker authority read.
    identity = current_identity_or_none()
    if execution_context is not None:
        if (execution_context.universe_id != universe_id
                or not execution_context.owner_user_id):
            raise _Refused("execution_context_mismatch", "workspace execution scope mismatch")
        principal = execution_context.owner_user_id
    else:
        principal = identity.user_id if identity is not None else ""

    if chain is None:
        from tinyassets.effectors import active_effect_chain

        chain = active_effect_chain(str(run_id))

    if execute is None:
        # Every operation that writes a directory goes through the owner's
        # cell, ``create`` included: the daemon cannot make a directory the
        # owner owns, and a node sandbox mounts nothing else.
        from tinyassets.workspace_worker import execute_workspace_operation

        execute = execute_workspace_operation

    if op == "create":
        # No connection, no repository, no consent: everything below this line
        # gates a CREDENTIAL, and a created workspace has none.
        if dry_run:
            return {
                "dry_run": True,
                "op": op,
                "storage": _str_field(packet, "storage") or "scratch",
                "workspace_key": _str_field(packet, "workspace_key"),
                "matched_output_key": matched_key,
            }
        evidence = _create(
            packet=packet,
            node_id=node_id,
            base_path=universe_dir,
            run_id=run_id,
            universe_id=universe_id,
            chain=chain,
            execute=execute,
            timeout_seconds=timeout_seconds,
            admission=admission,
            principal=principal,
            should_cancel=should_cancel,
        )
        evidence["matched_output_key"] = matched_key
        return evidence

    if op == "discard":
        if dry_run:
            return {"dry_run": True, "op": op, "reason": "dry_run"}
        return _discard(
            packet=packet,
            node_id=node_id,
            base_path=universe_dir,
            run_id=run_id,
            universe_id=universe_id,
            chain=chain,
            ancestors=ancestors,
        )

    if op == "push":
        # The capability is the authority, so a packet that disagrees with it
        # is refused HERE -- before the connection and scope gates, which would
        # otherwise report the contradiction as "scope not granted" and send
        # the reader looking for a missing grant that is not the problem.
        early = _resolve_mount(chain, packet, node_id, ancestors=ancestors)
        _require_packet_agrees_with_mount(packet, early)
        # A CREATED workspace carries no host and no repository, because no
        # remote was ever contacted to make it. Falling through would let the
        # packet supply both and push the universe's own scratch space at a
        # repository nobody checked out.
        if not early.repo or not early.host:
            raise _Refused(
                "workspace_push_refused",
                "this workspace has no git remote; check out a repository to push",
            )
        repo = early.repo
        host = early.host

    if not repo:
        raise _Refused("invalid_packet", "packet.repo is required")
    _split_repo(repo)
    connection_id = _str_field(packet, "connection_id")
    grant_id = _str_field(packet, "grant_id")
    if not connection_id:
        raise _Refused("invalid_packet", "packet.connection_id is required")
    if not grant_id:
        raise _Refused("invalid_packet", "packet.grant_id is required")

    _grant, resource = _read_connection(
        data_root=data_root,
        connection_id=connection_id,
        universe_id=universe_id,
        grant_id=grant_id,
        principal=principal,
    )
    # The credentialed host comes from the STORED connection, not the packet.
    # A packet may restate it; naming a different one is a refusal.
    host = transport_host_for(resource)
    _require_packet_host_agrees(packet, host)
    _require_scope(resource, op, host, repo)
    # The mode comes from the STORED connection the gates above already
    # validated, never from the packet: a caller must not be able to claim its
    # own grant is full (full-channel-access D3.3).
    _require_consent(
        universe_dir, op, host, repo, connection_id,
        access_mode=connection_access_mode(resource),
    )

    if dry_run:
        # Describe, never spawn. Every gate above has already run, so a dry run
        # reports the refusal a live run would hit rather than a clean plan.
        return {
            "dry_run": True,
            "op": op,
            "repo": repo,
            "host": host,
            "storage": _str_field(packet, "storage") or "scratch",
            "matched_output_key": matched_key,
        }

    common = {
        "packet": packet,
        "node_id": node_id,
        "base_path": universe_dir,
        "run_id": run_id,
        "universe_id": universe_id,
        "resource": resource,
        "chain": chain,
        "host": host,
        "repo": repo,
        "execute": execute,
        "ancestors": ancestors,
    }
    if op == "checkout":
        evidence = _checkout(
            **{k: v for k, v in common.items() if k != "ancestors"},
            timeout_seconds=timeout_seconds,
            admission=admission,
            principal=principal,
            should_cancel=should_cancel,
        )
    else:
        evidence = _push(**common, principal=principal)
    evidence["matched_output_key"] = matched_key
    return evidence
