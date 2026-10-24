"""Read-only connected-source census: counts, first failure, no secrets."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/diagnose_connected_sources.py"
WORKFLOW = ROOT / ".github/workflows/diagnose-connected-sources.yml"

pytestmark = pytest.mark.verifies("owner-daemon-in-box:C11")

PRINCIPAL = "leak-principal-9f3a"
OTHER = "other-owner-should-not-print"
DEFINITION_ID = "provdef_should_not_leak"
TOKEN = "LEAK-TOKEN-0123456789abcdef"
CENTER = "u-test"


def _load():
    spec = importlib.util.spec_from_file_location("diagnose_connected_sources", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


diag = _load()


@pytest.fixture(scope="module")
def shipped():
    from tinyassets.providers.subscription_source import digest, sources

    return sources(None), digest


def _row(ref, owner=PRINCIPAL):
    return {
        "id": DEFINITION_ID,
        "owner_user_id": owner,
        "universe_id": CENTER,
        "ref": ref,
        "access_method": "subscription_cli",
    }


def _binary(root: Path, source) -> None:
    install = source.sign_in["cli_login"]["install"]
    relative = (
        f".ta-cli/{source['id']}/venv/bin/{install['bin']}"
        if install["manager"] == "pip"
        else f".ta-cli/{source['id']}/node_modules/.bin/{install['bin']}"
    )
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"bin")


def _marker(root: Path, source_id: str, presented: str, incarnation: str = "inc-1") -> None:
    path = root / ".ta-cli" / source_id / "connection"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"source": presented, "incarnation": incarnation}), encoding="utf-8")


def _definitions(root: Path, rows: list) -> None:
    center = root / CENTER
    center.mkdir(parents=True, exist_ok=True)
    (center / "provider_definitions.json").write_text(json.dumps(rows), encoding="utf-8")


def _vault(tmp: Path, payload: dict) -> Path:
    path = tmp / "vault.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _claude_vault(document: bytes) -> tuple[dict, str]:
    blob = base64.b64encode(document).decode()
    payload = {"claude-code": {"revision": 1, "state": {".claude/.credentials.json": blob}}}
    return payload, blob


def _inspect(root: Path, vault: Path | None, **kwargs):
    return diag.inspect_owner(
        diag.LocalView(root), PRINCIPAL, cli_root=kwargs.get("cli_root"),
        vault_path=vault, ledger=kwargs.get("ledger"))


def _report(results, **kwargs):
    fields = dict(
        awake=0, asleep=0, missing_image=0, image_without_binding=0, unreadable=0,
        asleep_inspection="readonly_loop", awake_census="runsc", canary_journal="")
    fields.update(kwargs)
    return diag.build_report(results, **fields)


def test_file_checks_pass_without_printing_secrets(tmp_path, shipped):
    sources, digest = shipped
    source = sources["claude-code"]
    root = tmp_path / "owner"
    _definitions(root, [_row("claude-code")])
    _marker(root, "claude-code", digest(source))
    _binary(root, source)
    payload, blob = _claude_vault(json.dumps({"token": TOKEN}).encode())
    result = _inspect(root, _vault(tmp_path, payload))
    assert result["subscription"] == [None]
    assert result["http"] == []
    report = _report([result], awake=1)
    private = {PRINCIPAL, TOKEN, blob, DEFINITION_ID}
    text = diag.safe_json(report, private)
    for secret in private:
        assert secret not in text
    assert report["owners_with_subscription_cli"] == 1
    assert report["passed_file_checks"] == {"owners": 1, "definitions": 1}
    assert report["cli_status"] == "not_run"


def test_safe_json_refuses_a_planted_principal():
    with pytest.raises(SystemExit) as planted:
        diag.safe_json({"note": PRINCIPAL}, {PRINCIPAL})
    assert str(planted.value) == "diagnostic refused: output_contains_private_value"
    assert PRINCIPAL not in str(planted.value)
    account = hashlib.sha256(PRINCIPAL.encode()).hexdigest()
    coded = _report([], canary_journal=(
        "canary_sign_in refused code=password_refused workos=" + account + "\n"))
    with pytest.raises(SystemExit) as hashed:
        diag.safe_json(coded, {account})
    assert str(hashed.value) == "diagnostic refused: output_contains_private_value"
    assert account not in str(hashed.value)


def test_ordinary_vault_words_do_not_refuse(tmp_path):
    vault = {"claude-code": {"state": {
        ".claude/.credentials.json": "subscription_cli",
        "status": "connected",
        "phase": "authenticated",
    }}}
    private = diag._vault_secrets(vault)
    assert private == set()
    report = _report(
        [{"subscription": ["authenticated_state_check"], "http": [], "owner_errors": []}],
        canary_journal="canary_sign_in refused code=authenticated_other_account\n"
        "canary_sign_in refused code=wrong_door_key\n",
    )
    text = diag.safe_json(report, private)
    assert "subscription_cli" in text and "authenticated" in text
    assert diag.safe_json(report, {"subscription_cli", "connected"}) == text
    assert tmp_path.name not in text


def test_credential_in_a_canary_code_or_leaf_refuses():
    secret = "k9" * 20
    assert diag._credential_like(secret)
    journal = "canary_sign_in refused code=password_refused workos=" + secret + "\n"
    coded = _report([], canary_journal=journal)
    with pytest.raises(SystemExit) as coded_refusal:
        diag.safe_json(coded, {secret})
    assert str(coded_refusal.value) == "diagnostic refused: output_contains_private_value"
    assert secret not in str(coded_refusal.value)
    leaf = _report([], canary_journal="canary_sign_in refused code=wrong_door_key\n")
    leaf["awake_census"] = secret
    with pytest.raises(SystemExit) as leaf_refusal:
        diag.safe_json(leaf, {secret})
    assert str(leaf_refusal.value) == "diagnostic refused: output_contains_private_value"


def test_not_root_refusal_names_the_check(monkeypatch):
    monkeypatch.setattr(diag.os, "geteuid", lambda: 1)
    with pytest.raises(SystemExit) as refused:
        diag.collect()
    assert str(refused.value) == "diagnostic refused: not_root"


def test_missing_marker_fails_before_a_missing_owner_binary(tmp_path, shipped):
    root = tmp_path / "owner"
    _definitions(root, [_row("claude-code")])
    result = _inspect(root, None)
    assert result["subscription"] == ["require_accepted_missing_marker"]


def test_digest_mismatch_and_legacy_digest(tmp_path, shipped):
    sources, digest = shipped
    source = sources["claude-code"]
    current = digest(source)
    legacy = diag.legacy_digests(source)
    assert legacy and current not in legacy

    mismatch = tmp_path / "mismatch"
    _definitions(mismatch, [_row("claude-code")])
    _marker(mismatch, "claude-code", "0" * 64)
    _binary(mismatch, source)
    payload, _blob = _claude_vault(b"{}")
    assert _inspect(mismatch, _vault(tmp_path, payload))["subscription"] == [
        "require_accepted_digest_mismatch"]

    accepted = tmp_path / "accepted"
    _definitions(accepted, [_row("claude-code")])
    _marker(accepted, "claude-code", next(iter(legacy)))
    _binary(accepted, source)
    assert _inspect(accepted, _vault(accepted, payload))["subscription"] == [None]


def test_empty_custody_and_codex_state_check(tmp_path, shipped):
    sources, digest = shipped
    claude = sources["claude-code"]
    empty = tmp_path / "empty"
    _definitions(empty, [_row("claude-code")])
    _marker(empty, "claude-code", digest(claude))
    _binary(empty, claude)
    assert _inspect(empty, None)["subscription"] == ["custody_and_capture_empty"]

    codex = sources["codex"]
    root = tmp_path / "codex"
    _definitions(root, [_row("codex")])
    _marker(root, "codex", digest(codex))
    _binary(root, codex)
    blob = base64.b64encode(b"{}").decode()
    vault = _vault(root, {"codex": {"revision": 1, "state": {".codex/auth.json": blob}}})
    assert _inspect(root, vault)["subscription"] == ["authenticated_state_check"]


def test_scope_mismatch_symlink_marker_and_unreadable_marker(tmp_path, shipped):
    sources, digest = shipped
    source = sources["claude-code"]
    scoped = tmp_path / "scoped"
    _definitions(scoped, [_row("claude-code", owner=OTHER)])
    result = _inspect(scoped, None)
    assert result["subscription"] == ["require_scope"]
    assert OTHER not in diag.safe_json(_report([result]), {OTHER, PRINCIPAL})

    linked = tmp_path / "linked"
    _definitions(linked, [_row("claude-code")])
    marker = linked / ".ta-cli" / "claude-code" / "connection"
    marker.parent.mkdir(parents=True)
    marker.symlink_to(linked / "outside")
    assert _inspect(linked, None)["subscription"] == ["connection_marker_unreadable"]

    broken = tmp_path / "broken"
    _definitions(broken, [_row("claude-code")])
    path = broken / ".ta-cli" / "claude-code" / "connection"
    path.parent.mkdir(parents=True)
    path.write_text("{", encoding="utf-8")
    assert _inspect(broken, None)["subscription"] == ["require_accepted_unreadable"]

    blank = tmp_path / "blank"
    _definitions(blank, [_row("claude-code")])
    _marker(blank, "claude-code", digest(source), incarnation="")
    assert _inspect(blank, None)["subscription"] == ["require_accepted_missing_incarnation"]


def test_image_lock_mismatch_precedes_the_marker_and_a_good_image_supplies_the_binary(
        tmp_path, shipped):
    sources, digest = shipped
    source = sources["claude-code"]
    install = source.sign_in["cli_login"]["install"]
    expected = digest({"install": install, "registry": source["egress"]["registry"]})

    broken = tmp_path / "cli-broken" / "claude-code"
    broken.mkdir(parents=True)
    (broken / "package-lock.json").write_bytes(b"lock")
    (broken / "installed.json").write_text(json.dumps({
        "source": expected, "lock": "f" * 64}), encoding="utf-8")
    root = tmp_path / "owner-broken"
    _definitions(root, [_row("claude-code")])
    assert _inspect(root, None, cli_root=broken.parent)["subscription"] == ["binary_lock_mismatch"]

    good = tmp_path / "cli-good" / "claude-code"
    lock = b'{"name":"claude"}\n'
    good.mkdir(parents=True)
    (good / "package-lock.json").write_bytes(lock)
    (good / "installed.json").write_text(json.dumps({
        "source": expected, "lock": hashlib.sha256(lock).hexdigest()}), encoding="utf-8")
    binary = good / "node_modules" / ".bin" / "claude"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"bin")
    ready = tmp_path / "owner-good"
    _definitions(ready, [_row("claude-code")])
    _marker(ready, "claude-code", digest(source))
    payload, _blob = _claude_vault(b"{}")
    result = _inspect(ready, _vault(ready, payload), cli_root=good.parent)
    assert result["subscription"] == [None]


def test_image_npm_symlink_binary_is_not_missing(tmp_path, shipped):
    sources, digest = shipped
    source = sources["claude-code"]
    install = source.sign_in["cli_login"]["install"]
    expected = digest({"install": install, "registry": source["egress"]["registry"]})
    directory = tmp_path / "cli" / "claude-code"
    lock = b'{"name":"claude"}\n'
    directory.mkdir(parents=True)
    (directory / "package-lock.json").write_bytes(lock)
    (directory / "installed.json").write_text(json.dumps({
        "source": expected, "lock": hashlib.sha256(lock).hexdigest()}), encoding="utf-8")
    target = directory / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"bin")
    binary = directory / "node_modules" / ".bin" / install["bin"]
    binary.parent.mkdir(parents=True)
    binary.symlink_to(Path("..") / "@anthropic-ai" / "claude-code" / "cli.js")
    assert binary.is_symlink() and binary.is_file()
    owner = tmp_path / "owner"
    _definitions(owner, [_row("claude-code")])
    _marker(owner, "claude-code", digest(source))
    result = _inspect(owner, None, cli_root=directory.parent)
    assert result["subscription"] != ["binary_missing"]


def test_symlink_vault_is_not_followed(tmp_path, shipped):
    sources, digest = shipped
    source = sources["claude-code"]
    root = tmp_path / "owner"
    _definitions(root, [_row("claude-code")])
    _marker(root, "claude-code", digest(source))
    _binary(root, source)
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(_claude_vault(TOKEN.encode())[0]), encoding="utf-8")
    link = tmp_path / "vault-link.json"
    link.symlink_to(outside)
    assert _inspect(root, link)["subscription"] == ["custody_read:OSError"]
    assert TOKEN not in diag.safe_json(_report([{"subscription": ["custody_read:OSError"],
                                                  "http": [], "owner_errors": []}]), {TOKEN})


def test_http_grant_failures_and_a_passing_capability(tmp_path):
    db = sqlite3.connect(tmp_path / "outbound.db")
    db.executescript(
        """
        CREATE TABLE outbound_connection_grants (
            grant_id TEXT, owner_user_id TEXT, revoked_at TEXT, connection_id TEXT);
        CREATE TABLE outbound_connections (
            connection_id TEXT, owner_user_id TEXT, revoked_at TEXT);
        CREATE TABLE connection_capabilities (
            connection_id TEXT, capability_kind TEXT);
        """
    )
    assert diag.classify_http(PRINCIPAL, "grant-1", None) == "ledger:OSError"
    assert diag.classify_http(PRINCIPAL, "grant-1", db) == "ledger:GrantResolutionError"
    db.execute(
        "INSERT INTO outbound_connection_grants VALUES ('grant-1', ?, NULL, 'conn-1')",
        (PRINCIPAL,))
    db.execute(
        "INSERT INTO outbound_connections VALUES ('conn-1', ?, NULL)", (PRINCIPAL,))
    assert diag.classify_http(PRINCIPAL, "grant-1", db) == "ledger_not_model_source"
    db.execute("INSERT INTO connection_capabilities VALUES ('conn-1', 'model_use')")
    assert diag.classify_http(PRINCIPAL, "grant-1", db) is None
    db.close()


def test_aggregate_counts_owners_once_and_keeps_the_canary_code(tmp_path):
    journal = (
        "canary_sign_in refused code=wrong_door_key\n"
        "canary_sign_in refused code=password_refused workos=invalid_credentials\n"
        "canary_sign_in refused code=not_a_code user_01SECRET\n"
    )
    results = [
        {"subscription": ["require_scope", "require_scope"],
         "http": ["ledger:GrantResolutionError"], "owner_errors": ["centers_unreadable"]},
        {"subscription": [None], "http": [None], "owner_errors": []},
        {"subscription": [], "http": [], "owner_errors": ["tree_unreadable"]},
    ]
    report = _report(
        results, awake=1, asleep=2, missing_image=3, image_without_binding=4,
        unreadable=1, asleep_inspection="unavailable", awake_census="unavailable",
        canary_journal=journal)
    assert report["owners"] == 6
    assert report["awake"] == 1 and report["asleep"] == 2 and report["missing_image"] == 3
    assert report["asleep_inspection"] == "unavailable"
    assert report["owners_with_subscription_cli"] == 2
    assert report["owners_with_api_key_http"] == 2
    assert report["subscription_cli_definitions"] == 3
    assert report["api_key_http_definitions"] == 2
    assert report["failures"]["require_scope"] == {"owners": 1, "definitions": 2}
    assert report["failures"]["ledger:GrantResolutionError"] == {"owners": 1, "definitions": 1}
    assert report["failures"]["centers_unreadable"] == {"owners": 1, "definitions": 0}
    assert report["failures"]["tree_unreadable"] == {"owners": 1, "definitions": 0}
    assert report["passed_file_checks"] == {"owners": 1, "definitions": 1}
    assert report["passed_api_key_http"] == {"owners": 1, "definitions": 1}
    codes = report["canary_sign_in"]["codes"]
    assert codes["wrong_door_key"] == 1
    assert codes["password_refused workos=invalid_credentials"] == 1
    text = diag.safe_json(report, {"user_01SECRET", PRINCIPAL})
    assert "user_01SECRET" not in text and "not_a_code" not in text
    assert tmp_path.name not in text


def test_owner_view_rejects_paths_and_awake_identity():
    with pytest.raises(ValueError):
        diag.LocalView(Path("/tmp")).read_bytes("../secret")
    with pytest.raises(ValueError):
        diag.NsenterView(1, 300001)
    with pytest.raises(ValueError):
        diag.NsenterView(2, 1000)


def test_awake_listing_reads_the_existing_mount_root():
    calls = []

    def run(_pid, argv):
        calls.append(argv)

        class Done:
            returncode = 0
            stdout = b"u-test\n"

        return Done()

    view = diag.NsenterView(2, 300001, run=run)
    assert view.list_dir("") == ["u-test"]
    assert calls[0][-1] == "/var/lib/boxhostd/mounts/300001"
    assert view.mounted() is True


def test_asleep_mount_is_read_only_and_the_script_does_not_mutate():
    image, dest = Path("/var/lib/boxhostd/owners/300001.img"), Path("/tmp/ta-ro-300001")
    assert diag.mount_argv(image, dest) == [
        "mount", "-t", "xfs", "-o", diag.READONLY_MOUNT_OPTIONS, str(image), str(dest)]
    assert diag.READONLY_MOUNT_OPTIONS == "ro,norecovery,nosuid,nodev,loop"
    assert diag.umount_argv(dest) == ["umount", "--no-canonicalize", str(dest)]
    text = SCRIPT.read_text(encoding="utf-8")
    for banned in (
        "nouuid", "mkfs", "volumes.restore", "umount -l", "umount -f", "os.replace",
        "attach(", "lease(", "migrate(", "query_ledger(", "require_accepted(",
        "SubscriptionRuntime", "authenticated(", "context(", "systemctl restart",
    ):
        assert banned not in text, banned


def test_attached_asleep_image_is_not_mounted_and_counts_unreadable(tmp_path, monkeypatch):
    image = tmp_path / "300001.img"
    image.write_bytes(b"image")
    data = tmp_path / "data"
    data.mkdir()
    commands = []

    def run(argv, **kwargs):
        commands.append(list(argv))
        if argv[:2] == ["losetup", "-j"]:
            assert argv[2] == str(image)
            assert kwargs.get("timeout") == 10

            class Done:
                returncode = 0
                stdout = b"/dev/loop0: [0001]:1 (/var/lib/boxhostd/owners/300001.img)\n"
                stderr = b""

            return Done()
        raise OSError("mounted an attached image")

    monkeypatch.setattr(diag.os, "geteuid", lambda: 0)
    monkeypatch.setattr(diag, "_data_root", lambda: data)
    monkeypatch.setattr(diag, "_owners", lambda _root: [(300001, PRINCIPAL)])
    monkeypatch.setattr(diag, "_awake", lambda: (set(), "runsc"))
    monkeypatch.setattr(diag, "_private_namespace", lambda: True)
    monkeypatch.setattr(diag, "_current_harness", lambda: tmp_path)
    monkeypatch.setattr(diag, "_harness_package", lambda _harness: tmp_path)
    monkeypatch.setattr(diag, "_boxhostd_pid", lambda: None)
    monkeypatch.setattr(diag, "_journal", lambda: "")
    monkeypatch.setattr(diag, "_image", lambda _uid: image)
    monkeypatch.setattr(diag.subprocess, "run", run)
    report, private = diag.collect()
    assert commands == [["losetup", "-j", str(image)]]
    assert all(argv[0] != "mount" for argv in commands)
    assert report["unreadable"] == 1
    assert PRINCIPAL in private


def test_workflow_is_a_manual_production_read():
    text = WORKFLOW.read_text(encoding="utf-8")
    workflow = yaml.safe_load(text)
    assert workflow.get("on", workflow.get(True)) == {"workflow_dispatch": None}
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"] == {
        "group": "production-host-observation", "cancel-in-progress": False}
    job = workflow["jobs"]["diagnose"]
    assert job["environment"] == "production"
    assert "inputs:" not in text
    assert "sudo python3 -B" in text
    assert "scripts/diagnose_connected_sources.py" in text
    for banned in ("systemctl", "compose", "mkfs", "volumes.restore", "mount "):
        assert banned not in text, banned
