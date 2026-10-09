"""Guard: no new raw file read or write in a module that touches universe paths.

A workflow provider jail binds its universe read-write and allows ``symlink``,
so any path under the data dir may be a link planted by a universe pointing at
another universe (``docs/concerns/2026-10-01-provider-planted-link-reads-another-universe.md``).
The daemon reads and writes those paths through ONE pair of helpers:
:func:`tinyassets.universe_files.read_data_path` /
:func:`~tinyassets.universe_files.write_data_path` (and the relpath forms
``read_universe_file`` / ``write_universe_file``), which follow no link at any
component and refuse loudly.

This test pins, per module, how many raw file operations remain in every module
that mentions a universe or data-dir path. Adding one fails: route it through
``universe_files``. Removing one also fails until the pin is lowered, so the
count only ever shrinks. Many pinned sites read platform files that are in no
universe (package assets, the data root's own files); the pin does not claim
each is unsafe, only that none is added unreviewed.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_PKG = _REPO / "tinyassets"

#: The helpers themselves: the only modules allowed to do the raw I/O.
_EXEMPT = {"tinyassets/universe_files.py", "tinyassets/workspace_fs.py"}

_TOUCHES_UNIVERSE = re.compile(
    r"universe_dir|universe_path|\budir\b|_universe_dir|data_dir\(\)|_base_path\(\)"
    r"|base_path|wiki_root|_wiki_root\(\)|wiki_path\(\)|canon_dir"
)
_RAW_ATTRS = {"read_text", "read_bytes", "write_text", "write_bytes", "open",
              "rename", "unlink", "touch", "symlink_to", "hardlink_to"}
_RAW_OS = {"replace", "rename", "unlink", "remove", "open"}
_RAW_SHUTIL = {"copy", "copy2", "copyfile", "move", "rmtree"}

#: ``(receiver, attribute)`` pairs that READ like a path operation but whose
#: receiver is a module, so no file is opened and no link can be followed.
#: ``storage_accounting.touch()`` marks a measurement row dirty with a SQLite
#: UPDATE (``storage_accounting.touch``); counting it would pin a phantom
#: operation that this file's shrink-only ratchet could then never drop.
#: Exact pairs only -- never a bare receiver name, which would hide every other
#: operation on it.
_NOT_PATH_CALLS = {("storage_accounting", "touch")}


def _raw_ops(source: str) -> list[tuple[int, str, str]]:
    """``(line, enclosing function, operation)`` for every raw file call.

    ``Path.replace`` is not counted: statically it is indistinguishable from
    ``str.replace``. ``os.replace`` is.
    """
    found: list[tuple[int, str, str]] = []

    def visit(node: ast.AST, func: str) -> None:
        for child in ast.iter_child_nodes(node):
            is_def = isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            name = child.name if is_def else func
            if isinstance(child, ast.Call):
                target = child.func
                what = None
                if isinstance(target, ast.Name) and target.id == "open":
                    what = "open()"
                elif isinstance(target, ast.Attribute):
                    owner = target.value.id if isinstance(target.value, ast.Name) else ""
                    if owner == "os" and target.attr in _RAW_OS:
                        what = f"os.{target.attr}()"
                    elif owner == "shutil" and target.attr in _RAW_SHUTIL:
                        what = f"shutil.{target.attr}()"
                    elif (owner not in ("os", "shutil") and target.attr in _RAW_ATTRS
                          and (owner, target.attr) not in _NOT_PATH_CALLS):
                        what = f".{target.attr}()"
                if what is not None:
                    found.append((child.lineno, name, what))
            visit(child, name)

    visit(ast.parse(source), "<module>")
    return found


def _scan() -> dict[str, list[str]]:
    """module -> sorted ``"function: op"`` keys for every raw file call."""
    out: dict[str, list[str]] = {}
    for path in sorted(_PKG.rglob("*.py")):
        rel = path.relative_to(_REPO).as_posix()
        if rel in _EXEMPT:
            continue
        source = path.read_text(encoding="utf-8")
        if not _TOUCHES_UNIVERSE.search(source):
            continue
        ops = sorted(f"{func}: {what}" for _line, func, what in _raw_ops(source))
        if ops:
            out[rel] = ops
    return out


#: module -> the raw file operations left, as ``"function: op"``.
#: Initial inventory reconciled with main 6004a636 on 2026-10-03 before this
#: guard lands. Existing authority-record operations remain migration debt;
#: listing them is not evidence that they refuse links (see the concern below).
#: The migration checklist for the sealed-box move. Delete an entry when you
#: convert its site to tinyassets.universe_files; never add one.
PINNED: dict[str, list[str]] = {
    "tinyassets/account_deletion.py": [
        "_rmtree: shutil.rmtree()",
        "_rmtree: shutil.rmtree()",
        "_stage_home: .rename()",
        "_write_unfinished_receipt: .write_text()",
        "pending_deletions: .read_text()",
    ],
    "tinyassets/agent_sessions.py": [
        "_nofollow_dirs: os.open()",
        "_nofollow_dirs: os.open()",
        "clear: .unlink()",
        "exclusive: os.open()",
        "load: .read_text()",
        "save: os.replace()",
        "save: os.unlink()",
    ],
    "tinyassets/api/pending_requests.py": [
        "_first_power_preset: .read_text()",
    ],
    "tinyassets/api/status.py": [
        # Fixed platform data-root marker, outside universe-writable mounts.
        "_load_deploy_pending: .read_text()",
        "_load_release_state: .read_text()",
    ],
    "tinyassets/api/universe.py": [
        "_action_control_daemon: .unlink()",
        "_action_create_universe: .write_text()",
        "_action_switch_universe: .write_text()",
        "_read_founder_offers: .read_text()",
        "_write_founder_offers: os.replace()",
        "_write_founder_offers: os.unlink()",
    ],
    "tinyassets/api/universe_file_reads.py": [
        "_open_universe_dir: os.open()",
    ],
    "tinyassets/authoring/store.py": [
        "put_file_handle: .write_bytes()",
    ],
    "tinyassets/auto_ship_ledger.py": [
        "_file_lock: os.open()",
        "_read_raw: .open()",
        "_write_raw: .open()",
        "_write_raw: os.replace()",
        "record_attempt: .open()",
    ],
    "tinyassets/automation_context.py": [
        "_brain: .open()",
    ],
    "tinyassets/bid/execution_log.py": [
        "_exec_log_lock: os.open()",
        "append_execution_log_entry: .write_text()",
        "append_execution_log_entry: os.replace()",
        "read_execution_log: .read_text()",
    ],
    "tinyassets/billing/stripe_adapter.py": [
        "last_verified_delivery: .read_text()",
        "record_verified_delivery: .write_text()",
    ],
    "tinyassets/bug_investigation.py": [
        "attach_patch_packet_comment: .read_text()",
        "attach_patch_packet_comment: .write_text()",
    ],
    "tinyassets/catalog/backend.py": [
        "_rollback_yaml: .unlink()",
        "_rollback_yaml: .write_bytes()",
        "_snapshot_paths: .read_bytes()",
        "_write_yaml: .write_text()",
    ],
    "tinyassets/config.py": [
        "write_provider_assignment_projection: os.replace()",
        "write_provider_assignment_projection: os.unlink()",
        "write_universe_config_fields: os.replace()",
        "write_universe_config_fields: os.unlink()",
    ],
    "tinyassets/credential_refresh.py": [
        "file_lock: .open()",
    ],
    "tinyassets/credential_vault.py": [
        "_fsync_directory: os.open()",
        "_fsync_file: open()",
        "_on_disk_document_is_newer: .read_text()",
        "_read_credential_material: .read_bytes()",
        "_remove_snapshot_tree: .unlink()",
        "_remove_snapshot_tree: .unlink()",
        "_write_exclusive_snapshot_file: os.open()",
        "ensure_codex_home_from_vault: .write_bytes()",
        "ensure_codex_home_from_vault: .write_text()",
        "load_credential_vault: .read_text()",
    ],
    "tinyassets/daemon_brain.py": [
        "_promote_daemon_memory_to_wiki: .open()",
        "_promote_daemon_memory_to_wiki: .write_text()",
    ],
    "tinyassets/daemon_memory.py": [
        "_read_section: .read_text()",
    ],
    "tinyassets/daemon_wiki.py": [
        "_append_line: .open()",
        "_write_if_missing: .write_text()",
        "record_daemon_signal: .write_text()",
    ],
    "tinyassets/desktop/dashboard.py": [
        "_seed_from_output_dir: .read_text()",
    ],
    "tinyassets/desktop/launcher.py": [
        "_handle_add_files: shutil.copy2()",
    ],
    "tinyassets/desktop/packaged_entrypoint.py": [
        "_apply_update: .read_bytes()",
        "_apply_update: .read_bytes()",
        "_apply_update: .read_text()",
    ],
    "tinyassets/dispatcher.py": [
        "load_dispatcher_config: .read_text()",
    ],
    "tinyassets/engine_mcp_http.py": [
        "_write_routes: os.open()",
        "_write_routes: os.replace()",
        "read_engine_mcp_route: .read_text()",
    ],
    "tinyassets/idle_cycle.py": [
        "_read_stamp: .read_text()",
        "_try_lock_nonblocking: os.open()",
        "try_acquire_idle_cycle_slot: .write_text()",
        "try_acquire_idle_cycle_slot: os.replace()",
    ],
    "tinyassets/ingestion/extractors.py": [
        "synthesize_source: .read_text()",
    ],
    "tinyassets/knowledge/raptor.py": [
        "_read_canon_paragraphs: .read_text()",
    ],
    "tinyassets/mcp_server.py": [
        "add_canon: .write_text()",
        "get_activity: .read_text()",
        "get_chapter: .read_text()",
        "get_progress: .read_text()",
        "get_review_state: .read_text()",
        "get_status: .read_text()",
        "get_work_targets: .read_text()",
        "pause: .write_text()",
        "resume: .unlink()",
        "set_premise: .write_text()",
    ],
    "tinyassets/memory/ingestion.py": [
        "_split_into_sections: .read_text()",
    ],
    "tinyassets/onboarding/__init__.py": [
        "render_app_html: .read_text()",
        "render_app_html: .read_text()",
        "request_theme: .read_text()",
    ],
    "tinyassets/onboarding/hosted_model_auth.py": [
        "load_preset: .read_text()",
    ],
    "tinyassets/onboarding/session_store.py": [
        "_discard_legacy_store: shutil.rmtree()",
        "_read_json: .read_text()",
        "_unlink: .unlink()",
        "_write_record: .write_text()",
        "_write_record: os.replace()",
    ],
    # Data-root files only: the lease db and every owner-tree member lock live
    # at ``<data_root>/.owner_tree/<tree_id>/`` -- both names are registered
    # platform entries in ``storage_accounting.ROOT_ENTRIES``, and no jail binds
    # the data root, so no universe can plant a link on these paths.
    # The locks additionally CANNOT route through universe_files: that writer
    # publishes by fresh inode plus rename, which would move the lock off the
    # inode each member holds open for its whole process life, and the proof
    # that an owner is dead is exactly "every member file is lockable".
    # tests/test_owner_lease.py::test_tree_files_live_only_at_the_data_root
    "tinyassets/owner_lease.py": [
        "_try_lock: os.open()",
        "founder_alive: .read_text()",
        "join: .write_text()",
        "leave: .unlink()",
    ],
    "tinyassets/platform_runtime_provenance.py": [
        "_read_metadata_instance_id: .open()",
        "read_expected_instance_id: .open()",
    ],
    "tinyassets/process_liveness.py": [
        "remove_if_dead: .unlink()",
        "remove_if_dead: .unlink()",
        "remove_if_dead: .unlink()",
        "remove_if_dead: os.open()",
    ],
    "tinyassets/producers/goal_pool.py": [
        "_scan_goal_dir: .read_text()",
        "write_pool_post: .write_text()",
    ],
    "tinyassets/provider_assignment.py": [
        "_file_lock: .open()",
    ],
    # Inherited main operations, NOT certified link-safe. Parent-path lookup
    # remains ordinary I/O; preserve atomic migration publication when fixing.
    # docs/concerns/2026-10-03-provider-authority-parent-links.md
    "tinyassets/provider_authority.py": [
        "_read: .read_text()",
        "authority_for: .unlink()",
        "write_record: .unlink()",
        "write_record: os.replace()",
    ],
    "tinyassets/providers/base.py": [
        "_codex_last_refresh_age_s: .read_text()",
        "_read_probe_cache_file: .read_text()",
        "_write_probe_cache_file: .write_text()",
        "_write_probe_cache_file: os.replace()",
        "subprocess_env_for_provider: .read_text()",
    ],
    # ``claude_provider`` had two in ``_engine_mcp_flags``; they are gone from
    # the tree, and this ratchet only ever shrinks, so the pin goes with them.
    "tinyassets/providers/codex_provider.py": [
        "_resolved_codex_executable: .open()",
    ],
    "tinyassets/providers/definition.py": [
        "_load: .read_text()",
        "_write: .unlink()",
        "_write: os.replace()",
        "list_commons_definitions: .read_text()",
    ],
    # owner-dynamic-admission DA3: every call is descriptor-relative with
    # O_NOFOLLOW; the setgid hand-off cannot go through the path helpers.
    "tinyassets/role_center_admission.py": [
        "_open_dir: os.open()",
        "_remove_tree: os.unlink()",
        "admit_center: os.open()",
        "admit_center: os.open()",
    ],
    "tinyassets/reset.py": [
        "reset: .unlink()",
        "reset: shutil.rmtree()",
    ],
    "tinyassets/rollback.py": [
        "_append_rollback_log: .open()",
    ],
    "tinyassets/runtime/assigned_queue_consumer.py": [
        "_hold_liveness: .unlink()",
        "_publish_heartbeat: .write_text()",
    ],
    "tinyassets/storage/__init__.py": [
        "_reject_orphaned_legacy_sidecars: os.replace()",
        "_replace_if_exists: os.replace()",
        "active_universe_id: .read_text()",
        "probe_env_readability: .open()",
    ],
    "tinyassets/storage/delivery_lock.py": [
        "try_attempt_lock: os.open()",
    ],
    "tinyassets/storage/outbound_connections.py": [
        "__call__: .open()",
        "__call__: .open()",
        "__call__: .read_text()",
        "_execute_pinned_https_request: .open()",
        # S6 broker: the streaming sibling of _execute_pinned_https_request; this
        # .open() is urllib opener.open() (network), not a file.
        "_open_pinned_https_stream: .open()",
    ],
    "tinyassets/storage/rotation.py": [
        "prune_universe_outputs: .unlink()",
        "rotate_run_transcripts: .open()",
        "rotate_run_transcripts: .open()",
        "rotate_run_transcripts: .unlink()",
        "rotate_run_transcripts: .unlink()",
        "rotate_run_transcripts: .unlink()",
    ],
    "tinyassets/storage/run_execution_lock.py": [
        "try_run_execution_lock: os.open()",
    ],
    "tinyassets/storage/run_file_lock.py": [
        "try_file_operation_lock: os.open()",
    ],
    # Data-root files only (.layout.json, .layout.lock): no jail binds the data
    # root, and the layout lock must be opened before anything else is.
    "tinyassets/storage_layout.py": [
        "_open_lock: os.open()",
        "_open_lock: os.open()",
        "_write_atomically: .unlink()",
        "_write_atomically: os.open()",
        "_write_atomically: os.replace()",
        "read_marker: .read_text()",
    ],
    "tinyassets/storage_accounting.py": [
        "_commons_pages: .read_bytes()",
    ],
    "tinyassets/subscription_refresh.py": [
        "adopt_newer_on_disk_document: .read_bytes()",
    ],
    "tinyassets/subscriptions.py": [
        "_file_lock: os.open()",
        "_read_raw: .read_text()",
        "_write_raw: .write_text()",
        "_write_raw: os.replace()",
    ],
    "tinyassets/ui_preview.py": [
        # The host-wide render slot's flock holder at the DATA ROOT
        # (.ui-preview.lock): no jail binds the data root, and flock needs the
        # descriptor to outlive the open, which the universe_files writers do
        # not hand back. Opened O_NOFOLLOW.
        "_host_slot: os.open()",
        # Linux /proc, not a data path at all: reaping the render's strays.
        "_proc_snapshot: open()",
    ],
    "tinyassets/universe_tools.py": [
        # Direct-child removals in the validated, provider-masked workspace.
        "_clear_link_mountpoint: .unlink()",
        "_promote_brain_files: .unlink()",
        "_remove_cgroup: .read_text()",
        "_remove_cgroup: .write_text()",
        "_root_cgroup: .read_text()",
        "_root_cgroup: .read_text()",
        "_root_cgroup: .write_text()",
        "_root_cgroup: .write_text()",
        "_root_cgroup: .write_text()",
        "_root_cgroup: .write_text()",
        "_supervise: .write_text()",
        "_try_lock_one: os.open()",
    ],
    "tinyassets/wiki/okf_export.py": [
        "_conformance_report: .read_text()",
        "_write_index: .write_text()",
        "_write_log: .write_text()",
        "export_okf_bundle: .write_text()",
    ],
    "tinyassets/workspace_family.py": [
        "try_family_fence: os.open()",
    ],
    "tinyassets/workspace_pool.py": [
        "_reconcile_filesystem: .rename()",
    ],
    "tinyassets/workspace_staging.py": [
        "_lock_tree_exclusive: os.open()",
        "_namespace_started_at: open()",
        "_namespace_started_at: open()",
        "_remove_completely: os.rename()",
        "_rmtree: shutil.rmtree()",
        "hold_in_use: os.open()",
    ],
    # ``workspace_worker`` had one: the credentialed clone it deleted out of
    # its staging directory. The daemon runs no git and stages nothing now, so
    # the module does no file I/O at all; the clone's removal moved into the
    # owner's cell (``workspace_remote_cell``), which touches only paths
    # beneath the command center the launcher mounted for it.
}


def test_no_new_raw_file_io_in_universe_touching_modules():
    from collections import Counter

    live = _scan()
    added = {}
    for mod, ops in live.items():
        extra = Counter(ops) - Counter(PINNED.get(mod, []))
        if extra:
            added[mod] = sorted(extra.elements())
    assert not added, (
        "new raw file read/write in a module that touches universe paths; route "
        "it through tinyassets.universe_files (read_data_path / write_data_path / "
        "unlink_data_path), which follows no link and refuses loudly:\n"
        + "\n".join(f"{mod}: {ops}" for mod, ops in sorted(added.items()))
    )


def test_the_pin_only_shrinks():
    from collections import Counter

    live = _scan()
    stale = {}
    for mod, ops in PINNED.items():
        gone = Counter(ops) - Counter(live.get(mod, []))
        if gone:
            stale[mod] = sorted(gone.elements())
    assert not stale, (
        "raw file ops were removed; delete them from PINNED so they cannot "
        f"come back: {stale}"
    )


def test_the_scan_sees_a_raw_read_and_write():
    """Detection control: the scanner is not vacuous."""
    ops = {what for _l, _f, what in _raw_ops(
        "import os\n"
        "def f(udir):\n"
        "    (udir / 'a').write_text('x')\n"
        "    os.replace(udir / 'b', udir / 'c')\n"
        "    return open(udir / 'd').read()\n"
    )}
    assert {".write_text()", "os.replace()", "open()"} <= ops


def test_the_module_call_exemption_is_exactly_one_pair():
    """``storage_accounting.touch()`` is a SQLite UPDATE, so it is not counted --
    but a real ``Path.touch()``, and every other call on that module, still is."""
    ops = {what for _l, _f, what in _raw_ops(
        "from tinyassets import storage_accounting\n"
        "def f(udir):\n"
        "    storage_accounting.touch(udir.parent, udir.name, 'workspaces')\n"
        "    storage_accounting.unlink(udir / 'z')\n"
        "    (udir / 'a').touch()\n"
    )}
    assert ".touch()" in ops, "a path touch is still raw file I/O"
    assert ".unlink()" in ops, "only the touch pair is exempt, not the module"
