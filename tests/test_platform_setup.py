"""platform-setup.yml writes production secrets once, so its shape is held: dispatch only, on
main, in `production`, one run at a time, each secret in the steps that use it alone, every
action and package pinned, no token left in the checkout, the setup token deleted whatever
happened, nothing printed. Its script (scripts/platform_setup.py) sets a name only where it
is absent, against a fake of GitHub's and E2B's APIs."""

from __future__ import annotations

import base64
import importlib.util
import io
import json
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path

import pytest
import yaml

from scripts import platform_setup as setup

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/platform-setup.yml"
LOCK = ROOT / "scripts/platform_setup.requirements.txt"
PAGE = ROOT / "platform/deploy/review-app.html"
REPO = "TinyAssets/TinyAssets"
ISSUER = "https://unassuming-environment-16.authkit.app"


def _production_guard():
    spec = importlib.util.spec_from_file_location(
        "production_environment_guard", ROOT / "tests/test_workflow_production_environment.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_guard = _production_guard()


def _workflow() -> tuple[dict, dict]:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return workflow, workflow["jobs"]["setup"]


def _steps() -> list[dict]:
    return _workflow()[1]["steps"]


# --- the workflow's shape ----------------------------------------------------------------


def test_dispatch_only_on_main_in_production_one_at_a_time() -> None:
    workflow, job = _workflow()
    assert list(workflow.get("on", workflow.get(True))) == ["workflow_dispatch"]
    assert set(workflow["jobs"]) == {"setup"}
    assert job["if"] == "github.ref == 'refs/heads/main'"
    assert job["environment"] == "production"
    assert workflow["concurrency"] == {"group": "platform-setup", "cancel-in-progress": False}
    assert workflow["permissions"] == {"contents": "read"}
    assert "platform-setup.yml" in _guard._KNOWN_PRODUCTION_WORKFLOWS


def test_each_secret_is_in_the_steps_that_use_it_alone() -> None:
    """Every form of a secret reference, read by the production guard's own parser."""
    workflow, job = _workflow()
    outside = {**workflow, "jobs": {"setup": {k: v for k, v in job.items() if k != "steps"}}}
    assert _guard._secret_names(json.dumps(outside)) == set()
    found = {step.get("name", step.get("uses")): _guard._secret_names(json.dumps(step))
             for step in job["steps"]}
    assert {name: refs for name, refs in found.items() if refs} == {
        "The review App": {"SETUP_PAT"},
        "ROUTING_MAC_KEY": {"SETUP_PAT"},
        "GATEWAY_KEYRING": {"SETUP_PAT"},
        "FILES_KEYRING": {"SETUP_PAT"},
        "WORKOS_API_KEY from the host": {"SETUP_PAT", "DO_SSH_KEY", "DO_DROPLET_HOST",
                                         "DO_SSH_USER"},
        "E2B key, webhook secret and lifecycle webhook": {"SETUP_PAT", "E2B_API_KEY",
                                                          "E2B_WEBHOOK_SECRET"},
        "Repository variables": {"SETUP_PAT"},
        "Delete the setup token": {"SETUP_PAT"},
    }
    # The PAT is only ever the API's token, never another variable's value.
    for step in job["steps"]:
        for name, value in (step.get("env") or {}).items():
            if "SETUP_PAT" in str(value):
                assert name == "GH_TOKEN"


def test_actions_and_packages_are_pinned_and_no_token_is_kept() -> None:
    steps = _steps()
    uses = [step["uses"] for step in steps if "uses" in step]
    assert uses and all(re.fullmatch(r"actions/[a-z-]+@[0-9a-f]{40}", u) for u in uses)
    checkout = next(step for step in steps if "checkout" in step.get("uses", ""))
    assert checkout["with"]["persist-credentials"] is False
    runs = "\n".join(str(step.get("run", "")) for step in steps)
    assert "--require-hashes -r scripts/platform_setup.requirements.txt" in runs
    pins = [line for line in LOCK.read_text(encoding="utf-8").splitlines()
            if re.match(r"^[a-z0-9_.-]+==", line, re.I)]
    assert any(line.startswith("pynacl==") for line in pins)
    assert all(line.endswith("\\") for line in pins)


def test_the_setup_token_is_deleted_whatever_happened() -> None:
    last = _steps()[-1]
    assert last["name"] == "Delete the setup token" and last["if"] == "always()"
    assert "gh secret delete SETUP_PAT --env production" in last["run"]
    # A failed step leaves the rest to run; none is skipped by the one before it.
    for step in _steps()[4:-1]:
        assert str(step["if"]).startswith("${{ !cancelled()"), step["name"]


def test_nothing_is_printed_and_the_code_is_never_in_a_step_env() -> None:
    held = {name for step in _steps() for name, value in (step.get("env") or {}).items()
            if "secrets." in str(value)}
    assert held == {"GH_TOKEN", "SSH_KEY", "DO_DROPLET_HOST", "DO_SSH_USER", "E2B_API_KEY",
                    "E2B_WEBHOOK_SECRET"}
    for step in _steps():
        run = str(step.get("run", ""))
        assert "set -x" not in run and "::add-mask::" not in run
        for line in run.splitlines():
            if re.search(r"\b(echo|printf|cat|tee)\b", line):
                for name in held - {"DO_DROPLET_HOST"}:
                    assert name not in line or line.strip() == (
                        "(umask 077 && printf '%s\\n' \"${SSH_KEY}\" > \"${key_file}\")"), line
        assert "inputs.review_app_code" not in json.dumps(step.get("env") or {})
    host = next(step for step in _steps() if step.get("name") == "WORKOS_API_KEY from the host")
    # The one ssh call is the script's, with its fixed remote command.
    assert "ssh " not in host["run"].replace("ssh-keyscan ", "")
    assert setup.HOST_COMMAND == "sudo grep -E '^WORKOS_API_KEY=' /etc/tinyassets/env"


# --- a fake of GitHub's and E2B's APIs ----------------------------------------------------


class Fake:
    """GitHub's secrets, variables and environments, and E2B's webhooks, as the API holds
    them; every call recorded as `METHOD path`."""

    def __init__(self, secrets: dict[str | None, set[str]] | None = None,
                 variables: dict[str, str] | None = None,
                 environments: dict[str, list] | None = None,
                 hooks: list[dict] | None = None) -> None:
        self.secrets = {None: set(), "production": set(), "review": set(), **(secrets or {})}
        self.stored: dict[tuple[str | None, str], str] = {}
        self.variables = dict(variables or {})
        self.environments = dict(environments if environments is not None
                                 else {"production": [("main", "branch")]})
        self.hooks = list(hooks or [])
        self.calls: list[str] = []
        self.receipt: object = None

    def __call__(self, method, url, *, token=None, body=None, headers=None, raw=False,
                 follow=True):
        path = url.removeprefix(f"{setup.GITHUB_API}/repos/{REPO}")
        self.calls.append(f"{method} {path}")
        if url.startswith(setup.E2B_API):
            return self._e2b(method, url.removeprefix(setup.E2B_API), body, headers)
        if url == "https://storage/receipt.zip":
            data = io.BytesIO()
            with zipfile.ZipFile(data, "w") as archive:
                archive.writestr(setup.CLAIMS_FILE, json.dumps(self.receipt))
            return 200, data.getvalue()
        if url.startswith(f"{setup.GITHUB_API}/app-manifests/"):
            assert token is None
            return 201, {"id": 77, "slug": "tinyassets-review", "owner": {"login": "TinyAssets"},
                         "pem": "-----BEGIN RSA PRIVATE KEY-----\nx\n", "client_secret": "c",
                         "webhook_secret": None}
        if url == f"{ISSUER}/.well-known/openid-configuration":
            return 200, {"issuer": ISSUER}
        assert token == "pat"
        return self._github(method, path, body, follow)

    def _github(self, method, path, body, follow):
        if m := re.fullmatch(r"(?:/environments/(\w+))?(?:/actions)?/secrets/public-key", path):
            return 200, {"key": base64.b64encode(b"k" * 32).decode(), "key_id": "id1"}
        if m := re.fullmatch(r"(?:/environments/(\w+))?(?:/actions)?/secrets/(\w+)", path):
            env, name = m.group(1), m.group(2)
            held = self.secrets[env]
            if method == "GET":
                return (200 if name in held else 404), {}
            if method == "PUT":
                created = name not in held
                held.add(name)
                self.stored[(env, name)] = body["encrypted_value"]
                return (201 if created else 204), None
            if method == "DELETE":
                if name not in held:
                    return 404, {}
                held.discard(name)
                return 204, None
        if m := re.fullmatch(r"/actions/variables(?:/(\w+))?", path):
            name = m.group(1)
            if method == "GET":
                return (200, {"value": self.variables[name]}) if name in self.variables \
                    else (404, {})
            if method == "POST":
                assert body["name"] not in self.variables
                self.variables[body["name"]] = body["value"]
                return 201, {}
            if method == "PATCH":
                self.variables[name] = body["value"]
                return 204, None
        if m := re.fullmatch(r"/environments/(\w+)", path):
            env = m.group(1)
            if method == "GET":
                if env not in self.environments:
                    return 404, {}
                policy = ({"protected_branches": False, "custom_branch_policies": True}
                          if self.environments[env] is not None else None)
                return 200, {"name": env, "deployment_branch_policy": policy}
            if method == "PUT":
                self.environments[env] = []
                return 200, {}
        if m := re.fullmatch(r"/environments/(\w+)/deployment-branch-policies", path):
            env = m.group(1)
            if method == "POST":
                self.environments[env].append((body["name"], body["type"]))
                return 200, {}
            return 200, {"branch_policies": [{"name": n, "type": t}
                                             for n, t in self.environments[env] or []]}
        if path.startswith("/actions/workflows/uptime-canary.yml/runs"):
            return 200, {"workflow_runs": [
                {"id": 1, "head_branch": "feature", "path": ".github/workflows/uptime-canary.yml"},
                {"id": 2, "head_branch": "main", "path": ".github/workflows/uptime-canary.yml"},
            ]}
        if path == f"/actions/runs/2/artifacts?name={setup.CLAIMS_ARTIFACT}":
            return 200, {"artifacts": [] if self.receipt is None else [
                {"id": 9, "name": setup.CLAIMS_ARTIFACT, "expired": False}]}
        if path == "/actions/artifacts/9/zip":
            assert follow is False
            return 302, "https://storage/receipt.zip"
        raise AssertionError(f"unexpected {method} {path}")

    def _e2b(self, method, path, body, headers):
        assert headers["X-API-Key"] == "e2b-key"
        if method == "GET" and path == "/events/webhooks":
            return 200, self.hooks
        if method == "POST" and path == "/events/webhooks":
            self.hooks.append({"id": "w1", **body})
            return 201, {"id": "w1"}
        if method == "PATCH" and path.startswith("/events/webhooks/"):
            next(h for h in self.hooks if h["id"] == path.rsplit("/", 1)[1]).update(body)
            return 200, {}
        raise AssertionError(f"unexpected E2B {method} {path}")


@pytest.fixture(autouse=True)
def _sealed(monkeypatch, tmp_path):
    """Sealing is PyNaCl's (the workflow installs it from the lock); here it is marked, so
    each test can see what reached GitHub was sealed to its key."""
    monkeypatch.setattr(setup, "seal", lambda key, value: f"sealed[{key}]:{value}")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary"))


def _summary(tmp_path) -> list[str]:
    path = tmp_path / "summary"
    return path.read_text().splitlines() if path.exists() else []


def _gh(fake: Fake) -> setup.GitHub:
    return setup.GitHub("pat", REPO, fake)


# --- set only where absent ----------------------------------------------------------------


def test_a_secret_is_made_and_stored_only_where_absent(tmp_path) -> None:
    fake = Fake(secrets={"production": {"GATEWAY_KEYRING"}})
    made: list[str] = []

    def make() -> str:
        made.append("x")
        return "value"

    assert setup.ensure_secret(_gh(fake), "GATEWAY_KEYRING", make) == "already set"
    assert made == [] and not any(c.startswith("PUT") for c in fake.calls)
    assert setup.ensure_secret(_gh(fake), "ROUTING_MAC_KEY", make) == "set"
    key = base64.b64encode(b"k" * 32).decode()
    assert fake.stored == {("production", "ROUTING_MAC_KEY"): f"sealed[{key}]:value"}
    assert fake.calls[-2:] == ["GET /environments/production/secrets/public-key",
                               "PUT /environments/production/secrets/ROUTING_MAC_KEY"]


def test_the_made_values_are_the_shapes_the_platform_reads() -> None:
    mac = setup.make_mac()
    assert len(base64.urlsafe_b64decode(mac + "=")) == 32 and "=" not in mac
    assert setup.make_mac() != mac and len(setup.make_token()) >= 43


def test_a_keyring_is_the_kits_own_and_its_file_is_shredded(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    seen: list[list[str]] = []

    def run(argv, **_):
        seen.append(argv)
        if argv[0] == "node":
            Path(argv[-1]).write_text('{"active": "k1", "keys": {"k1": "a"}}')
        else:
            Path(argv[-1]).unlink()
        return type("Done", (), {"returncode": 0})()

    assert json.loads(setup.make_keyring(run))["active"] == "k1"
    assert seen[0][:4] == ["node", "--experimental-strip-types",
                           str(ROOT / "platform/deploy/deploy.ts"), "keyring-new"]
    assert seen[1][:2] == ["shred", "-u"] and seen[1][2] == seen[0][4]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(("text", "ok"), [
    ("WORKOS_API_KEY=sk_live_abc123\n", True),
    ("WORKOS_API_KEY='sk_live_abc123'\n", True),
    ('WORKOS_API_KEY="sk_live_abc123"\n', True),
    ("WORKOS_API_KEY=sk_test_abc123\n", False),
    ("", False),
    ("WORKOS_API_KEY=sk_live_a\nWORKOS_API_KEY=sk_live_b\n", False),
    ("WORKOS_API_KEY=sk_live_a b\n", False),
])
def test_the_host_key_is_its_one_production_line(text, ok) -> None:
    if ok:
        assert setup.host_value(text) == "sk_live_abc123"
    else:
        with pytest.raises(setup.SetupError) as refused:
            setup.host_value(text)
        assert "sk_live_a" not in str(refused.value) and "abc123" not in str(refused.value)


def test_the_host_is_asked_one_fixed_command_and_only_when_the_key_is_absent(monkeypatch):
    monkeypatch.setenv("DO_SSH_USER", "deploy")
    monkeypatch.setenv("DO_DROPLET_HOST", "203.0.113.7")
    monkeypatch.setenv("SSH_KEY_FILE", "/tmp/k")
    asked: list[list[str]] = []

    def run(argv, **_):
        asked.append(argv)
        return type("Done", (), {"returncode": 0, "stdout": "WORKOS_API_KEY=sk_live_k\n"})()

    monkeypatch.setitem(setup.MAKERS, "host", lambda: setup.read_host(run))
    fake = Fake(secrets={"production": {"WORKOS_API_KEY"}})
    assert setup.ensure_secret(_gh(fake), "WORKOS_API_KEY", setup.MAKERS["host"]) == "already set"
    assert asked == []
    fake = Fake()
    assert setup.ensure_secret(_gh(fake), "WORKOS_API_KEY", setup.MAKERS["host"]) == "set"
    assert asked == [["ssh", "-n", "-i", "/tmp/k", "-o", "BatchMode=yes", "-o",
                      "ConnectTimeout=10", "deploy@203.0.113.7", setup.HOST_COMMAND]]
    assert fake.stored[("production", "WORKOS_API_KEY")].endswith(":sk_live_k")


# --- E2B ----------------------------------------------------------------------------------


def test_e2b_copies_a_repository_key_deletes_that_copy_and_registers_disabled(tmp_path):
    fake = Fake(secrets={None: {"E2B_API_KEY"}})
    setup.e2b(_gh(fake), "e2b-key", "", fake)
    assert fake.secrets == {None: set(), "production": {"E2B_API_KEY", "E2B_WEBHOOK_SECRET"},
                            "review": set()}
    [hook] = fake.hooks
    secret = fake.stored[("production", "E2B_WEBHOOK_SECRET")].split(":", 1)[1]
    assert hook == {"id": "w1", "name": setup.HOOK_NAME, "url": setup.HOOK_URL,
                    "events": ["sandbox.lifecycle.paused", "sandbox.lifecycle.killed"],
                    "enabled": False, "signatureSecret": secret}
    assert _summary(tmp_path) == [
        "- `E2B_API_KEY`: copied into production",
        "- `E2B_API_KEY (repository)`: deleted",
        "- `E2B_WEBHOOK_SECRET`: set",
        "- `E2B lifecycle webhook`: registered, disabled until the cutover"]
    # Run again: nothing changes.
    calls = len(fake.calls)
    setup.e2b(_gh(fake), "e2b-key", secret, fake)
    assert not [c for c in fake.calls[calls:] if not c.startswith("GET")]
    assert _summary(tmp_path)[-1] == "- `E2B lifecycle webhook`: already registered, disabled"


def test_e2b_without_a_key_makes_the_secret_and_says_what_is_owed(tmp_path):
    fake = Fake()
    setup.e2b(_gh(fake), "", "", fake)
    assert fake.secrets["production"] == {"E2B_WEBHOOK_SECRET"} and fake.hooks == []
    assert _summary(tmp_path)[0] == (
        "- `E2B_API_KEY`: absent: add it to production from the E2B dashboard")
    assert _summary(tmp_path)[-1] == "- `E2B lifecycle webhook`: not registered: no E2B_API_KEY"


def test_e2b_gives_a_registered_hook_a_secret_made_now_and_leaves_a_held_key(tmp_path):
    fake = Fake(secrets={None: {"E2B_API_KEY"}, "production": {"E2B_API_KEY"}},
                hooks=[{"id": "w9", "url": setup.HOOK_URL, "enabled": False,
                        "signatureSecret": "old"}])
    setup.e2b(_gh(fake), "e2b-key", "", fake)
    secret = fake.stored[("production", "E2B_WEBHOOK_SECRET")].split(":", 1)[1]
    assert fake.hooks[0]["signatureSecret"] == secret != "old"
    assert "E2B_API_KEY" in fake.secrets[None]
    assert "- `E2B_API_KEY (repository)`: also set, left: it may be another key" in (
        _summary(tmp_path))


# --- the variables ------------------------------------------------------------------------


def test_the_variables_are_set_once_from_the_canarys_main_receipt(tmp_path):
    fake = Fake()
    fake.receipt = {"iss": ISSUER, "auth_time": 1767225600}
    assert setup.variables(_gh(fake), ISSUER, "client_01", fake)
    assert fake.variables == {
        "OWNER_ISSUER": ISSUER, "WORKOS_CLIENT_ID": "client_01",
        "OWNER_TOKEN_CLAIMS": json.dumps({"iss": ISSUER, "auth_time": 1767225600})}
    assert "GET /actions/runs/1/artifacts?name=owner-token-claims" not in fake.calls
    # Never the token on the storage URL GitHub redirects to.
    assert "GET https://storage/receipt.zip" in fake.calls
    # Run again: every one already set, nothing written.
    calls = len(fake.calls)
    assert setup.variables(_gh(fake), ISSUER, "client_01", fake)
    assert all(c.startswith("GET") for c in fake.calls[calls:])
    # One set by hand to something else is left, and the run says so.
    fake.variables["WORKOS_CLIENT_ID"] = "client_other"
    assert not setup.variables(_gh(fake), ISSUER, "client_01", fake)
    assert fake.variables["WORKOS_CLIENT_ID"] == "client_other"


@pytest.mark.parametrize("receipt", [None, {"iss": ISSUER}, {"iss": "https://other",
                                                             "auth_time": 1},
                                     {"iss": ISSUER, "auth_time": "1"},
                                     {"iss": ISSUER, "auth_time": True}])
def test_owner_token_claims_needs_a_receipt_naming_the_issuer_with_auth_time(receipt):
    fake = Fake()
    fake.receipt = receipt
    with pytest.raises(setup.SetupError):
        setup.variables(_gh(fake), ISSUER, "client_01", fake)
    assert "OWNER_TOKEN_CLAIMS" not in fake.variables


# --- the review App -----------------------------------------------------------------------


def test_the_review_app_key_goes_to_a_main_only_review_environment(tmp_path):
    fake = Fake()
    setup.review_app(_gh(fake), "a180b1a3d263c81bc6441d7b990bae27d4c10679", "TinyAssets", fake)
    assert fake.environments["review"] == [("main", "branch")]
    assert fake.secrets["review"] == {"REVIEW_APP_PRIVATE_KEY"}
    assert fake.variables == {"REVIEW_APP_ID": "77"}
    assert fake.calls.index("PUT /environments/review") < fake.calls.index(
        "POST https://api.github.com/app-manifests/a180b1a3d263c81bc6441d7b990bae27d4c10679"
        "/conversions")
    summary = "\n".join(_summary(tmp_path))
    assert "BEGIN" not in summary and "installations/new" in summary
    # A second code is not spent while the key is held.
    calls = len(fake.calls)
    setup.review_app(_gh(fake), "b" * 40, "TinyAssets", fake)
    assert not [c for c in fake.calls[calls:] if "app-manifests" in c]


@pytest.mark.parametrize("policy", [None, [("main", "branch"), ("release/*", "branch")],
                                    [("v*", "tag")]])
def test_a_review_environment_open_to_other_refs_is_refused(policy):
    fake = Fake(environments={"review": policy})
    with pytest.raises(setup.SetupError, match="other branches than main"):
        setup.review_app(_gh(fake), "c" * 40, "TinyAssets", fake)
    assert not [c for c in fake.calls if "app-manifests" in c]


def test_no_code_is_no_change_and_a_bad_one_is_refused(tmp_path):
    fake = Fake()
    setup.review_app(_gh(fake), "", "TinyAssets", fake)
    assert fake.calls == []
    with pytest.raises(setup.SetupError):
        setup.review_app(_gh(fake), "../../x", "TinyAssets", fake)
    assert fake.calls == []


def test_the_code_is_read_from_the_event_file(monkeypatch, tmp_path):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"inputs": {"review_app_code": " abc "}}))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    assert setup.dispatch_input("review_app_code") == "abc"


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
    manifest = json.loads(field["value"])
    assert manifest == {
        "name": "TinyAssets Review", "url": "https://tinyassets.io",
        "hook_attributes": {"url": "https://tinyassets.io/", "active": False},
        "redirect_url": "https://github.com/TinyAssets/TinyAssets/actions/workflows/"
                        "platform-setup.yml",
        "public": False,
        "default_permissions": {"checks": "write", "statuses": "write", "metadata": "read"},
        "default_events": []}
