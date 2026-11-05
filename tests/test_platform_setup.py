"""scripts/platform_setup.py runs once on the founder's own (Windows) computer with their
own `gh`: every value is set only where it is absent, goes to gh on stdin alone (never an
argv, the disk or the output), and the review App's code is turned in only after a
main-only `review` environment exists. Held against a fake of GitHub's API reached through
a fake `gh`, and once through real subprocess with a stand-in `gh` on PATH."""

from __future__ import annotations

import base64
import importlib.util
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

pytestmark = pytest.mark.verifies(
    "clean-sheet-platform:The owner's sign-in is the configured issuer's OpenID Connect flow"
)


def _production_guard():
    spec = importlib.util.spec_from_file_location(
        "production_environment_guard", ROOT / "tests/test_workflow_production_environment.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- a fake of GitHub's API, and of the founder's `gh` in front of it --------------------


class Fake:
    """The repo's secrets, variables, environments and the canary's runs, as GitHub holds
    them; every REST call recorded as `METHOD path`, every value stored by name."""

    def __init__(self, secrets: dict[str | None, set[str]] | None = None,
                 variables: dict[str, str] | None = None,
                 environments: dict[str, list | None] | None = None) -> None:
        self.secrets = {None: set(), "production": set(), "review": set(), **(secrets or {})}
        self.stored: dict[tuple[str | None, str], str] = {}
        self.variables = dict(variables or {})
        self.environments = dict(environments or {})
        self.calls: list[str] = []
        self.runs = [{"databaseId": 1, "headBranch": "feature"},
                     {"databaseId": 2, "headBranch": "main"},
                     {"databaseId": 3, "headBranch": "main"}]
        self.receipts: dict[int, object] = {}

    def rest(self, method: str, path: str, body: object) -> tuple[int, object]:
        self.calls.append(f"{method} {path}")
        if m := re.fullmatch(r"(?:/environments/(\w+))?(?:/actions)?/secrets/(\w+)", path):
            env, name = m.group(1), m.group(2)
            held = self.secrets.setdefault(env, set())
            if method == "GET":
                return (200 if name in held else 404), {}
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
            return _Done(0)
        if argv[1:3] == ["variable", "set"]:
            fake.variables[argv[3]] = value
            return _Done(0)
        if argv[1:3] == ["run", "list"]:
            assert argv[argv.index("--workflow") + 1] == "uptime-canary.yml"
            return _Done(0, json.dumps(fake.runs).encode())
        if argv[1:3] == ["run", "download"]:
            run_id = int(argv[3])
            assert argv[argv.index("--name") + 1] == "owner-token-claims"
            if run_id not in fake.receipts:
                return _Done(1)
            into = Path(argv[argv.index("--dir") + 1])
            (into / "owner-token-claims.json").write_text(json.dumps(fake.receipts[run_id]))
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


def test_the_made_values_are_the_shapes_the_platform_reads() -> None:
    mac = setup.make_mac()
    assert len(base64.urlsafe_b64decode(mac + "=")) == 32 and "=" not in mac
    assert setup.make_mac() != mac and len(setup.make_token()) >= 43
    ring = json.loads(setup.make_keyring())
    assert ring["active"] == "k1" and list(ring["keys"]) == ["k1"]
    assert len(base64.b64decode(ring["keys"]["k1"])) == 32
    assert setup.make_keyring() != setup.make_keyring()


# --- WorkOS -----------------------------------------------------------------------------


def test_workos_stores_a_pasted_production_key_then_retires_the_repository_copies(capsys):
    fake = Fake(secrets={None: {"WORKOS_API_KEY", "WORKOS_AUTHKIT_DOMAIN"}})
    said: list[str] = []
    asked: list[str] = []

    def ask(prompt: str) -> str:
        asked.append(prompt)
        return '  "sk_live_pasted"\r\n'

    setup.workos(_gh(fake, said), ask)
    assert fake.stored[("production", "WORKOS_API_KEY")] == "sk_live_pasted"
    assert fake.secrets[None] == set() and len(asked) == 1
    out = capsys.readouterr().out
    assert out.splitlines() == ["WORKOS_API_KEY: set", "WORKOS_API_KEY (repository): deleted",
                                "WORKOS_AUTHKIT_DOMAIN (repository): deleted"]
    _no_value_shown(said, out, ["sk_live_pasted"])
    # Held already: nothing asked, nothing stored again.
    setup.workos(_gh(fake, said), lambda _: pytest.fail("asked again"))
    assert capsys.readouterr().out.splitlines()[0] == "WORKOS_API_KEY: already set"


@pytest.mark.parametrize("pasted", ["sk_test_staging", "", "sk_live_a b", "client_01"])
def test_workos_refuses_anything_but_one_production_key(pasted) -> None:
    fake = Fake(secrets={None: {"WORKOS_API_KEY"}})
    with pytest.raises(setup.SetupError) as refused:
        setup.workos(_gh(fake), lambda _: pasted)
    assert fake.stored == {} and fake.secrets[None] == {"WORKOS_API_KEY"}
    assert not pasted or pasted not in str(refused.value)


def test_only_production_jobs_read_the_retired_repository_copies() -> None:
    """The move deletes the repository copies: every job that reads one must be one the
    `production` copy reaches."""
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


def test_the_variables_are_set_once_from_the_newest_main_receipt(capsys) -> None:
    fake, said = Fake(), []
    fake.receipts = {1: {"iss": "https://other", "auth_time": 1},
                     3: {"iss": ISSUER, "auth_time": 1767225600}}
    assert setup.variables(_gh(fake, said), _discovery)
    assert fake.variables == {
        "OWNER_ISSUER": ISSUER, "WORKOS_CLIENT_ID": setup.WORKOS_CLIENT_ID,
        "OWNER_TOKEN_CLAIMS": json.dumps({"iss": ISSUER, "auth_time": 1767225600})}
    # The feature branch's run is never read; main's run 2 kept none, so run 3 is used.
    downloads = [line.split()[3] for line in said if line.startswith("gh run download")]
    assert downloads == ["2", "3"]
    _no_value_shown(said, capsys.readouterr().out, fake.variables.values())
    # Run again: every one already set, nothing written.
    calls = len(said)
    assert setup.variables(_gh(fake, said), _discovery)
    assert not [line for line in said[calls:] if line.startswith("gh variable")]
    # One set to something else is left, and the run says so.
    fake.variables["WORKOS_CLIENT_ID"] = "client_other"
    assert not setup.variables(_gh(fake, said), _discovery)
    assert fake.variables["WORKOS_CLIENT_ID"] == "client_other"


@pytest.mark.parametrize("receipt", [None, {"iss": ISSUER}, {"iss": "https://other",
                                                             "auth_time": 1},
                                     {"iss": ISSUER, "auth_time": "1"},
                                     {"iss": ISSUER, "auth_time": True}])
def test_owner_token_claims_needs_a_receipt_naming_the_issuer_with_auth_time(receipt):
    fake = Fake()
    if receipt is not None:
        fake.receipts = {2: receipt}
    with pytest.raises(setup.SetupError):
        setup.variables(_gh(fake), _discovery)
    assert "OWNER_TOKEN_CLAIMS" not in fake.variables


def test_an_issuer_whose_discovery_names_another_sets_nothing() -> None:
    fake = Fake()
    with pytest.raises(setup.SetupError, match="does not name"):
        setup.variables(_gh(fake), lambda _: (200, {"issuer": "https://other"}))
    assert fake.variables == {}


# --- the review App ---------------------------------------------------------------------


def _turn_in(fake: Fake, app: dict | None = None):
    def turn_in(code: str):
        fake.calls.append(f"POST conversions {code}")
        return 201, app or {"id": 77, "slug": "tinyassets-review",
                            "owner": {"login": "TinyAssets"}, "pem": PEM,
                            "client_secret": "c", "webhook_secret": None}

    return turn_in


def test_the_review_app_key_goes_to_a_main_only_review_environment(capsys) -> None:
    fake, said = Fake(), []
    setup.review_app(CODE, _gh(fake, said), _turn_in(fake))
    assert fake.environments["review"] == [("main", "branch")]
    assert fake.secrets["review"] == {"REVIEW_APP_PRIVATE_KEY"}
    assert fake.stored[("review", "REVIEW_APP_PRIVATE_KEY")] == PEM
    assert fake.variables == {"REVIEW_APP_ID": "77"}
    # The environment is main-only before the code is spent.
    assert fake.calls.index("PUT /environments/review") < fake.calls.index(
        f"POST conversions {CODE}")
    out = capsys.readouterr().out
    _no_value_shown(said, out, [PEM, "BEGIN RSA"])
    assert out.splitlines()[-1].endswith(
        "https://github.com/apps/tinyassets-review/installations/new")
    # A second code is not spent while the key is held.
    with pytest.raises(setup.SetupError, match="already set"):
        setup.review_app("b" * 40, _gh(fake, said), _turn_in(fake))
    assert "POST conversions " + "b" * 40 not in fake.calls


@pytest.mark.parametrize("policy", [None, [("main", "branch"), ("release/*", "branch")],
                                    [("v*", "tag")]])
def test_a_review_environment_open_to_other_refs_is_refused(policy) -> None:
    fake = Fake(environments={"review": policy})
    with pytest.raises(setup.SetupError, match="other branches than main"):
        setup.review_app(CODE, _gh(fake), _turn_in(fake))
    assert not [c for c in fake.calls if "conversions" in c]


def test_an_app_another_account_owns_is_not_stored() -> None:
    fake = Fake()
    other = {"id": 1, "slug": "x", "owner": {"login": "someone"}, "pem": PEM}
    with pytest.raises(setup.SetupError, match="belongs to someone"):
        setup.review_app(CODE, _gh(fake), _turn_in(fake, other))
    assert fake.secrets["review"] == set() and fake.variables == {}


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
        "default_permissions": {"checks": "write", "statuses": "write", "metadata": "read"},
        "default_events": []}


# --- the command line, and the real subprocess path the founder's Windows machine takes -


def test_a_refusal_exits_nonzero_and_names_no_value(capsys) -> None:
    fake = Fake(environments={"review": None})
    assert setup.main(["review-app", "--code", CODE], gh=_gh(fake)) == 1
    err = capsys.readouterr().err
    assert "other branches than main" in err and CODE not in err
    assert setup.main(["secrets"], gh=_gh(Fake())) == 0


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
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    gh = setup.Gh()
    gh.set_secret("REVIEW_APP_PRIVATE_KEY", PEM, "review")
    seen = json.loads(log.read_text())
    assert seen["argv"] == ["secret", "set", "REVIEW_APP_PRIVATE_KEY", "--env", "review",
                            "--repo", REPO]
    assert seen["stdin"] == PEM
    assert gh.call("GET", "/x") == (200, {"n": "\u00e9"})


def test_the_docs_give_the_windows_command() -> None:
    checklist = (ROOT / "platform/deploy/checklist.md").read_text(encoding="utf-8")
    for doc in (ROOT / "platform/deploy/checklist.md", ROOT / "docs/host-actions.md", PAGE,
                ROOT / "scripts/platform_setup.py"):
        text = doc.read_text(encoding="utf-8")
        assert "python3 scripts/platform_setup.py" not in text, doc
        assert "SETUP_PAT" not in text and "platform-setup.yml" not in text, doc
    for command in ("secrets", "variables", "workos", 'review-app --code "'):
        assert f"python scripts/platform_setup.py {command}" in checklist, command
