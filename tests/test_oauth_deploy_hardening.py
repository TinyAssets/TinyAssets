"""PR #4443: runner prerequisites, rollback compatibility and whole-pair writes."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts.validate_oauth_provider_credentials import ID, SECRET, target_protects_children

# Resolve via PATH explicitly: Windows subprocess otherwise prefers the
# system32 WSL launcher, which does not inherit this harness environment.
BASH = shutil.which("bash")
assert BASH is not None, "OAuth deploy execution proof requires bash"
ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts/validate_oauth_provider_credentials.py"
HELPER = ROOT / "deploy/install-tinyassets-env.sh"
REMOTE = ROOT / "deploy/install-oauth-credentials.sh"
STEPS = yaml.safe_load((ROOT / ".github/workflows/deploy-prod.yml").read_text())["jobs"][
    "deploy"
]["steps"]
PROTECTED = '''def child_env(source):
    return {name: value for name, value in source.items()
            if name not in CHILD_FORBIDDEN_ENV and not name.startswith("TINYASSETS_OAUTH_")}
'''


@pytest.fixture
def revisions(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def git(*args):
        return subprocess.check_output(["git", *args], text=True).strip()

    git("init", "-q")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "Test")
    source = tmp_path / "tinyassets/platform_secrets.py"
    source.parent.mkdir()
    source.write_text("def child_env(source):\n    return dict(source)\n")
    git("add", ".")
    git("commit", "-qm", "unprotected")
    old = git("rev-parse", "HEAD")
    source.write_text(PROTECTED)
    registry = source.parent / "connection_oauth/providers.json"
    registry.parent.mkdir()
    registry.write_text("{}")
    git("add", ".")
    git("commit", "-qm", "protected")
    return old, git("rev-parse", "HEAD")


def validate(tmp_path, revision, client_id, secret):
    output = tmp_path / "output"
    output.write_text("")
    proc = subprocess.run(
        [sys.executable, str(VALIDATOR)], capture_output=True, text=True,
        env={**os.environ, ID: client_id, SECRET: secret,
             "TARGET_REVISION": revision, "GITHUB_OUTPUT": str(output)},
    )
    for value in (client_id, secret):
        if value:
            assert value not in proc.stdout + proc.stderr
    return proc, output.read_text()


@pytest.mark.parametrize("values", [("client-id", ""), ("", "GOCSPX-secret")])
def test_half_configured_warns_and_skips(tmp_path, revisions, values):
    proc, output = validate(tmp_path, revisions[1], *values)
    assert proc.returncode == 0
    assert "::warning::configure both" in proc.stdout
    assert output == "action=skip\n"


def test_absent_pair_skips_on_protected_target(tmp_path, revisions):
    proc, output = validate(tmp_path, revisions[1], "", "")
    assert proc.returncode == 0
    assert proc.stdout == ""
    assert output == "action=skip\n"


@pytest.mark.parametrize("bad", [
    "bad\nvalue", "bad\rvalue", '"quoted', "'quoted", "value #comment",
    " leading", "trailing ", "\tedge", "edge\u00a0", "$EXPAND", "back\\slash",
    "`command`", "$(command)",
])
@pytest.mark.parametrize("key", [ID, SECRET])
def test_format_refusal_is_name_only(tmp_path, revisions, bad, key):
    values = {ID: "client-id", SECRET: "GOCSPX-secret"}
    values[key] = bad
    proc, output = validate(tmp_path, revisions[1], values[ID], values[SECRET])
    assert proc.returncode == 1
    assert f"::error::{key}" in proc.stdout
    assert not output


def test_validation_precedes_every_host_contact():
    validation = next(s for s in STEPS if s.get("name") == "Validate OAuth provider credentials")
    assert validation["env"]["TARGET_REVISION"] == "${{ steps.tag.outputs.revision }}"
    assert validation["run"] == "python scripts/validate_oauth_provider_credentials.py"
    for step in STEPS:
        if any(token in step.get("run", "") for token in ("ssh ", "scp ", "ssh-keyscan ")):
            assert STEPS.index(validation) < STEPS.index(step)


def test_target_revision_not_checkout_controls_install(tmp_path, revisions):
    old, protected = revisions  # checkout is protected while deploying old
    assert not target_protects_children(old)
    proc, output = validate(tmp_path, old, "client-id", "GOCSPX-secret")
    assert proc.returncode == 0
    assert "::warning::target lacks" in proc.stdout
    assert output == "action=remove\n"
    assert target_protects_children(protected)
    proc, output = validate(tmp_path, protected, "client-id", "GOCSPX-secret")
    assert proc.returncode == 0
    assert output == "action=install\n"


@pytest.mark.parametrize("source,registry", [
    (PROTECTED, False),
    ('# not name.startswith("TINYASSETS_OAUTH_")\ndef child_env(s): return dict(s)', True),
    (PROTECTED.replace(" and not ", " or not "), True),
])
def test_target_requires_registry_and_actual_filter(tmp_path, revisions, source, registry):
    Path("tinyassets/platform_secrets.py").write_text(source)
    if not registry:
        Path("tinyassets/connection_oauth/providers.json").unlink()
    subprocess.run(["git", "add", "."], check=True)
    subprocess.run(["git", "commit", "-qm", "unsupported target"], check=True)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    assert not target_protects_children(revision)


def helper_env(tmp_path):
    return {**os.environ, "TINYASSETS_ENV_FILE": (tmp_path / "env").as_posix(),
            "TINYASSETS_DAEMON_ENV_SOURCE": (tmp_path / "env").as_posix(),
            "TINYASSETS_DAEMON_ENV_FILE": (tmp_path / "daemon.env").as_posix(),
            "TINYASSETS_ENV_OWNER": "", "TINYASSETS_ENV_READ_USER": ""}


@pytest.mark.parametrize("failure", ["none", "env", "daemon.env"])
def test_pair_is_atomic_even_when_commit_fails(tmp_path, failure):
    old = f"KEEP=yes\n{ID}=old-id\n{SECRET}=old-secret\n"
    for name in ("env", "daemon.env"):
        (tmp_path / name).write_text(old)
    # Inject a rename failure at either commit; use the actual writer otherwise.
    prelude = r'''
mv() {
    if [ "${@: -1}" = "$FAIL_DEST" ]; then return 23; fi
    command mv "$@"
}
export -f mv
'''
    proc = subprocess.run(
        [BASH, "-c", prelude + 'bash "$HELPER" set-pair "$ID_KEY" "$SECRET_KEY"'],
        # Preserve LF framing: text-mode stdin becomes CRLF on Windows.
        input=b"new-id\nGOCSPX-new-secret\n", capture_output=True,
        env={**helper_env(tmp_path), "HELPER": HELPER.as_posix(), "ID_KEY": ID,
             "SECRET_KEY": SECRET, "FAIL_DEST": (tmp_path / failure).as_posix()},
    )
    assert proc.returncode == (0 if failure == "none" else 3), proc.stderr
    for name in ("env", "daemon.env"):
        content = (tmp_path / name).read_text()
        assert (f"{ID}=new-id\n" in content) == (f"{SECRET}=GOCSPX-new-secret\n" in content)
        assert content.count(f"{ID}=") == content.count(f"{SECRET}=") == 1
        assert "KEEP=yes" in content
    assert b"GOCSPX-new-secret" not in proc.stdout + proc.stderr
    assert not list(tmp_path.glob(".*.tmp.*"))


@pytest.mark.parametrize("payload", ["only-one\n", "one\ntwo\nextra\n", "one\n", "one\n\n"])
def test_pair_rejects_incomplete_input_without_mutation(tmp_path, payload):
    original = "KEEP=yes\n"
    (tmp_path / "env").write_text(original)
    proc = subprocess.run(
        [BASH, HELPER.as_posix(), "set-pair", ID, SECRET], input=payload.encode(),
        capture_output=True, env=helper_env(tmp_path),
    )
    assert proc.returncode != 0
    assert (tmp_path / "env").read_text() == original


@pytest.mark.parametrize("action,protected,installed", [
    ("install", True, True), ("install", False, False),
    ("remove", True, False), ("skip", False, False), ("skip", True, True),
])
def test_workflow_transport_lock_and_rollback_cleanup(tmp_path, action, protected, installed):
    step = next(s for s in STEPS if s.get("name") == "Install OAuth provider client credentials")
    assert step["env"]["OAUTH_ACTION"] == "${{ steps.oauth.outputs.action }}"
    assert step["env"]["PREV_IMAGE"] == "${{ steps.capture.outputs.prev_image }}"
    for name in ("env", "daemon.env"):
        (tmp_path / name).write_text(f"KEEP=yes\n{ID}=old-id\n{SECRET}=old-secret\n")
    package = tmp_path / "tinyassets"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "platform_secrets.py").write_text(
        "CHILD_FORBIDDEN_ENV = set()\n" + (
            PROTECTED if protected else "def child_env(source): return dict(source)\n"
        )
    )
    prelude = r'''
scp() { return 0; }
ssh() {
    printf '%s\n' "$@" >> "$SSH_ARGS"
    bash -c "${@: -1}"
}
sudo() { "$@"; }
flock() {
    [ "$1" = -w ] && [ "$2" = 120 ] &&
        [ "$3" = /var/lock/tinyassets-host-mutation.lock ] || return 91
    shift 3
    export LOCK_HELD=1
    "$@"
}
timeout() { shift; "$@"; }
docker() {
    [ "$LOCK_HELD" = 1 ] || return 92
    [ "$1 $2 $3 $4 $5 $6" = 'run --rm --network none --entrypoint python' ] || return 93
    [ "$7" = "$PREV_IMAGE" ] || return 94
    (cd "$PROBE_DIR" && command python -c "$9")
}
bash() {
    case "$1" in
        /tmp/install-oauth-credentials.sh) shift; command bash "$REMOTE" "$@" ;;
        /tmp/install-tinyassets-env.sh)
            [ "$LOCK_HELD" = 1 ] || return 95
            shift
            TINYASSETS_ENV_FILE="$TEST_ENV" command bash "$HELPER" "$@" ;;
        *) command bash "$@" ;;
    esac
}
# env must keep the bash function so the real helper is mapped to the test env.
env() { shift; "$@"; }
export -f sudo flock timeout docker bash env
'''
    proc = subprocess.run(
        [BASH, "-c", prelude + step["run"]], capture_output=True, text=True,
        input="", env={**helper_env(tmp_path), "TEST_ENV": (tmp_path / "env").as_posix(),
                       "REMOTE": REMOTE.as_posix(), "HELPER": HELPER.as_posix(),
                       "SSH_ARGS": (tmp_path / "ssh-args").as_posix(),
                       "PROBE_DIR": tmp_path.as_posix(), "OAUTH_ACTION": action,
                       "PREV_IMAGE": "ghcr.io/example/daemon@sha256:" + "a" * 64,
                       "DO_SSH_USER": "deploy", "DO_DROPLET_HOST": "host.invalid",
                       ID: "client-id", SECRET: "GOCSPX-secret"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    content = (tmp_path / "env").read_text()
    assert (ID in content) == (SECRET in content) == installed
    daemon_content = (tmp_path / "daemon.env").read_text()
    assert (ID in daemon_content) == (SECRET in daemon_content) == installed
    if action == "install" and protected:
        assert f"{ID}=client-id\n{SECRET}=GOCSPX-secret\n" in content
    if not protected and action != "remove":
        assert "::warning::rollback image lacks" in proc.stdout
    for value in ("client-id", "GOCSPX-secret"):
        assert value not in proc.stdout + proc.stderr + (tmp_path / "ssh-args").read_text()
