"""scripts/platform_setup.py runs once on the founder's own (Windows) computer with their
own `gh`: every value is set only where it is absent, goes to gh on stdin alone (never an
argv, the disk or the output), and the review App's code is turned in only after a
main-only `review` environment exists. Held against a fake of GitHub's API reached through
a fake `gh`, and once through real subprocess with a stand-in `gh` on PATH."""

from __future__ import annotations

import base64
import importlib.util
import io
import json
import os
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest

from scripts import platform_setup as setup

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "platform/deploy/review-app.html"
REPO = setup.REPO
ISSUER = setup.OWNER_ISSUER
CODE = "a180b1a3d263c81bc6441d7b990bae27d4c10679"
PEM = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA\n-----END RSA PRIVATE KEY-----\n"

# Each test names the requirement it holds, as a literal mark (no file-wide one). The review
# App's tests name its own requirement (#5438): review.yml mints its token from the names
# this script sets, in the main-only `review` environment.


def _production_guard():
    spec = importlib.util.spec_from_file_location(
        "production_environment_guard", ROOT / "tests/test_workflow_production_environment.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- a fake of GitHub's API, and of the founder's `gh` in front of it --------------------


class Fake:
    """The repo's secrets, variables and environments, as GitHub holds them; every REST
    call recorded as `METHOD path`, every value stored by name."""

    def __init__(self, secrets: dict[str | None, set[str]] | None = None,
                 variables: dict[str, str] | None = None,
                 environments: dict[str, list | None] | None = None) -> None:
        self.secrets = {None: set(), "production": set(), "review": set(), **(secrets or {})}
        self.stored: dict[tuple[str | None, str], str] = {}
        self.updated: dict[tuple[str | None, str], str] = {}
        self.variables = dict(variables or {})
        self.environments = dict(environments or {})
        self.calls: list[str] = []

    def rest(self, method: str, path: str, body: object) -> tuple[int, object]:
        self.calls.append(f"{method} {path}")
        if m := re.fullmatch(r"(?:/environments/(\w+))?(?:/actions)?/secrets/(\w+)", path):
            env, name = m.group(1), m.group(2)
            held = self.secrets.setdefault(env, set())
            if method == "GET":
                if name not in held:
                    return 404, {}
                return 200, {"name": name,
                             "updated_at": self.updated.get((env, name), "2026-11-01T00:00:00Z")}
            if method == "DELETE":
                if name not in held:
                    return 404, {}
                held.discard(name)
                return 204, None
        if m := re.fullmatch(r"/actions/variables/(\w+)", path):
            name = m.group(1)
            return (200, {"value": self.variables[name]}) if name in self.variables else (404, {})
        if m := re.fullmatch(r"/environments/(\w+)", path):
            env = m.group(1)
            if method == "GET":
                if env not in self.environments:
                    return 404, {}
                policy = setup.MAIN_ONLY if self.environments[env] is not None else None
                return 200, {"name": env, "deployment_branch_policy": policy}
            if method == "PUT":
                assert body == {"deployment_branch_policy": setup.MAIN_ONLY}
                self.environments[env] = []
                return 200, {}
        if m := re.fullmatch(r"/environments/(\w+)/deployment-branch-policies", path):
            env = m.group(1)
            if method == "POST":
                self.environments[env].append((body["name"], body["type"]))
                return 200, {}
            return 200, {"branch_policies": [{"name": n, "type": t}
                                             for n, t in self.environments[env] or []]}
        raise AssertionError(f"unexpected {method} {path}")


class _Done:
    def __init__(self, returncode: int, stdout: bytes = b"") -> None:
        self.returncode, self.stdout = returncode, stdout


def _gh_cli(fake: Fake, said: list[str]):
    """The founder's `gh` over the fake. Every argv is kept in `said`, so a test can see no
    value was ever in one; a value arrives only as stdin bytes."""

    def run(argv, input=None, **kwargs):
        assert "text" not in kwargs and "shell" not in kwargs
        assert input is None or isinstance(input, bytes)
        value = None if input is None else input.decode("utf-8")
        said.append(" ".join(argv))
        assert argv[0] == "gh"
        if argv[1] == "api":
            assert argv[2:4] == ["-i", "--method"]
            status, body = fake.rest(argv[4], argv[5].removeprefix(f"repos/{REPO}"),
                                     json.loads(value) if value else None)
            answer = f"HTTP/2.0 {status} X\r\nContent-Type: json\r\n\r\n{json.dumps(body)}"
            return _Done(0 if status < 400 else 1, answer.encode())
        assert argv[argv.index("--repo") + 1] == REPO
        if argv[1:3] == ["secret", "set"]:
            env = argv[argv.index("--env") + 1] if "--env" in argv else None
            fake.secrets.setdefault(env, set()).add(argv[3])
            fake.stored[(env, argv[3])] = value
            fake.updated[(env, argv[3])] = "2026-11-05T10:00:01Z"
            return _Done(0)
        if argv[1:3] == ["variable", "set"]:
            fake.variables[argv[3]] = value
            return _Done(0)
        raise AssertionError(argv)

    return run


def _gh(fake: Fake, said: list[str] | None = None) -> setup.Gh:
    return setup.Gh(run=_gh_cli(fake, [] if said is None else said))


def _no_value_shown(said: list[str], out: str, values) -> None:
    for value in values:
        assert value and not [line for line in said if value in line], value
        assert value not in out, value


# --- the production secrets ----------------------------------------------------------------


@pytest.mark.verifies(
    "clean-sheet-platform:One keyring, held only by the gateway")
def test_the_made_secrets_are_set_once_each_on_stdin_alone(capsys) -> None:
    fake, said = Fake(secrets={"production": {"GATEWAY_KEYRING"}}), []
    setup.made_secrets(_gh(fake, said))
    assert fake.secrets["production"] == {"ROUTING_MAC_KEY", "E2B_WEBHOOK_SECRET",
                                          "GATEWAY_KEYRING", "FILES_KEYRING"}
    assert ("production", "GATEWAY_KEYRING") not in fake.stored
    out = capsys.readouterr().out
    assert out.splitlines() == ["ROUTING_MAC_KEY: set", "E2B_WEBHOOK_SECRET: set",
                                "GATEWAY_KEYRING: already set", "FILES_KEYRING: set"]
    _no_value_shown(said, out, fake.stored.values())
    # Run again: nothing written.
    calls = len(said)
    setup.made_secrets(_gh(fake, said))
    assert all(line.startswith("gh api -i --method GET") for line in said[calls:])


@pytest.mark.verifies(
    "clean-sheet-platform:One keyring, held only by the gateway")
def test_the_made_values_are_the_shapes_the_platform_reads() -> None:
    mac = setup.make_mac()
    assert len(base64.urlsafe_b64decode(mac + "=")) == 32 and "=" not in mac
    assert setup.make_mac() != mac and len(setup.make_token()) >= 43
    ring = json.loads(setup.make_keyring())
    assert ring["active"] == "k1" and list(ring["keys"]) == ["k1"]
    assert len(base64.b64decode(ring["keys"]["k1"])) == 32
    assert setup.make_keyring() != setup.make_keyring()


# --- WorkOS -----------------------------------------------------------------------------


@pytest.mark.verifies(
    "clean-sheet-platform:Sign-up is the platform's explicit admit, "
    "and erasure its outside record")
def test_workos_stores_a_pasted_key_and_leaves_the_repository_copies(capsys) -> None:
    fake = Fake(secrets={None: {"WORKOS_API_KEY", "WORKOS_AUTHKIT_DOMAIN"}})
    said: list[str] = []
    asked: list[str] = []

    def ask(prompt: str) -> str:
        asked.append(prompt)
        return '  "sk_pasted_0123456789abcdef"\r\n'

    setup.workos(_gh(fake, said), ask)
    assert fake.stored[("production", "WORKOS_API_KEY")] == "sk_pasted_0123456789abcdef"
    assert len(asked) == 1
    # The first step never deletes: the founder checks production's key first.
    assert fake.secrets[None] == {"WORKOS_API_KEY", "WORKOS_AUTHKIT_DOMAIN"}
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "WORKOS_API_KEY: set (updated 2026-11-05T10:00:01Z)"
    assert "--delete-repo-copies" in out.splitlines()[1]
    _no_value_shown(said, out, ["sk_pasted_0123456789abcdef"])
    # Held already: nothing asked, nothing stored again, and its time shown.
    setup.workos(_gh(fake, said), lambda _: pytest.fail("asked again"))
    assert capsys.readouterr().out.splitlines()[0] == (
        "WORKOS_API_KEY: already set (updated 2026-11-05T10:00:01Z)")


@pytest.mark.verifies(
    "clean-sheet-platform:Sign-up is the platform's explicit admit, "
    "and erasure its outside record")
def test_workos_deletes_the_repository_copies_only_when_asked_and_production_holds_it(
        capsys) -> None:
    fake = Fake(secrets={None: {"WORKOS_API_KEY", "WORKOS_AUTHKIT_DOMAIN"}})
    with pytest.raises(setup.SetupError, match="production holds no WORKOS_API_KEY"):
        setup.workos(_gh(fake), lambda _: pytest.fail("asked"), delete_repo_copies=True)
    assert fake.secrets[None] == {"WORKOS_API_KEY", "WORKOS_AUTHKIT_DOMAIN"}
    fake.secrets["production"].add("WORKOS_API_KEY")
    fake.updated[("production", "WORKOS_API_KEY")] = "2026-11-05T09:00:00Z"
    capsys.readouterr()
    setup.workos(_gh(fake), lambda _: pytest.fail("asked"), delete_repo_copies=True)
    assert fake.secrets[None] == set()
    assert capsys.readouterr().out.splitlines() == [
        "WORKOS_API_KEY: already set (updated 2026-11-05T09:00:00Z)",
        "WORKOS_API_KEY (repository): deleted", "WORKOS_AUTHKIT_DOMAIN (repository): deleted"]

    # The command line's flag, end to end: a fresh repository whose production holds the key.
    fresh = Fake(secrets={None: {"WORKOS_API_KEY", "WORKOS_AUTHKIT_DOMAIN"},
                          "production": {"WORKOS_API_KEY"}})
    assert setup.main(["workos", "--delete-repo-copies"], gh=_gh(fresh)) == 0
    assert fresh.secrets[None] == set() and fresh.stored == {}
    # Without the flag the same command keeps them.
    kept = Fake(secrets={None: {"WORKOS_API_KEY"}, "production": {"WORKOS_API_KEY"}})
    assert setup.main(["workos"], gh=_gh(kept)) == 0
    assert kept.secrets[None] == {"WORKOS_API_KEY"}


@pytest.mark.verifies(
    "clean-sheet-platform:Sign-up is the platform's explicit admit, "
    "and erasure its outside record")
def test_workos_reads_one_line_of_stdin_when_it_is_not_a_terminal(monkeypatch) -> None:
    """An agent's shell, or `Get-Clipboard | python ...`: Windows' getpass reads only the
    console, so a pipe is read as one line, its CR/LF stripped."""
    monkeypatch.setattr(sys, "stdin", io.StringIO("sk_piped_0123456789abcdef\r\nignored\n"))
    monkeypatch.setattr(setup.getpass, "getpass", lambda _: pytest.fail("prompted"))
    assert setup.read_key("p") == "sk_piped_0123456789abcdef"
    fake = Fake()
    monkeypatch.setattr(sys, "stdin", io.StringIO("sk_piped_0123456789abcdef\r\n"))
    assert setup.main(["workos"], gh=_gh(fake)) == 0
    assert fake.stored[("production", "WORKOS_API_KEY")] == "sk_piped_0123456789abcdef"


@pytest.mark.verifies(
    "clean-sheet-platform:Sign-up is the platform's explicit admit, "
    "and erasure its outside record")
def test_workos_fails_loudly_when_the_stored_key_does_not_read_back(capsys) -> None:
    fake = Fake(secrets={None: {"WORKOS_API_KEY"}})
    inner = _gh_cli(fake, [])

    def run(argv, input=None, **kwargs):
        done = inner(argv, input=input, **kwargs)
        if argv[1:3] == ["secret", "set"]:
            # GitHub took the write, but the key is not there when read back.
            fake.secrets["production"].discard(argv[3])
        return done

    with pytest.raises(setup.SetupError, match="does not read back from production"):
        setup.workos(setup.Gh(run=run), lambda _: "sk_pasted_0123456789abcdef")
    assert "WORKOS_API_KEY: set" not in capsys.readouterr().out
    assert fake.secrets[None] == {"WORKOS_API_KEY"}


@pytest.mark.verifies(
    "clean-sheet-platform:Sign-up is the platform's explicit admit, "
    "and erasure its outside record")
@pytest.mark.parametrize("key", ["sk_" + "a1B2" * 18, "sk_live_" + "a1B2" * 18])
def test_workos_takes_a_secret_key_with_or_without_a_label(key) -> None:
    """WorkOS's current keys are `sk_` with no environment label."""
    assert setup.live_workos_key(key)


@pytest.mark.verifies(
    "clean-sheet-platform:Sign-up is the platform's explicit admit, "
    "and erasure its outside record")
@pytest.mark.parametrize("pasted", ["sk_test_staging", "sk_test_" + "a1B2" * 18,
                                    "sk_Test_" + "a1B2" * 18, "sk_", "sk_short", "",
                                    "sk_live_a b", "client_01"])
def test_workos_refuses_anything_but_one_production_key(pasted) -> None:
    fake = Fake(secrets={None: {"WORKOS_API_KEY"}})
    with pytest.raises(setup.SetupError) as refused:
        setup.workos(_gh(fake), lambda _: pasted)
    assert fake.stored == {} and fake.secrets[None] == {"WORKOS_API_KEY"}
    assert not pasted or pasted not in str(refused.value)


@pytest.mark.verifies(
    "clean-sheet-platform:Sign-up is the platform's explicit admit, "
    "and erasure its outside record")
def test_only_production_jobs_read_the_retired_repository_copies() -> None:
    """The second step deletes the repository copies: every job that reads one must be one
    the `production` copy reaches."""
    guard = _production_guard()
    readers = []
    for path in guard._workflow_files():
        for name, job in (guard._load(path).get("jobs") or {}).items():
            if set(setup.WORKOS_REPO_COPIES) & guard._secret_names(json.dumps(job)):
                readers.append((path.name, name, job.get("environment")))
    assert all(env == "production" for _, _, env in readers), readers


# --- the repository variables ----------------------------------------------------------


def _discovery(url: str):
    assert url == f"{ISSUER}/.well-known/openid-configuration"
    return 200, {"issuer": ISSUER}


@pytest.mark.verifies(
    "clean-sheet-platform:The owner's sign-in is the owner issuer's "
    "User Management code flow")
def test_the_variables_are_set_once(capsys) -> None:
    fake, said = Fake(), []
    assert setup.variables(_gh(fake, said), _discovery)
    assert fake.variables == {
        "OWNER_ISSUER": ISSUER, "WORKOS_CLIENT_ID": setup.WORKOS_CLIENT_ID}
    _no_value_shown(said, capsys.readouterr().out, fake.variables.values())
    # Run again: every one already set, nothing written.
    calls = len(said)
    assert setup.variables(_gh(fake, said), _discovery)
    assert not [line for line in said[calls:] if line.startswith("gh variable")]
    # One set to something else is left, and the run says so.
    fake.variables["WORKOS_CLIENT_ID"] = "client_other"
    assert not setup.variables(_gh(fake, said), _discovery)
    assert fake.variables["WORKOS_CLIENT_ID"] == "client_other"


@pytest.mark.verifies(
    "clean-sheet-platform:The owner's sign-in is the owner issuer's "
    "User Management code flow")
def test_an_issuer_whose_discovery_names_another_sets_nothing() -> None:
    fake = Fake()
    with pytest.raises(setup.SetupError, match="does not name"):
        setup.variables(_gh(fake), lambda _: (200, {"issuer": "https://other"}))
    assert fake.variables == {}


# --- the review App ---------------------------------------------------------------------


def _turn_in(fake: Fake, app: dict | None = None, status: int = 201):
    def turn_in(code: str):
        fake.calls.append(f"POST conversions {code}")
        return status, app or {"id": 77, "slug": "tinyassets-review",
                               "owner": {"login": "TinyAssets"}, "pem": PEM,
                               "client_secret": "c", "webhook_secret": None}

    return turn_in


@pytest.mark.verifies(
    "development-coordination-runtime:A review verdict is a check run only the "
    "review App posts")
def test_the_review_app_key_goes_to_a_main_only_review_environment(capsys) -> None:
    fake, said = Fake(), []
    setup.review_app(CODE, _gh(fake, said), _turn_in(fake))
    assert fake.environments["review"] == [("main", "branch")]
    assert fake.secrets["review"] == {"REVIEW_APP_PRIVATE_KEY"}
    assert fake.stored[("review", "REVIEW_APP_PRIVATE_KEY")] == PEM
    assert fake.variables == {"REVIEW_APP_ID": "77"}
    # The environment is main-only before the code is spent; the App is named, and its id
    # set, before its key is stored.
    assert fake.calls.index("PUT /environments/review") < fake.calls.index(
        f"POST conversions {CODE}")
    assert [line for line in said if line.startswith(("gh variable", "gh secret set"))] == [
        f"gh variable set REVIEW_APP_ID --repo {REPO}",
        f"gh secret set REVIEW_APP_PRIVATE_KEY --env review --repo {REPO}"]
    out = capsys.readouterr().out
    _no_value_shown(said, out, [PEM, "BEGIN RSA"])
    assert out.splitlines()[1] == "App tinyassets-review: made, id 77"
    assert out.splitlines()[-1].endswith(
        "https://github.com/apps/tinyassets-review/installations/new")
    # Run again with the key held: reported as set, the code not spent, no App to delete
    # but one made just now.
    setup.review_app("b" * 40, _gh(fake, said), _turn_in(fake))
    assert "POST conversions " + "b" * 40 not in fake.calls
    again = capsys.readouterr().out.splitlines()[-1]
    assert again.startswith("REVIEW_APP_PRIVATE_KEY: already set (App 77)")
    assert "made just now" in again and "not App 77" in again


@pytest.mark.verifies(
    "development-coordination-runtime:A review verdict is a check run only the "
    "review App posts")
def test_a_key_that_failed_to_store_names_its_app() -> None:
    fake = Fake()

    def run(argv, input=None, **kwargs):
        if argv[1:3] == ["secret", "set"]:
            return _Done(1)
        return _gh_cli(fake, [])(argv, input=input, **kwargs)

    with pytest.raises(setup.SetupError, match=r"App 77 \(tinyassets-review\) exists"):
        setup.review_app(CODE, setup.Gh(run=run), _turn_in(fake))
    assert fake.variables == {"REVIEW_APP_ID": "77"}
    # A rerun with a spent code points at that App's key, not at making another App.
    with pytest.raises(setup.SetupError, match="App 77 is REVIEW_APP_ID without its key"):
        setup.review_app(CODE, _gh(fake), _turn_in(fake, status=404))


@pytest.mark.verifies(
    "development-coordination-runtime:A review verdict is a check run only the "
    "review App posts")
def test_a_half_made_review_environment_is_finished_not_refused(capsys) -> None:
    fake = Fake(environments={"review": []})
    setup.review_app(CODE, _gh(fake), _turn_in(fake))
    assert fake.environments["review"] == [("main", "branch")]
    assert capsys.readouterr().out.splitlines()[0] == (
        "review environment: already there; limited to main now")


@pytest.mark.verifies(
    "development-coordination-runtime:A review verdict is a check run only the "
    "review App posts")
@pytest.mark.parametrize("policy", [None, [("main", "branch"), ("release/*", "branch")],
                                    [("v*", "tag")]])
def test_a_review_environment_open_to_other_refs_is_refused(policy) -> None:
    fake = Fake(environments={"review": policy})
    with pytest.raises(setup.SetupError, match="other branches than main"):
        setup.review_app(CODE, _gh(fake), _turn_in(fake))
    assert not [c for c in fake.calls if "conversions" in c]
    assert fake.environments["review"] == policy


@pytest.mark.verifies(
    "development-coordination-runtime:A review verdict is a check run only the "
    "review App posts")
def test_an_app_another_account_owns_is_not_stored() -> None:
    fake = Fake()
    other = {"id": 1, "slug": "x", "owner": {"login": "someone"}, "pem": PEM}
    with pytest.raises(setup.SetupError, match="belongs to someone"):
        setup.review_app(CODE, _gh(fake), _turn_in(fake, other))
    assert fake.secrets["review"] == set() and fake.variables == {}


@pytest.mark.verifies(
    "development-coordination-runtime:A review verdict is a check run only the "
    "review App posts")
@pytest.mark.parametrize(("given", "code"), [
    (CODE, CODE),
    (f"https://github.com/TinyAssets/TinyAssets?code={CODE}", CODE),
    (f" https://github.com/TinyAssets/TinyAssets?code={CODE}&state=s ", CODE),
    (f"'https://github.com/TinyAssets/TinyAssets?code={CODE}'", CODE),
    (f'"{CODE}"', CODE),
    ("../../x", None),
    ("https://github.com/TinyAssets/TinyAssets", None),
])
def test_the_code_is_taken_bare_or_from_the_redirect(given, code) -> None:
    if code is None:
        with pytest.raises(setup.SetupError):
            setup.code_of(given)
    else:
        assert setup.code_of(given) == code


class _Form(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.forms: list[dict] = []
        self.inputs: list[dict] = []

    def handle_starttag(self, tag, attrs):
        if tag == "form":
            self.forms.append(dict(attrs))
        elif tag == "input":
            self.inputs.append(dict(attrs))


@pytest.mark.verifies(
    "development-coordination-runtime:A review verdict is a check run only the "
    "review App posts")
def test_the_manifest_page_asks_for_the_review_app_and_nothing_more() -> None:
    page = _Form()
    page.feed(PAGE.read_text(encoding="utf-8"))
    [form] = page.forms
    assert form["action"] == "https://github.com/organizations/TinyAssets/settings/apps/new"
    assert form["method"] == "post"
    [field] = page.inputs
    assert field["name"] == "manifest"
    assert json.loads(field["value"]) == {
        "name": "TinyAssets Review", "url": "https://tinyassets.io",
        "hook_attributes": {"url": "https://tinyassets.io/", "active": False},
        "redirect_url": "https://github.com/TinyAssets/TinyAssets",
        "public": False,
        "default_permissions": {"checks": "write", "metadata": "read"},
        "default_events": []}


# --- the command line, and the real subprocess path the founder's Windows machine takes -


@pytest.mark.verifies(
    "development-coordination-runtime:A review verdict is a check run only the "
    "review App posts")
def test_a_refusal_exits_nonzero_and_names_no_value(capsys) -> None:
    fake = Fake(environments={"review": None})
    assert setup.main(["review-app", "--code", CODE], gh=_gh(fake)) == 1
    err = capsys.readouterr().err
    assert "other branches than main" in err and CODE not in err
    assert setup.main(["secrets"], gh=_gh(Fake())) == 0


@pytest.mark.verifies(
    "development-coordination-runtime:A review verdict is a check run only the "
    "review App posts")
def test_the_real_gh_call_sends_bytes_and_no_shell(tmp_path, monkeypatch) -> None:
    """Through subprocess itself, as on the founder's machine: an argv list (no shell, so no
    quoting by hand), the value as bytes with its LF newlines kept, gh's answer read as
    UTF-8."""
    log = tmp_path / "log.json"
    stand_in = tmp_path / "gh"
    stand_in.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        f"open({str(log)!r}, 'w').write(json.dumps({{'argv': sys.argv[1:], "
        "'stdin': sys.stdin.buffer.read().decode('latin-1')}))\n"
        "sys.stdout.buffer.write('HTTP/2.0 200 OK\\r\\n\\r\\n{\"n\": \"\\u00e9\"}'.encode())\n",
        encoding="utf-8")
    stand_in.chmod(0o755)
    # The stand-in alone: where it cannot run (Windows ignores the shebang), the call fails
    # rather than reaching a signed-in real gh and replacing a live secret.
    monkeypatch.setenv("PATH", str(tmp_path))
    gh = setup.Gh()
    gh.set_secret("REVIEW_APP_PRIVATE_KEY", PEM, "review")
    seen = json.loads(log.read_text())
    assert seen["argv"] == ["secret", "set", "REVIEW_APP_PRIVATE_KEY", "--env", "review",
                            "--repo", REPO]
    assert seen["stdin"] == PEM
    assert gh.call("GET", "/x") == (200, {"n": "\u00e9"})


@pytest.mark.verifies(
    "clean-sheet-platform:The owner's sign-in is the owner issuer's "
    "User Management code flow")
def test_the_docs_give_the_windows_command() -> None:
    checklist = (ROOT / "platform/deploy/checklist.md").read_text(encoding="utf-8")
    for doc in (ROOT / "platform/deploy/checklist.md", ROOT / "docs/host-actions.md", PAGE,
                ROOT / "scripts/platform_setup.py"):
        text = doc.read_text(encoding="utf-8")
        assert "python3 scripts/platform_setup.py" not in text, doc
        assert "SETUP_PAT" not in text and "platform-setup.yml" not in text, doc
    for command in ("secrets", "variables", "workos", 'review-app --code "'):
        assert f"python scripts/platform_setup.py {command}" in checklist, command
    # The key comes from the clipboard, in PowerShell or from Git Bash, never the chat.
    assert "`Get-Clipboard | python scripts/platform_setup.py workos`" in checklist
    assert ("`powershell.exe -NoProfile -Command Get-Clipboard | python "
            "scripts/platform_setup.py workos`") in checklist
