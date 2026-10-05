"""Developer capability probes against the shipping jail, not a host shell."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

from tinyassets import universe_egress as egress
from tinyassets import universe_tools as tools

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux jail oracle")


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "cc"
    root.mkdir()
    return root


def shell(workspace, command):
    result = tools.bash(workspace, command, agent_id="developer")
    print(result)
    return result


def test_git_and_pytest(workspace):
    assert "git version" in shell(workspace, "git --version")
    assert "[exit code 0]" in shell(
        workspace,
        "printf 'def test_sum(): assert 1 + 1 == 2\\n' > test_small.py && "
        "python -m pytest -q test_small.py",
    )


def test_large_edit_and_next_call_persistence(workspace):
    assert "[exit code 0]" in shell(
        workspace,
        "python -c \"from pathlib import Path; "
        "Path('app.html').write_text('line\\n' * 120000 + 'unique-before\\n')\"",
    )
    result = tools.read_file(workspace, "app.html", offset=120001, limit=1,
                             agent_id="developer")
    assert "unique-before" in result and len(result) < 1000
    assert "edited" in tools.edit_file(
        workspace, "app.html", "unique-before", "unique-after", agent_id="developer",
    )
    assert "unique-after" in shell(workspace, "tail -n 1 app.html")
    assert "600013" in shell(workspace, "wc -c < app.html")


def test_branch_merge_conflict_and_rebase(workspace):
    assert "[exit code 0]" in shell(workspace, """
set -e
git init -b main repo
cd repo
git config user.name Developer
git config user.email developer@example.test
printf 'original\n' > file
git add file
git commit -qm initial
git switch -c feature
printf 'feature\n' > file
git commit -qam feature
git switch main
printf 'main\n' > file
git commit -qam main
git switch feature
if git merge main; then exit 1; fi
test -n "$(git ls-files -u)"
printf 'resolved\n' > file
git add file
git commit -qm resolution
git merge-base --is-ancestor main HEAD
git switch main
git merge --ff-only feature
git switch -c rebase-work
printf 'work\n' > work
git add work
git commit -qm work
git switch main
printf 'next\n' > next
git add next
git commit -qm next
git switch rebase-work
git rebase main
test -z "$(git status --porcelain)"
test "$(cat file)" = resolved
""")


def test_registered_connection_does_not_authenticate_git(workspace, tmp_path, monkeypatch):
    """Diagnosis: real smart HTTP works, but the jail proxy supplies no credential."""
    from tinyassets.credential_vault import write_credential_vault
    from tinyassets.storage.outbound_connections import ConnectionLedger

    secret = "synthetic-git-credential-not-in-box"
    ledger = ConnectionLedger(tmp_path / "connections.db")
    ledger.create_connection(
        connection_id="git", owner_user_id="owner", connection_class="outbound-http",
        scopes=("GET", "POST", "git_read:owner/repo", "git_write:owner/repo"),
        provider="http", destination="git.test", credential_ref="vault://http/git.test",
        connection_type="http", auth_scheme="bearer", git_host="git.test",
        allowed_endpoints=[{"host": "git.test", "path_template": "/owner/repo.git/{part}",
                            "methods": ["GET", "POST"],
                            "param_patterns": {"part": "[A-Za-z0-9/-]+"}}],
    )
    ledger.grant_connection(grant_id="grant", connection_id="git", owner_user_id="owner",
                            universe_id=workspace.name)
    write_credential_vault(workspace, [{"credential_type": "http", "destination": "git.test",
                                      "token": secret}])
    remote = tmp_path / "remotes" / "owner" / "repo.git"
    remote.parent.mkdir(parents=True)

    def git(*args, env=None):
        result = subprocess.run(["git", *args], env=env, capture_output=True, timeout=30)
        assert result.returncode == 0, result.stderr.decode().replace(secret, "[redacted]")
        return result

    git("init", "--bare", "-b", "main", str(remote))
    git("-C", str(remote), "config", "http.receivepack", "true")
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            authorized = self.headers.get("Authorization") == "Bearer " + secret
            seen.append(authorized)
            if not authorized:
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="synthetic"')
                self.end_headers()
                return
            target = urlsplit(self.path)
            result = subprocess.run(
                ["git", "http-backend"],
                input=self.rfile.read(int(self.headers.get("Content-Length", 0))),
                capture_output=True, check=True,
                env={**os.environ, "GIT_PROJECT_ROOT": str(remote.parent.parent),
                     "GIT_HTTP_EXPORT_ALL": "1", "PATH_INFO": target.path,
                     "QUERY_STRING": target.query, "REQUEST_METHOD": self.command,
                     "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                     "REMOTE_USER": "synthetic"},
            )
            headers, body = result.stdout.split(b"\r\n\r\n", 1)
            self.send_response(200)
            for line in headers.decode().split("\r\n"):
                name, value = line.split(":", 1)
                self.send_header(name, value.strip())
            self.end_headers()
            self.wfile.write(body)

        do_POST = do_GET

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_port
    # Test-only DNS pin for one synthetic host. No production allow rule changes.
    checked = egress._checked_addresses
    monkeypatch.setattr(egress, "_checked_addresses", lambda host, p:
                        ["127.0.0.1"] if (host, p) == ("git.test", port) else checked(host, p))
    try:
        # Positive control: backend accepts real clone/fetch/push with host-only auth.
        control = tmp_path / "control"
        env = {**os.environ, "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.extraHeader",
               "GIT_CONFIG_VALUE_0": "Authorization: Bearer " + secret, "NO_PROXY": "127.0.0.1"}
        git("clone", f"http://127.0.0.1:{port}/owner/repo.git", str(control), env=env)
        git("-C", str(control), "symbolic-ref", "HEAD", "refs/heads/main")
        git("-C", str(control), "-c", "user.name=Test", "-c", "user.email=test@example.test",
            "commit", "--allow-empty", "-m", "initial", env=env)
        git("-C", str(control), "push", "origin", "main", env=env)
        git("-C", str(control), "fetch", "origin", env=env)
        assert seen and all(seen)
        seen.clear()
        url = f"http://git.test:{port}/owner/repo.git"
        commands = [f"git clone {url} private", f"git -C local fetch {url}",
                    f"git -C local push {url} HEAD:main"]
        assert "[exit code 0]" in shell(workspace,
            "git init local && git -C local -c user.name=Test -c user.email=test@example.test "
            "commit --allow-empty -m initial")
        for command in commands:
            result = shell(workspace, "GIT_TERMINAL_PROMPT=0 " + command)
            assert "[exit code 128]" in result and "could not read Username" in result
            assert secret not in result
        assert seen and not any(seen)
        visibility = shell(workspace,
            "test ! -e .runtime/credential-vault.json && test ! -e /cc && "
            "env && find . -maxdepth 3 -type f")
        assert "[exit code 0]" in visibility and secret not in visibility
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_public_clone(workspace):
    if os.environ.get("TA_DEV_PUBLIC_PROBE") != "1":
        pytest.skip("owner=agent-box-dev-workflow runs-in=manual Linux oracle "
                    "with TA_DEV_PUBLIC_PROBE=1")
    assert "[exit code 0]" in shell(
        workspace,
        "GIT_TERMINAL_PROMPT=0 git clone --depth 1 https://github.com/octocat/Hello-World repo "
        "&& git -C repo status --porcelain",
    )
