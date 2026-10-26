#!/usr/bin/env python3
"""Read-only census of why connected_sources() is empty, for every owner.

Awake boxes are read through the mount boxhostd already holds. Asleep images
are attached with a private read-only XFS mount (ro,norecovery) and detached
before exit. Nothing here wakes a box, formats an image, or replaces one.

The report is aggregate counts. It does not contain principals, tokens,
definition ids, or paths.
"""
from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

READONLY_MOUNT_OPTIONS = "ro,norecovery,nosuid,nodev,loop"
_CENTER = re.compile(r"[A-Za-z0-9_-]{1,128}")
_UID = range(300001, 400000)
_403 = re.compile(
    r"canary_sign_in refused code="
    r"(wrong_door_key|email_not_pinned_account|authenticated_other_account"
    r"|password_refused workos=[a-z0-9_]{1,64})"
)
_STORE = "provider_definitions.json"
# The assignment record is the membership the router enforces, so it is what a
# user actually sees offered. A definition walk alone reports sources nobody has.
_ASSIGNMENT = ".provider-authority/assignment.json"
_MEMBER_METHODS = ("subscription_cli", "api_key_http")
_MEMBERSHIP = (*_MEMBER_METHODS, "other", "definitions_without_member",
               "members_without_definition")


def _unreadable() -> dict:
    return {"subscription": [], "http": [], "owner_errors": ["tree_unreadable"]}
_MAX_FILE = 2_000_000
_MAX_VAULT = 8 * 1024 * 1024

# First failure wins, in the order the serving catalog evaluates the checks.
# The CLI status command is not run: it can refresh tokens.


def mount_argv(image: Path, dest: Path) -> list[str]:
    return ["mount", "-t", "xfs", "-o", READONLY_MOUNT_OPTIONS, str(image), str(dest)]


def umount_argv(dest: Path) -> list[str]:
    return ["umount", "--no-canonicalize", str(dest)]


def canary_refusal_codes(journal: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for code in _403.findall(journal):
        counts[code] = counts.get(code, 0) + 1
    return counts


def _rel(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ValueError("path leaves the owner root")
    return path


class LocalView:
    def __init__(self, root: Path):
        self.root = root

    def _path(self, rel: str) -> Path:
        return self.root / _rel(rel)

    def is_symlink(self, rel: str) -> bool:
        return self._path(rel).is_symlink()

    def is_file(self, rel: str, *, follow_symlinks: bool = False) -> bool:
        path = self._path(rel)
        # Default rejects a symlink. The CLI opts in: Path.is_file() is true
        # when the link's target is a regular file.
        if follow_symlinks:
            return path.is_file()
        return path.is_file() and not path.is_symlink()

    def is_dir(self, rel: str) -> bool:
        path = self._path(rel)
        return path.is_dir() and not path.is_symlink()

    def list_dir(self, rel: str) -> list[str]:
        path = self.root if rel == "" else self._path(rel)
        if path.is_symlink() or not path.is_dir():
            return []
        return sorted(child.name for child in path.iterdir())

    def read_bytes(self, rel: str) -> bytes | None:
        path = self._path(rel)
        if path.is_symlink() or not path.is_file():
            return None
        if path.stat().st_size > _MAX_FILE:
            return None
        return path.read_bytes()


class NsenterView:
    """Read an awake owner's existing mount. Does not mount or write."""

    def __init__(self, pid: int, uid: int, run=None):
        if type(uid) is not int or uid not in _UID or type(pid) is not int or pid <= 1:
            raise ValueError("awake mount identity is invalid")
        self.pid, self.uid = pid, uid
        self._run = run or _nsenter

    def _path(self, rel: str) -> str:
        return str(Path(f"/var/lib/boxhostd/mounts/{self.uid}") / _rel(rel))

    def _ok(self, argv: list[str]) -> bool:
        return self._run(self.pid, argv).returncode == 0

    def is_symlink(self, rel: str) -> bool:
        return self._ok(["test", "-L", self._path(rel)])

    def is_file(self, rel: str, *, follow_symlinks: bool = False) -> bool:
        # test -f follows links, matching Path.is_file(). The CLI is the only
        # caller that wants that; state files stay non-symlinks.
        if follow_symlinks:
            return self._ok(["test", "-f", self._path(rel)])
        return not self.is_symlink(rel) and self._ok(["test", "-f", self._path(rel)])

    def is_dir(self, rel: str) -> bool:
        return not self.is_symlink(rel) and self._ok(["test", "-d", self._path(rel)])

    def mounted(self) -> bool:
        return self._ok(["test", "-d", f"/var/lib/boxhostd/mounts/{self.uid}"])

    def list_dir(self, rel: str) -> list[str]:
        target = f"/var/lib/boxhostd/mounts/{self.uid}" if rel == "" else self._path(rel)
        completed = self._run(self.pid, ["ls", "-A1", "--", target])
        if completed.returncode != 0:
            return []
        return sorted(
            name for name in completed.stdout.decode("utf-8", "replace").splitlines()
            if name and "/" not in name and "\x00" not in name
        )

    def read_bytes(self, rel: str) -> bytes | None:
        if not self.is_file(rel):
            return None
        size = self._run(self.pid, ["stat", "-c", "%s", "--", self._path(rel)])
        if size.returncode != 0:
            return None
        try:
            if int(size.stdout.strip()) > _MAX_FILE:
                return None
        except ValueError:
            return None
        completed = self._run(self.pid, ["cat", "--", self._path(rel)])
        if completed.returncode != 0:
            return None
        return completed.stdout


def _nsenter(pid: int, argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["nsenter", "-t", str(pid), "-m", "--", *argv],
        capture_output=True, timeout=15,
    )


def _digest(value):
    from tinyassets.providers.subscription_source import digest

    return digest(value)


def legacy_digests(source) -> set[str]:
    """Digests upgrade_connection() would accept. This does not write the marker."""
    old = source.document()
    old["state"] = dict(old["state"])
    old["state"]["location"] = "owner_box"
    accepted = {_digest(old)}
    install = old.get("sign_in", {}).get("cli_login", {}).get("install")
    if isinstance(install, dict) and "lock_sha256" in install:
        old["sign_in"] = json.loads(json.dumps(old["sign_in"]))
        old["sign_in"]["cli_login"]["install"].pop("lock_sha256", None)
        old["state"]["location"] = "owner_box"
        accepted.add(_digest(old))
        old["state"]["location"] = "broker"
        accepted.add(_digest(old))
    return accepted


def _centers(view) -> tuple[list[str], str | None]:
    raw = view.read_bytes(".centers.db")
    if raw is None:
        names = [name for name in view.list_dir("") if _CENTER.fullmatch(name)]
        return names, None
    uri_path = None
    try:
        import tempfile
        handle = tempfile.NamedTemporaryFile(prefix="ta-centers-", suffix=".db")
        handle.write(raw)
        handle.flush()
        uri_path = handle
        with closing(sqlite3.connect(f"file:{handle.name}?mode=ro", uri=True)) as db:
            rows = db.execute(
                "SELECT center FROM centers WHERE retired=0 ORDER BY center"
            ).fetchall()
        names = [row[0] for row in rows if isinstance(row[0], str) and _CENTER.fullmatch(row[0])]
        return names, None
    except (sqlite3.Error, OSError):
        return [], "centers_unreadable"
    finally:
        if uri_path is not None:
            uri_path.close()


def _definitions(view, center: str) -> tuple[list[dict], str | None]:
    raw = view.read_bytes(f"{center}/{_STORE}")
    if raw is None:
        return [], None
    try:
        rows = json.loads(raw)
    except (json.JSONDecodeError, UnicodeError):
        return [], "definition_store_unreadable"
    if not isinstance(rows, list):
        return [], "definition_store_unreadable"
    return [row for row in rows if isinstance(row, dict)], None


def _members(view, center: str) -> tuple[set[str], str | None]:
    """The provider names this center's assignment record admits. No ids leave here."""
    raw = view.read_bytes(f"{center}/{_ASSIGNMENT}")
    if raw is None:
        return set(), None
    try:
        record = json.loads(raw)
    except (json.JSONDecodeError, UnicodeError):
        return set(), "assignment_record_unreadable"
    if not isinstance(record, dict):
        return set(), "assignment_record_unreadable"
    allowed = record.get("allowed_providers")
    if not isinstance(allowed, list):
        # None is the never-assigned default; anything else is a malformed ceiling.
        return set(), None if allowed is None else "assignment_record_unreadable"
    return {name for name in allowed if isinstance(name, str)}, None


def _image_binary(source, cli_root: Path) -> str | None:
    """None when the image does not supply this CLI. A check name when it is broken."""
    if cli_root is None:
        return None
    directory = cli_root / source["id"]
    marker_path = directory / "installed.json"
    if not marker_path.is_file() or marker_path.is_symlink():
        return None
    try:
        record = json.loads(marker_path.read_text(encoding="utf-8"))
        install = source.sign_in["cli_login"]["install"]
        expected = _digest({"install": install, "registry": source["egress"]["registry"]})
        if record.get("source") != expected:
            return None
        lock_name = "requirements.lock" if install["manager"] == "pip" else "package-lock.json"
        lock = directory / lock_name
        if not lock.is_file() or lock.is_symlink():
            return "binary_lock_mismatch"
        if record.get("lock") != hashlib.sha256(lock.read_bytes()).hexdigest():
            return "binary_lock_mismatch"
        binary = directory / (
            Path("venv/bin") / install["bin"] if install["manager"] == "pip"
            else Path("node_modules/.bin") / install["bin"]
        )
        # npm ci links node_modules/.bin/<bin>. Path.is_file() accepts that.
        if not binary.is_file():
            return "binary_missing"
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return "binary_lock_mismatch"
    return ""


def _owner_binary(view, source) -> bool:
    install = source.sign_in["cli_login"]["install"]
    relative = (
        f".ta-cli/{source['id']}/venv/bin/{install['bin']}"
        if install["manager"] == "pip"
        else f".ta-cli/{source['id']}/node_modules/.bin/{install['bin']}"
    )
    return view.is_file(relative, follow_symlinks=True)


def _load_vault(path: Path | None) -> tuple[dict, str | None]:
    if path is None:
        return {}, None
    if path.is_symlink() or (path.exists() and not path.is_file()):
        return {}, "custody_read:OSError"
    if not path.is_file():
        return {}, None
    try:
        if path.stat().st_size > _MAX_VAULT:
            return {}, "custody_read:OSError"
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        return {}, "custody_read:OSError"
    except json.JSONDecodeError:
        return {}, "custody_read:ValueError"
    if not isinstance(payload, dict):
        return {}, "custody_read:ValueError"
    return payload, None


def _state_bytes(vault_entry: dict, view, name: str) -> bytes | None:
    state = vault_entry.get("state") if isinstance(vault_entry, dict) else None
    if isinstance(state, dict) and isinstance(state.get(name), str):
        try:
            return base64.b64decode(state[name], validate=True)
        except (ValueError, TypeError):
            return None
    return view.read_bytes(name)


def _state_checks(source, view, vault_entry: dict) -> str | None:
    checks = source.sign_in["cli_login"]["login"].get("state_checks", [])
    from tinyassets.providers.subscription_source import select

    for check in checks:
        raw = _state_bytes(vault_entry, view, check["path"])
        if raw is None:
            return "authenticated_state_check"
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, UnicodeError):
            return "authenticated_state_check"
        if any(not select(value, key) for key in check.get("present", [])):
            return "authenticated_state_check"
        if any(select(value, key) != expected for key, expected in check.get("equals", {}).items()):
            return "authenticated_state_check"
    return None


def _catalog(view):
    from tinyassets.providers.subscription_source import sources

    if isinstance(view, LocalView):
        return sources(view.root)
    import tempfile
    with tempfile.TemporaryDirectory(prefix="ta-sources-") as tmp:
        home = Path(tmp)
        override = home / ".ta-cli" / "sources"
        override.mkdir(parents=True)
        for name in view.list_dir(".ta-cli/sources"):
            if name != Path(name).name or not name.endswith(".json"):
                continue
            raw = view.read_bytes(f".ta-cli/sources/{name}")
            if raw is not None:
                (override / name).write_bytes(raw)
        return sources(home)


def classify_subscription(view, principal: str, center: str, row: dict, *,
                          cli_root: Path | None, vault: dict, vault_error: str | None,
                          catalog: dict) -> str | None:
    if (row.get("owner_user_id") != principal or row.get("universe_id") != center
            or not isinstance(center, str) or center.startswith(".")):
        return "require_scope"
    ref = row.get("ref")
    if not isinstance(ref, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,63}", ref):
        return "source_record_missing"
    source = catalog.get(ref)
    if source is None:
        return "source_record_missing"
    # Image CLI resolution runs while the runtime is constructed, before the
    # accepted-marker check. A lock or binary failure there is raised first.
    image = _image_binary(source, cli_root)
    if image:
        return image
    marker_rel = f".ta-cli/{ref}/connection"
    if view.is_symlink(marker_rel):
        return "connection_marker_unreadable"
    raw = view.read_bytes(marker_rel)
    if raw is None:
        if view.is_file(marker_rel):
            return "marker_too_large"
        return "require_accepted_missing_marker"
    try:
        marker = json.loads(raw)
    except (json.JSONDecodeError, UnicodeError):
        return "marker_not_json"
    if not isinstance(marker, dict):
        return "marker_not_object"
    if "source" not in marker:
        return "marker_missing_source"
    presented = marker.get("source")
    if not isinstance(presented, str):
        return "marker_source_not_string"
    if "incarnation" not in marker:
        return "marker_missing_incarnation"
    incarnation = marker.get("incarnation")
    if not isinstance(incarnation, str) or not incarnation:
        return "require_accepted_missing_incarnation"
    current = _digest(source)
    if presented != current and presented not in legacy_digests(source):
        return "require_accepted_digest_mismatch"
    if image is None and not _owner_binary(view, source):
        return "binary_missing"
    if vault_error:
        return vault_error
    entry = vault.get(ref) if isinstance(vault.get(ref), dict) else {}
    state = entry.get("state") if isinstance(entry.get("state"), dict) else {}
    captured = any(view.is_file(name) for name in source["state"].get("paths", []))
    if not state and not captured:
        return "custody_and_capture_empty"
    paths = source["state"].get("paths", [])
    if any(name not in state and not view.is_file(name) for name in paths):
        return "authenticated_paths_missing"
    failed = _state_checks(source, view, entry)
    if failed:
        return failed
    return None


def classify_http(principal: str, grant_id: str, db) -> str | None:
    if db is None:
        return "ledger:OSError"
    if not isinstance(grant_id, str) or not grant_id:
        return "ledger:GrantResolutionError"
    try:
        grant = db.execute(
            "SELECT owner_user_id, revoked_at, connection_id "
            "FROM outbound_connection_grants WHERE grant_id=?",
            (grant_id,),
        ).fetchone()
        if grant is None:
            return "grant_absent"
        grant_owner, grant_revoked, connection_id = grant
        if grant_owner != principal:
            return "grant_owner_mismatch"
        if grant_revoked is not None:
            return "grant_revoked"
        row = db.execute(
            "SELECT owner_user_id, revoked_at FROM outbound_connections WHERE connection_id=?",
            (connection_id,),
        ).fetchone()
        if row is None:
            return "connection_absent"
        if row[1] is not None:
            return "connection_revoked"
        if row[0] != principal:
            return "connection_owner_mismatch"
        capability = db.execute(
            "SELECT 1 FROM connection_capabilities WHERE connection_id=? "
            "AND capability_kind IN ('model_use', 'model_discovery')",
            (connection_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return "ledger:OperationalError"
    except OSError:
        return "ledger:OSError"
    if capability is None:
        return "ledger_not_model_source"
    return None


_LEDGER_TABLES = (
    "outbound_connections",
    "outbound_connection_grants",
    "connection_capabilities",
    "box_model_sources",
)


def ledger_totals(db) -> dict:
    """Row counts only. No ids, owners, or credential text."""
    found = {}
    for name in _LEDGER_TABLES:
        slot = {"exists": False, "rows": 0, "revoked": 0}
        if db is not None:
            try:
                present = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (name,),
                ).fetchone()
                if present is not None:
                    quoted = '"' + name + '"'
                    slot["exists"] = True
                    slot["rows"] = db.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0]
                    columns = {info[1] for info in db.execute(f"PRAGMA table_info({quoted})")}
                    if "revoked_at" in columns:
                        slot["revoked"] = db.execute(
                            f"SELECT COUNT(*) FROM {quoted} WHERE revoked_at IS NOT NULL"
                        ).fetchone()[0]
            except sqlite3.Error:
                slot = {"exists": False, "rows": 0, "revoked": 0}
        found[name] = slot
    return found


def inspect_owner(view, principal: str, *, cli_root: Path | None, vault_path: Path | None,
                  ledger) -> dict:
    centers, center_error = _centers(view)
    vault, vault_error = _load_vault(vault_path)
    try:
        catalog = _catalog(view)
    except (OSError, ValueError):
        catalog = {}
    subscription: list[str | None] = []
    http: list[str | None] = []
    owner_errors = [center_error] if center_error else []
    membership = dict.fromkeys(_MEMBERSHIP, 0)
    for center in centers:
        if view.is_symlink(center):
            owner_errors.append("center_symlink")
            continue
        rows, store_error = _definitions(view, center)
        if store_error:
            owner_errors.append(store_error)
            continue
        members, member_error = _members(view, center)
        if member_error:
            owner_errors.append(member_error)
        served = set()
        for name in members:
            method, _, ident = name.partition(":")
            membership[method if method in _MEMBER_METHODS else "other"] += 1
            served.add(ident)
        defined = {row["id"] for row in rows if isinstance(row.get("id"), str)}
        membership["definitions_without_member"] += len(defined - served)
        membership["members_without_definition"] += len(served - defined)
        for row in rows:
            method = row.get("access_method")
            if method == "subscription_cli":
                subscription.append(classify_subscription(
                    view, principal, center, row, cli_root=cli_root,
                    vault=vault, vault_error=vault_error, catalog=catalog))
            elif method == "api_key_http":
                http.append(classify_http(principal, str(row.get("ref", "")), ledger))
    return {"subscription": subscription, "http": http, "owner_errors": owner_errors,
            "vault_secrets": _vault_secrets(vault),
            "assignment_members": membership,
            "ta_cli": view.is_dir(".ta-cli")}


_FIXED_LEAVES = frozenset({"readonly_loop", "unavailable", "runsc", "not_run"})
_ORDINARY_WORD = re.compile(r"[a-z_-]+\Z")


def _credential_like(value: str) -> bool:
    """Long vault strings that are not ordinary lowercase words."""
    return len(value) >= 20 and _ORDINARY_WORD.fullmatch(value) is None


def _vault_secrets(vault: dict) -> set[str]:
    found = set()
    for entry in vault.values() if isinstance(vault, dict) else ():
        state = entry.get("state") if isinstance(entry, dict) else None
        if not isinstance(state, dict):
            continue
        for value in state.values():
            if isinstance(value, str) and _credential_like(value):
                found.add(value)
    return found


def _data_leaves(report: dict) -> list[str]:
    """Journal-derived canary codes, plus any string leaf that is not fixed vocabulary."""
    leaves: list[str] = []

    def walk(value):
        if isinstance(value, str):
            if value not in _FIXED_LEAVES:
                leaves.append(value)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    if not isinstance(report, dict):
        walk(report)
        return leaves
    canary = report.get("canary_sign_in")
    codes = canary.get("codes") if isinstance(canary, dict) else None
    if isinstance(codes, dict):
        for code in codes:
            if isinstance(code, str):
                leaves.append(code)
    for key, value in report.items():
        if key == "canary_sign_in" and isinstance(value, dict):
            for subkey, item in value.items():
                if subkey == "codes" and isinstance(item, dict):
                    walk(list(item.values()))
                else:
                    walk(item)
        else:
            walk(value)
    return leaves


def build_report(owner_results: list[dict], *, awake: int, asleep: int, missing_image: int,
                 image_without_binding: int, unreadable: int, asleep_inspection: str,
                 awake_census: str, canary_journal: str, canary_sources: dict | None = None,
                 ledger_tables: dict | None = None, ta_cli: dict | None = None,
                 subscription_state: dict | None = None,
                 broker_dates: dict | None = None) -> dict:
    failures: dict[str, dict[str, int]] = {}
    sub_owners: set[int] = set()
    http_owners: set[int] = set()
    sub_defs = http_defs = 0
    passed_sub_owners: set[int] = set()
    passed_http_owners: set[int] = set()
    passed_sub = passed_http = 0
    membership = dict.fromkeys(_MEMBERSHIP, 0)
    member_owners: set[int] = set()

    def bump(name: str, index: int, definitions: bool = True):
        slot = failures.setdefault(name, {"owners": 0, "definitions": 0, "_owners": set()})
        slot["_owners"].add(index)
        if definitions:
            slot["definitions"] += 1

    for index, result in enumerate(owner_results):
        subscription = result.get("subscription") or []
        http = result.get("http") or []
        if subscription:
            sub_owners.add(index)
            sub_defs += len(subscription)
            if all(item is None for item in subscription):
                passed_sub_owners.add(index)
        if http:
            http_owners.add(index)
            http_defs += len(http)
            if all(item is None for item in http):
                passed_http_owners.add(index)
        for item in subscription:
            if item is None:
                passed_sub += 1
            else:
                bump(item, index)
        for item in http:
            if item is None:
                passed_http += 1
            else:
                bump(item, index)
        for item in result.get("owner_errors") or []:
            bump(item, index, definitions=False)
        counts = result.get("assignment_members") or {}
        for name in _MEMBERSHIP:
            value = counts.get(name, 0)
            if type(value) is int:
                membership[name] += value
        if any(counts.get(name) for name in _MEMBER_METHODS):
            member_owners.add(index)
    public_failures = {
        name: {"owners": len(slot["_owners"]), "definitions": slot["definitions"]}
        for name, slot in sorted(failures.items())
    }
    return {
        "owners": len(owner_results) + missing_image,
        "awake": awake,
        "asleep": asleep,
        "missing_image": missing_image,
        "image_without_binding": image_without_binding,
        "unreadable": unreadable,
        "asleep_inspection": asleep_inspection,
        "awake_census": awake_census,
        "owners_with_subscription_cli": len(sub_owners),
        "owners_with_api_key_http": len(http_owners),
        "subscription_cli_definitions": sub_defs,
        "api_key_http_definitions": http_defs,
        "assignment_members": {**membership, "owners": len(member_owners)},
        "failures": public_failures,
        "passed_file_checks": {
            "owners": len(passed_sub_owners),
            "definitions": passed_sub,
        },
        "passed_api_key_http": {"owners": len(passed_http_owners), "definitions": passed_http},
        "cli_status": "not_run",
        "canary_sign_in": {
            "codes": canary_refusal_codes(canary_journal),
            "sources": canary_sources or {},
        },
        "ledger_tables": ledger_tables if ledger_tables is not None else ledger_totals(None),
        "ta_cli": ta_cli or {"present": 0, "absent": 0},
        "subscription_state": subscription_state or {"present": 0, "absent": 0},
        "broker_dates": broker_dates or {"outbound.db": None, "credential-custody.db": None},
    }


def safe_json(report: dict, private: set[str]) -> str:
    leaves = _data_leaves(report)
    for secret in private:
        if secret and any(secret in leaf for leaf in leaves):
            raise SystemExit("diagnostic refused: output_contains_private_value")
    return json.dumps(report, sort_keys=True)


def _harness_package(harness: Path) -> Path | None:
    candidates = [harness / "app", *harness.glob("opt/venv/lib/python3.*/site-packages"),
                  *harness.glob("usr/local/lib/python3.*/site-packages")]
    for candidate in candidates:
        if (candidate / "tinyassets" / "providers" / "source_records").is_dir():
            return candidate
    return None


def _current_harness() -> Path:
    state = json.loads(Path("/var/lib/boxhostd/state/images.json").read_text(encoding="utf-8"))
    name = state.get("current")
    root = Path("/opt/tinyassets/boxhostd")
    if not isinstance(name, str) or not (root / name).is_dir():
        name = os.readlink(root / "harness-current")
    path = root / name
    if path.is_symlink() or not path.is_dir():
        raise OSError("harness image is unavailable")
    return path


def _data_root() -> Path:
    completed = subprocess.run(
        ["docker", "volume", "inspect", "tinyassets-data", "--format", "{{.Mountpoint}}"],
        check=True, capture_output=True, text=True, timeout=30,
    )
    root = Path(completed.stdout.strip())
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise OSError("data volume is unavailable")
    return root


def _owners(data_root: Path) -> list[tuple[int, str]]:
    path = data_root / ".broker" / "state" / "owner-identities.db"
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        rows = db.execute("SELECT uid, principal FROM owner_volumes").fetchall()
    found = []
    for uid, principal in rows:
        if (type(uid) is not int or uid not in _UID
                or not isinstance(principal, str) or not principal):
            continue
        found.append((uid, principal))
    return found


def _awake() -> tuple[set[int], str]:
    runsc = Path("/opt/tinyassets/boxhostd/current/runsc")
    state = Path("/var/lib/boxhostd/state/runsc")
    if not runsc.is_file():
        return set(), "unavailable"
    completed = subprocess.run(
        [str(runsc), "--root", str(state), "list", "--format=json"],
        capture_output=True, text=True, timeout=20,
    )
    if completed.returncode != 0:
        return set(), "unavailable"
    try:
        payload = json.loads(completed.stdout or "[]")
    except json.JSONDecodeError:
        return set(), "unavailable"
    if payload is None:
        payload = []
    found = set()
    for item in payload:
        if not isinstance(item, dict) or item.get("status") != "running":
            continue
        box = item.get("id")
        if not isinstance(box, str) or not box.startswith("owner-"):
            continue
        number = box.removeprefix("owner-")
        if number.isdigit() and int(number) in _UID:
            found.add(int(number))
    return found, "runsc"


def _boxhostd_pid() -> int | None:
    completed = subprocess.run(
        ["systemctl", "show", "-p", "MainPID", "--value", "boxhostd.service"],
        capture_output=True, text=True, timeout=10,
    )
    if completed.returncode != 0:
        return None
    try:
        pid = int(completed.stdout.strip())
    except ValueError:
        return None
    if pid <= 1 or not Path(f"/proc/{pid}").is_dir():
        return None
    return pid


def _private_namespace() -> bool:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.unshare(0x00020000) != 0:  # CLONE_NEWNS
        return False
    subprocess.run(["mount", "--make-rprivate", "/"], check=True, timeout=10)
    return True


@contextmanager
def _readonly_image(image: Path, dest: Path):
    dest.mkdir(mode=0o700, exist_ok=False)
    subprocess.run(mount_argv(image, dest), check=True, timeout=30)
    try:
        yield dest
    finally:
        subprocess.run(umount_argv(dest), check=False, timeout=30)
        dest.rmdir()


_CANARY_NEEDLE = "canary_sign_in refused code="
_CANARY_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_CAPTURE_LIMIT = 262144


def _capture(argv: list[str], run, *, stderr: bool = False, timeout: int = 20) -> str:
    try:
        completed = run(argv, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    raw = getattr(completed, "stdout", b"") or b""
    if stderr:
        err = getattr(completed, "stderr", b"") or b""
        if raw and err and not raw.endswith(b"\n"):
            raw += b"\n"
        raw += err
    return raw.decode("utf-8", "replace")[:_CAPTURE_LIMIT]


# The same closed vocabularies the box logs with (subscription_runtime): a line
# carrying any other word is dropped, never reported.
_RUN_SOURCES = ("cerebras", "claude-code", "codex", "gemini", "google_ai_studio", "groq",
                "kimi", "mistral", "openrouter_user_models_v1", "custom")
_RUN_EVENTS = ("thread.started", "turn.started", "turn.completed", "turn.failed",
               "item.started", "item.updated", "item.completed", "error", "system",
               "assistant", "user", "result", "stream_event", "other")
_RUN_EVENT = "(?:" + "|".join(re.escape(name) for name in _RUN_EVENTS) + ")"
_RUN_LINE = re.compile(
    r"subscription run (ended|exit): source=("
    + "|".join(re.escape(name) for name in _RUN_SOURCES) + r") seconds=(\d{1,6}) "
    r"prompt_bytes=(\d{1,9}) output_bytes=(\d{1,9}) events=(none|"
    + _RUN_EVENT + "(?:," + _RUN_EVENT + r"){0,11})\s*\Z")


def _subscription_runs(run=None) -> dict:
    """Owner boxes log to boxhostd's journal. Only the fixed failure shape is kept."""
    run = run or subprocess.run
    text = _capture([
        "journalctl", "-u", "boxhostd", "--since", "12 hours ago",
        "--no-pager", "--output=cat", "-g", "subscription run ",
    ], run)
    counts: dict[str, int] = {}
    recent: list[dict] = []
    for line in text.splitlines():
        match = _RUN_LINE.search(line)
        if match is None:
            continue
        how, source, seconds, prompt, output, events = match.groups()
        counts[how] = counts.get(how, 0) + 1
        recent.append({"how": how, "source": source, "seconds": int(seconds),
                       "prompt_bytes": int(prompt), "output_bytes": int(output),
                       "events": events})
    return {"counts": counts, "recent": recent[-10:]}


# Host-side box health. Command names come from a fixed list and error text is
# kept only in a closed shape, so no owner data or path reaches the report.
_HOLDER_COMMANDS = frozenset({
    "rclone", "cp", "dd", "gzip", "zstd", "tar", "rsync", "e2fsck", "resize2fs",
    "fsfreeze", "mount", "umount", "losetup", "sqlite3", "runsc",
    "runsc-sandbox", "runsc-gofer", "python3", "node",
})
_BOX_ERROR = re.compile(r"owner daemon uid=\d{1,9} failures=(\d{1,6}): (.{1,300})\Z")
# The box host's own fixed lifecycle messages (tinyassets/boxes). Any other text,
# including an exception's own words, is reported only as "other".
_BOX_MESSAGES = (
    "broker launch authorization is missing", "broker owner reservation changed",
    "cannot prove the owner sandbox stopped", "harness image identity mismatch",
    "immutable harness image unavailable", "local image is absent",
    "owner app socket did not become ready", "owner daemon crashed",
    "owner daemon did not flush before shutdown deadline",
    "owner daemon exited during startup", "owner daemon is still draining",
    "owner daemon stopped answering", "owner lifecycle is unavailable",
    "owner relay socket is unavailable",
    "owner sandbox has an unverified launch or stop",
    "owner work must drain before volume operation", "runsc returned no sandbox PID",
    "runtime filesystem references remain", "the harness rootfs is unavailable or a link",
    "the pinned runsc binary is unavailable", "unexpected sandbox bundle",
    "volume must be fully detached", "volume operation requires a stopped box",
)
_STARTUP = "owner daemon startup failed: "


def _error_shape(text: str) -> str:
    text = text.strip()
    prefix = ""
    if text.startswith(_STARTUP):
        prefix, text = _STARTUP, text[len(_STARTUP):]
    return prefix + text if text in _BOX_MESSAGES else prefix + "other"


def _box_health(run=None) -> dict:
    run = run or subprocess.run
    seconds_by_pid: dict[str, int] = {}
    long_running: list[dict] = []
    for line in _capture(["ps", "-eo", "pid=,etimes=,comm="], run).splitlines():
        parts = line.split(None, 2)
        if len(parts) != 3 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        seconds_by_pid[parts[0]] = int(parts[1])
        command = parts[2].strip()
        if command in _HOLDER_COMMANDS and command not in ("python3", "node") \
                and int(parts[1]) >= 60:
            long_running.append({"command": command, "seconds": int(parts[1])})
    long_running.sort(key=lambda item: -item["seconds"])
    locks: list[dict] = []
    for line in _capture(["lslocks", "--noheadings", "--raw", "-o",
                          "COMMAND,PID,MODE,PATH"], run).splitlines():
        parts = line.split(" ", 3)
        if len(parts) != 4 or not re.search(r"/\.locks/\d{1,9}\Z", parts[3]):
            continue
        command = parts[0] if parts[0] in _HOLDER_COMMANDS else "other"
        locks.append({"command": command,
                      "mode": {"READ": "READ", "WRITE": "WRITE", "READ*": "READ waiting",
                               "WRITE*": "WRITE waiting"}.get(parts[2], "other"),
                      "seconds": seconds_by_pid.get(parts[1])})
    errors: dict[str, int] = {}
    worst = 0
    for line in _capture(["journalctl", "-u", "boxhostd", "--since", "12 hours ago",
                          "--no-pager", "--output=cat", "-n", "2000",
                          "-g", "owner daemon uid="],
                         run).splitlines():
        match = _BOX_ERROR.search(line.strip())
        if match is None:
            continue
        worst = max(worst, int(match.group(1)))
        shape = _error_shape(match.group(2))
        errors[shape] = errors.get(shape, 0) + 1
    return {"long_running": long_running[:20], "volume_locks": locks[:40],
            "box_errors": errors, "most_failures": worst}


def _canary_container(name: str) -> bool:
    if not _CANARY_NAME.fullmatch(name):
        return False
    lowered = name.lower()
    return lowered == "tinyassets-daemon" or "broker" in lowered or "router" in lowered


def _canary_census(run=None) -> tuple[str, dict[str, int]]:
    """Validated refusal lines only. Source values are lines scanned, never text."""
    run = run or subprocess.run
    seen: set[str] = set()
    kept: list[str] = []
    sources: dict[str, int] = {}

    def absorb(name: str, text: str) -> None:
        lines = text.splitlines()
        sources[name] = len(lines)
        for line in lines:
            if _CANARY_NEEDLE not in line or line in seen or _403.search(line) is None:
                continue
            seen.add(line)
            kept.append(line)

    absorb("journalctl_unit", _capture([
        "journalctl", "-u", "tinyassets-daemon", "--since", "6 hours ago",
        "--no-pager", "--output=cat", "-g", _CANARY_NEEDLE,
    ], run))
    absorb("journalctl", _capture([
        "journalctl", "--since", "12 hours ago",
        "--no-pager", "--output=cat", "-g", _CANARY_NEEDLE,
    ], run))
    names = _capture(["docker", "ps", "--format", "{{.Names}}"], run).splitlines()
    containers = ["tinyassets-daemon"]
    for raw_name in names:
        name = raw_name.strip()
        if name not in containers and _canary_container(name):
            containers.append(name)
    for name in containers:
        absorb("docker:" + name, _capture(
            ["docker", "logs", "--since", "12h", name], run, stderr=True))
    return "\n".join(kept), sources


def _mtime_date(path: Path) -> str | None:
    if path.is_symlink():
        return None
    if path.is_file():
        stamp = path.stat().st_mtime
    elif path.is_dir():
        stamps = [
            child.stat().st_mtime for child in path.iterdir()
            if not child.is_symlink() and child.is_file()
        ]
        if not stamps:
            return None
        stamp = max(stamps)
    else:
        return None
    return datetime.fromtimestamp(stamp, timezone.utc).date().isoformat()


def _broker_dates(data: Path) -> dict[str, str | None]:
    broker = data / ".broker"
    return {
        "outbound.db": _mtime_date(broker / "outbound.db"),
        "credential-custody.db": _mtime_date(broker / "credential-custody.db"),
    }


def _image(uid: int) -> Path:
    return Path(f"/var/lib/boxhostd/owners/{uid}.img")


def collect() -> tuple[dict, set[str]]:
    if os.geteuid() != 0:
        raise SystemExit("diagnostic refused: not_root")
    data = _data_root()
    owners = _owners(data)
    awake, census = _awake()
    # An unknown awake set makes a loop mount of a live image possible.
    if census != "runsc":
        awake = set()
        asleep_inspection = "unavailable"
    else:
        try:
            asleep_inspection = "readonly_loop" if _private_namespace() else "unavailable"
        except (OSError, subprocess.CalledProcessError):
            asleep_inspection = "unavailable"
    harness = _current_harness()
    package = _harness_package(harness)
    if package is None:
        raise OSError("harness source records are unavailable")
    sys.path.insert(0, str(package))
    cli_root = harness / "opt" / "tinyassets" / "cli"
    ledger_path = data / ".broker" / "outbound.db"
    private: set[str] = set()
    results = []
    missing = unreadable = 0
    ta_present = ta_absent = 0
    state_present = state_absent = 0
    pid = _boxhostd_pid()
    images = {
        int(path.name.removesuffix(".img"))
        for path in Path("/var/lib/boxhostd/owners").glob("[0-9]*.img")
        if path.is_file() and not path.is_symlink() and path.name.removesuffix(".img").isdigit()
        and int(path.name.removesuffix(".img")) in _UID
    }
    bound = {uid for uid, _ in owners}
    ledger = None
    if ledger_path.is_file() and not ledger_path.is_symlink():
        ledger = sqlite3.connect(ledger_path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        for uid, principal in owners:
            private.add(principal)
            account = hashlib.sha256(principal.encode()).hexdigest()
            private.add(account)
            vault_path = (data / ".broker" / "accounts" / account
                          / "credentials" / ".subscription-state.json")
            if vault_path.is_file() and not vault_path.is_symlink():
                state_present += 1
            else:
                state_absent += 1
            image = _image(uid)
            try:
                if uid in awake:
                    if pid is None:
                        raise OSError("boxhostd is not running")
                    view = NsenterView(pid, uid)
                    if not view.mounted():
                        raise OSError("awake mount is not readable")
                    inspected = inspect_owner(
                        view, principal, cli_root=cli_root,
                        vault_path=vault_path, ledger=ledger)
                elif not image.is_file() or image.is_symlink():
                    missing += 1
                    continue
                elif asleep_inspection != "readonly_loop":
                    raise OSError("asleep inspection is unavailable")
                else:
                    # A box can wake after _awake(). losetup -j is the live
                    # attachment, and a second loop mount of that image is refused.
                    attached = subprocess.run(
                        ["losetup", "-j", str(image)],
                        capture_output=True, timeout=10,
                    )
                    if attached.returncode != 0 or attached.stdout or attached.stderr:
                        raise OSError("asleep image is already attached")
                    dest = Path(f"/tmp/ta-ro-{uid}")
                    with _readonly_image(image, dest) as mounted:
                        inspected = inspect_owner(
                            LocalView(mounted), principal, cli_root=cli_root,
                            vault_path=vault_path, ledger=ledger)
            except (OSError, ValueError, sqlite3.Error, subprocess.CalledProcessError):
                unreadable += 1
                results.append(_unreadable())
                continue
            private |= inspected.pop("vault_secrets")
            if inspected.pop("ta_cli", False):
                ta_present += 1
            else:
                ta_absent += 1
            results.append(inspected)
        tables = ledger_totals(ledger)
    finally:
        if ledger is not None:
            ledger.close()
    asleep = len(bound - awake) - missing
    canary_journal, canary_sources = _canary_census()
    report = build_report(
        results, awake=len(awake & bound), asleep=asleep,
        missing_image=missing, image_without_binding=len(images - bound),
        unreadable=unreadable, asleep_inspection=asleep_inspection,
        awake_census=census, canary_journal=canary_journal,
        canary_sources=canary_sources, ledger_tables=tables,
        ta_cli={"present": ta_present, "absent": ta_absent},
        subscription_state={"present": state_present, "absent": state_absent},
        broker_dates=_broker_dates(data),
    )
    report["subscription_runs"] = _subscription_runs()
    return report, private


def main() -> None:
    report, private = collect()
    report["box_health"] = _box_health()
    sys.stdout.write(safe_json(report, private) + "\n")


if __name__ == "__main__":
    main()
