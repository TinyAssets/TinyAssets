"""Internal standalone capture into owner-scoped immutable run-file custody.

No public action is enabled here. A trusted authenticated adapter supplies owner
and universe; this service freshly checks their existing admin grant.
No run/job is fabricated. Produced workspace capture and cross-owner copy use
different source authority adapters and are not implied by this implementation.
"""

import hashlib
import json
import logging
import os
import shutil
import stat
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from tinyassets import runs, workspace_pool
from tinyassets.authoring.store import AuthoringStore
from tinyassets.execution_authority.blob_proof import BlobProofStore
from tinyassets.execution_authority.blob_stream import BlobStreamError
from tinyassets.run_file_contract import public_reference
from tinyassets.run_file_sources import open_authoring_source
from tinyassets.scoped_reset import _assert_recovery_state_is_clean, acquire_maintenance_barrier
from tinyassets.storage import _connect as author_connection
from tinyassets.storage import run_files as store
from tinyassets.storage.current_home import check_current_home
from tinyassets.storage.run_file_lock import try_file_operation_lock

logger = logging.getLogger(__name__)
MAX_CAPTURE_FILES = 32
_SOURCE_KEYS = (
    "handle_id",
    "session_id",
    "owner_id",
    "filename",
    "media_type",
    "size_bytes",
    "sha256",
)


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()


def _headroom_bytes():
    raw = os.environ.get("TINYASSETS_RUN_FILE_HEADROOM_BYTES", str(64 * 1024 * 1024))
    if not raw.isascii() or not raw.isdecimal():
        raise store.FileCustodyRefused("file_headroom_invalid")
    return store._integer(int(raw))


def check_admin_grant(conn, owner, universe, *, require_current_home=False):
    """The box-local universe admin check, on a caller-held connection.

    The only definition of "this principal may act in this universe" for custody.
    Exposed separately so a path already holding the platform writer -- notably the
    cross-owner publication fence, which must revalidate a SECOND principal -- runs
    exactly these checks instead of opening a nested writer with its own copy.
    """
    if require_current_home:
        check_current_home(conn, owner, universe)
    # Exact existing universe admin grant, not home ownership or attribution.
    row = conn.execute(
        "SELECT permission FROM universe_acl WHERE universe_id=? AND actor_id=?",
        (universe, owner),
    ).fetchone()
    if row is None or row[0] != "admin":
        raise store.FileCustodyRefused("run_file_access_denied")


@contextmanager
def _authority(base, owner, universe, *, write=False, require_current_home=False):
    with author_connection(base) as conn:
        conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        check_admin_grant(conn, owner, universe, require_current_home=require_current_home)
        yield conn


def _physical_store(base, *, create=True):
    root = Path(base).absolute() / ".run-file-custody"
    if create:
        root.mkdir(mode=0o700, exist_ok=True)
    info = root.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & 0x400
        or root.resolve() != root
    ):
        raise store.FileCustodyRefused("run_file_root_unsafe")
    return root, BlobProofStore(root)


def _replay(conn, guard, owner, universe, request_digest):
    guard.require_held(conn)
    row = conn.execute(
        "SELECT * FROM run_file_operations WHERE operation_id=?", (guard.operation_id,)
    ).fetchone()
    if row is None:
        return None
    if (row["owner_id"], row["universe_id"], row["request_sha256"]) != (
        owner,
        universe,
        request_digest,
    ):
        raise store.FileCustodyRefused("file_operation_conflict")
    if row["state"] != "committed":
        # An interrupted copy is cleanup debt, never an implicit repeat transfer.
        raise store.FileCustodyRefused("file_operation_recovery_required")
    objects = json.loads(row["result_json"])
    result = []
    for item in objects:
        current = conn.execute(
            "SELECT * FROM run_file_objects WHERE file_id=? AND operation_id=? "
            "AND owner_id=? AND universe_id=? AND state='ready'",
            (item["file_id"], guard.operation_id, owner, universe),
        ).fetchone()
        if current is None or public_reference(current) != public_reference(item):
            raise store.FileCustodyRefused("run_file_not_found")
        result.append(public_reference(current))
    if not result:
        raise store.FileCustodyRefused("file_operation_corrupt")
    return result


def capture_authoring_files(base, *, owner_id, universe_id, label, sources, should_cancel=None):
    """Atomically expose a verified binary bundle, independent of source lifetime.

    Stable label is namespaced by authenticated owner/universe. Source handle IDs
    are immutable capabilities; changed members/order conflicts. Accepted retry
    reads only owned custody, not an expired/revoked old source handle. Source
    revocation before acceptance refuses. Failure keeps inventoried cleanup debt.
    """
    if (
        any(type(x) is not str or not x for x in (owner_id, universe_id, label))
        or len(label) > 256
        or not isinstance(sources, list)
        or not 1 <= len(sources) <= MAX_CAPTURE_FILES
    ):
        raise store.FileCustodyRefused("file_capture_request_invalid")
    if any(not isinstance(source, dict) for source in sources):
        raise store.FileCustodyRefused("file_capture_source_invalid")
    sources = [dict(source) for source in sources]
    for source in sources:
        if set(source) != {"session_id", "handle_id"} or any(
            type(v) is not str or not v or len(v) > 256 for v in source.values()
        ):
            raise store.FileCustodyRefused("file_capture_source_invalid")
    operation = "file:capture:" + _digest([owner_id, universe_id, label])
    request = _digest(["authoring-handle-v1", owner_id, universe_id, sources])
    source_store = AuthoringStore(base)

    def metadata():
        result = []
        for source in sources:
            source_store.get_session(source["session_id"], actor_id=owner_id)
            result.append(source_store.get_file_handle(
                source["handle_id"], actor_id=owner_id, session_id=source["session_id"]
            ))
        return result

    @contextmanager
    def opened(index, expected, cancelled):
        with open_authoring_source(source_store, owner_id=owner_id, **sources[index],
                                   should_cancel=cancelled) as stream:
            if any(stream.metadata[key] != expected[key] for key in _SOURCE_KEYS):
                raise store.FileCustodyRefused("file_source_changed")
            yield stream.iter_chunks()

    @contextmanager
    def fence(expected):
        with source_store.file_handles_commit_fence(sources=sources, actor_id=owner_id) as current:
            if any(any(a[key] != b[key] for key in _SOURCE_KEYS)
                   for a, b in zip(current, expected)):
                raise store.FileCustodyRefused("file_source_changed")
            yield

    return _capture_files(base, owner_id=owner_id, universe_id=universe_id,
                          operation=operation, request=request, metadata_provider=metadata,
                          open_source=opened, source_fence=fence, should_cancel=should_cancel)


def _capture_files(base, *, owner_id, universe_id, operation, request, metadata_provider,
                   open_source, source_fence, should_cancel=None, require_current_home=False,
                   ready_to_copy=None, replay_result=None, publication_check=None):
    """Trusted source adapters share ONE journal/allocation/publication path.

    Callbacks are platform code only, never a workflow/plugin registry. The same
    worker thread owns the maintenance barrier and operation guard throughout.

    ``publication_check`` is called WITH the publication connection and the held
    platform connection, inside their ``BEGIN IMMEDIATE``, for a source whose
    authority serializes on those same databases: a ``source_fence`` cannot order
    such a source without opening a second writer against one held here. Raising
    rolls the publication back.
    """
    base = Path(base).absolute()

    def authority(*, write=False):
        return _authority(base, owner_id, universe_id, write=write,
                          require_current_home=require_current_home)

    with acquire_maintenance_barrier(base, exclusive=False, timeout=5):
        _assert_recovery_state_is_clean(base)
        with try_file_operation_lock(base, operation_id=operation) as guard:
            if guard is None:
                raise store.FileCustodyRefused("file_operation_busy")
            with authority(write=True):
                # Service-owned additive initialization, outside callback writers.
                # No source bytes or root coordinator work occurs under this fence.
                with runs._connect(base) as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    store.ensure_schema(conn)
                    prior = _replay(conn, guard, owner_id, universe_id, request)
                    if prior is not None:
                        return replay_result(conn, operation, prior) if replay_result else prior
            ceiling = store.capacity_limit()
            headroom = _headroom_bytes()
            metadata = metadata_provider()
            objects = [
                store.CapturedFile.new(
                    **{key: row[key] for key in ("filename", "media_type", "size_bytes", "sha256")}
                )
                for row in metadata
            ]
            for item in objects:
                public_reference(asdict(item))  # Bound display metadata, never truncate.
            maximum = sum(item.size_bytes for item in objects)
            root, blobs = _physical_store(base)
            physical_id = ":".join(str(part) for part in blobs.root_identity)
            with authority(write=True):
                with runs._connect(base) as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    guard.require_held(conn)
                    # Metadata measurement is serialized with allocation state;
                    # a pre-transaction value could miss another just-retained copy.
                    root_now = root.lstat()
                    if (root_now.st_dev, root_now.st_ino) != blobs.root_identity:
                        raise store.FileCustodyRefused("run_file_root_changed")
                    free = shutil.disk_usage(root).free
                    store.reserve_in_transaction(
                        conn,
                        operation_id=operation,
                        owner_id=owner_id,
                        universe_id=universe_id,
                        request_sha256=request,
                        max_bytes=maximum,
                        physical_root_id=physical_id,
                        ceiling_bytes=ceiling,
                        free_bytes=free,
                        headroom_bytes=headroom,
                    )
                    store.inventory_in_transaction(
                        conn,
                        operation_id=operation,
                        owner_id=owner_id,
                        universe_id=universe_id,
                        storage_keys=[item.file_id for item in objects],
                    )
            transferred = 0
            reserved = accepted = False
            storage_reservation = None

            def cancelled():
                with authority():
                    return should_cancel is not None and should_cancel()

            try:
                # The OWNER's account storage gates the copy -- for a cross-owner
                # delivery that is the RECEIVER (founder 2026-09-30, Q4). A refusal
                # here runs the cleanup below, so the custody allocation is
                # released and nothing is copied.
                from tinyassets import storage_accounting

                account = owner_id if storage_accounting.is_account(base, owner_id) else None
                storage_reservation = storage_accounting.reserve(
                    base, account_id=account, scope_id=account or "", store="uploads",
                    nbytes=maximum,
                )
                workspace_pool.reserve_transfer_bytes(
                    runs.runs_db_path(base),
                    universe_id=universe_id,
                    run_id="",
                    operation_id=operation,
                    max_bytes=maximum,
                )
                reserved = True
                if ready_to_copy is not None:
                    ready_to_copy()
                for index, (expected, item) in enumerate(zip(metadata, objects)):
                    try:
                        with open_source(index, expected, cancelled) as chunks:
                            staged = blobs.stage_stream(
                                item.file_id + ".part",
                                chunks,
                                max_bytes=item.size_bytes,
                                should_cancel=cancelled,
                            )
                            transferred += staged.size
                            if (staged.size, staged.sha256) != (item.size_bytes, item.sha256):
                                raise store.FileCustodyRefused("file_source_changed")
                        # Source handle verification completes before final binding;
                        # its fresh metadata fence below orders any later revoke.
                        blobs.publish_staged(
                            staged, item.file_id + ".body", should_cancel=cancelled
                        )
                    except BlobStreamError as exc:
                        transferred += exc.bytes_read
                        raise
                with authority(write=True) as platform:
                    with source_fence(metadata):
                        if should_cancel is not None and should_cancel():
                            raise store.FileCustodyRefused("file_capture_cancelled")
                        with runs._connect(base) as conn:
                            conn.execute("BEGIN IMMEDIATE")
                            guard.require_held(conn)
                            if publication_check is not None:
                                # The held platform writer is handed over so a
                                # cross-owner source can revalidate its OWN
                                # principal here without a nested writer.
                                publication_check(conn, platform)
                            store.commit_objects_in_transaction(
                                conn,
                                operation_id=operation,
                                owner_id=owner_id,
                                universe_id=universe_id,
                                objects=objects,
                            )
                            result = [public_reference(asdict(item)) for item in objects]
                            if replay_result is not None:
                                result = replay_result(conn, operation, result)
                accepted = True
                return result
            finally:
                if storage_reservation is not None:
                    from tinyassets import storage_accounting

                    if accepted:
                        storage_accounting.commit(storage_reservation)
                    else:
                        storage_accounting.release(storage_reservation)
                if not accepted:
                    # Visibility revocation/debt only; no guessed file deletion or
                    # retained-capacity refund before verified physical cleanup.
                    with runs._connect(base) as conn:
                        conn.execute("BEGIN IMMEDIATE")
                        guard.require_held(conn)
                        store.mark_cleanup_in_transaction(
                            conn, operation_id=operation, owner_id=owner_id, universe_id=universe_id
                        )
                if reserved:
                    try:
                        workspace_pool.reconcile_operation_bytes(
                            runs.runs_db_path(base), operation, transferred
                        )
                    except Exception:
                        # Existing conservative maximum remains durable. Never
                        # relabel accepted custody as absent or replay the copy.
                        logger.exception("Run-file byte settlement debt: %s", operation)
