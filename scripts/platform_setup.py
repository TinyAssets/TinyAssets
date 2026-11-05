"""The one-time platform setup (`.github/workflows/platform-setup.yml`).

Each command makes or moves what the dark deploy needs (`platform/deploy/checklist.md`),
only where it is absent, and never prints a value: a secret goes from where it is made or
read straight to GitHub's API, sealed with the environment's public key (libsodium's
sealed box, PyNaCl from a hashed lock), or to E2B's. Each command writes one line per name
to the job summary: the name and its status (set, already set, registered, ...).

    secret NAME --make mac|token|keyring|host   # a production secret, only if absent
    e2b                                         # E2B_API_KEY, E2B_WEBHOOK_SECRET, the webhook
    variables --issuer URL --client-id ID       # OWNER_ISSUER, WORKOS_CLIENT_ID, OWNER_TOKEN_CLAIMS
    review-app                                  # the review App from its manifest code

GH_TOKEN is the founder's short-lived SETUP_PAT; GITHUB_REPOSITORY names the repo.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GITHUB_API = "https://api.github.com"
E2B_API = "https://api.e2b.app"
PRODUCTION = "production"
REVIEW = "review"
# E2B's lifecycle webhook (`platform/doors/src/e2b-hooks.ts`), disabled until the cutover:
# the doors hold no route for it yet, and an enabled one sends the team's events nowhere.
HOOK_NAME = "tinyassets-lifecycle"
HOOK_URL = "https://tinyassets.io/hooks/e2b/lifecycle"
HOOK_EVENTS = ("sandbox.lifecycle.paused", "sandbox.lifecycle.killed")
# The one command the production host receives: the WorkOS key's line, to this stdout.
HOST_ENV_KEY = "WORKOS_API_KEY"
HOST_COMMAND = "sudo grep -E '^WORKOS_API_KEY=' /etc/tinyassets/env"
# The uptime canary's receipt of its renewed AuthKit token (scripts/canary_session.py).
CANARY_WORKFLOW = "uptime-canary.yml"
CLAIMS_ARTIFACT = "owner-token-claims"
CLAIMS_FILE = "owner-token-claims.json"
USER_AGENT = "tinyassets-platform-setup"

Fetch = Callable[..., tuple[int, object]]


class SetupError(RuntimeError):
    """A name and what went wrong, never a value."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: ANN002, ANN003 - urllib's signature
        return None


def fetch(method: str, url: str, *, token: str | None = None, body: object = None,
          headers: dict[str, str] | None = None, raw: bool = False,
          follow: bool = True) -> tuple[int, object]:
    """One HTTP call: its status and its JSON (raw bytes with `raw`; the Location header
    when `follow` is false and it redirects). Bodies of errors are never printed."""
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("User-Agent", USER_AGENT)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    opener = urllib.request.build_opener(*(() if follow else (_NoRedirect,)))
    try:
        with opener.open(request, timeout=30) as response:
            status, payload, location = response.status, response.read(), None
    except urllib.error.HTTPError as error:
        status, payload, location = error.code, error.read(), error.headers.get("Location")
    if not follow and location is not None:
        return status, location
    if raw:
        return status, payload
    try:
        return status, json.loads(payload) if payload else None
    except ValueError:
        return status, None


def report(name: str, status: str) -> None:
    """One summary line: a name and its status."""
    line = f"- `{name}`: {status}"
    print(line)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as out:
            out.write(line + "\n")


def seal(public_key: str, value: str) -> str:
    """`value` in a libsodium sealed box to GitHub's base64 public key, as its API takes it."""
    from nacl.public import PublicKey, SealedBox

    box = SealedBox(PublicKey(base64.b64decode(public_key)))
    return base64.b64encode(box.encrypt(value.encode("utf-8"))).decode("ascii")


class GitHub:
    """The repo's secrets, variables and environments, with the setup token."""

    def __init__(self, token: str, repo: str, call: Fetch = fetch) -> None:
        if not token:
            raise SetupError("SETUP_PAT is not set: nothing was changed")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise SetupError("GITHUB_REPOSITORY is not owner/name")
        self.token, self.repo, self._call = token, repo, call

    def call(self, method: str, path: str, body: object = None, **kwargs) -> tuple[int, object]:
        return self._call(method, f"{GITHUB_API}/repos/{self.repo}{path}", token=self.token,
                          body=body, headers={"Accept": "application/vnd.github+json",
                                              "X-GitHub-Api-Version": "2022-11-28"}, **kwargs)

    @staticmethod
    def _secrets(env: str | None) -> str:
        return f"/environments/{env}/secrets" if env else "/actions/secrets"

    def has_secret(self, name: str, env: str | None) -> bool:
        status, _ = self.call("GET", f"{self._secrets(env)}/{name}")
        if status in (200, 404):
            return status == 200
        raise SetupError(f"reading whether {name} is set failed ({status})")

    def put_secret(self, name: str, value: str, env: str | None) -> None:
        if not value:
            raise SetupError(f"{name} has no value: nothing stored")
        status, key = self.call("GET", f"{self._secrets(env)}/public-key")
        if status != 200 or not isinstance(key, dict) or not key.get("key"):
            raise SetupError(f"reading the public key for {name} failed ({status})")
        status, _ = self.call("PUT", f"{self._secrets(env)}/{name}", {
            "encrypted_value": seal(str(key["key"]), value), "key_id": key.get("key_id")})
        if status not in (201, 204):
            raise SetupError(f"storing {name} failed ({status})")

    def delete_secret(self, name: str, env: str | None) -> bool:
        status, _ = self.call("DELETE", f"{self._secrets(env)}/{name}")
        if status in (204, 404):
            return status == 204
        raise SetupError(f"deleting {name} failed ({status})")

    def variable(self, name: str) -> str | None:
        status, body = self.call("GET", f"/actions/variables/{name}")
        if status == 404:
            return None
        if status != 200 or not isinstance(body, dict):
            raise SetupError(f"reading the variable {name} failed ({status})")
        return str(body.get("value", ""))

    def set_variable(self, name: str, value: str, *, replace: bool = False) -> str:
        """Creates the variable; one already set is left unless `replace`. Its status."""
        held = self.variable(name)
        if held == value:
            return "already set"
        if held is not None and not replace:
            return "differs: left as it is"
        status, _ = (self.call("POST", "/actions/variables", {"name": name, "value": value})
                     if held is None else
                     self.call("PATCH", f"/actions/variables/{name}", {"name": name,
                                                                       "value": value}))
        if status not in (201, 204):
            raise SetupError(f"setting the variable {name} failed ({status})")
        return "set" if held is None else "replaced"


# --- the production secrets ------------------------------------------------------------


def make_mac() -> str:
    """32 random bytes, base64url: `ROUTING_MAC_KEY` (`doors/src/routing-mac.ts`)."""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")


def make_token() -> str:
    return secrets.token_urlsafe(32)


def make_keyring(run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> str:
    """A first keyring from the kit's own `keyring-new`, in RUNNER_TEMP, read, then shredded."""
    folder = tempfile.mkdtemp(dir=os.environ.get("RUNNER_TEMP") or None)
    path = Path(folder) / "keyring.json"
    try:
        done = run(["node", "--experimental-strip-types",
                    str(ROOT / "platform/deploy/deploy.ts"), "keyring-new", str(path)],
                   check=False, capture_output=True, text=True)
        if done.returncode != 0 or not path.is_file():
            raise SetupError(f"deploy.ts keyring-new failed (exit {done.returncode})")
        return path.read_text(encoding="utf-8")
    finally:
        if path.exists():
            run(["shred", "-u", str(path)], check=True, capture_output=True)
        os.rmdir(folder)


def host_value(text: str, name: str = HOST_ENV_KEY) -> str:
    """The value on `name`'s one line of the host's env file, its quotes removed."""
    lines = [line for line in text.splitlines() if line.startswith(f"{name}=")]
    if len(lines) != 1:
        raise SetupError(f"the host's env file holds {len(lines)} lines for {name}, not one")
    value = lines[0][len(name) + 1:].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
    # The production WorkOS environment's key (since 2026-08-29), never a staging one.
    if not re.fullmatch(r"sk_live_[A-Za-z0-9_]+", value):
        raise SetupError(f"{name} on the host is not one production WorkOS key (sk_live_)")
    return value


def read_host(run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> str:
    """The host's WorkOS key, over one ssh call whose remote command is HOST_COMMAND."""
    user, host = os.environ.get("DO_SSH_USER", ""), os.environ.get("DO_DROPLET_HOST", "")
    key = os.environ.get("SSH_KEY_FILE", "")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", user) or not host or not key:
        raise SetupError("DO_SSH_USER, DO_DROPLET_HOST and SSH_KEY_FILE are needed for the host")
    done = run(["ssh", "-n", "-i", key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                f"{user}@{host}", HOST_COMMAND], capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise SetupError(f"reading {HOST_ENV_KEY} from the host failed (ssh exit "
                         f"{done.returncode})")
    return host_value(done.stdout)


MAKERS: dict[str, Callable[[], str]] = {
    "mac": make_mac, "token": make_token, "keyring": make_keyring, "host": read_host}


def ensure_secret(gh: GitHub, name: str, make: Callable[[], str], env: str = PRODUCTION) -> str:
    """Stores `make()` as `name` only when it is absent; `make` runs only then."""
    if gh.has_secret(name, env):
        return "already set"
    gh.put_secret(name, make(), env)
    return "set"


# --- E2B -------------------------------------------------------------------------------


def e2b(gh: GitHub, key: str, hook_secret: str, call: Fetch = fetch) -> None:
    """E2B_API_KEY into `production` (from repository scope, whose copy is then deleted),
    E2B_WEBHOOK_SECRET made when absent, and the lifecycle webhook registered, disabled.

    `key` and `hook_secret` are what this job was given: `production`'s, else the
    repository's or the organization's."""
    in_production = gh.has_secret("E2B_API_KEY", PRODUCTION)
    at_repo = gh.has_secret("E2B_API_KEY", None)
    if in_production:
        report("E2B_API_KEY", "already set")
        if at_repo:
            report("E2B_API_KEY (repository)", "also set, left: it may be another key")
    elif key:
        gh.put_secret("E2B_API_KEY", key, PRODUCTION)
        report("E2B_API_KEY", "copied into production")
        if at_repo:
            gh.delete_secret("E2B_API_KEY", None)
            report("E2B_API_KEY (repository)", "deleted")
    else:
        report("E2B_API_KEY", "absent: add it to production from the E2B dashboard")
    made = not gh.has_secret("E2B_WEBHOOK_SECRET", PRODUCTION)
    if made:
        hook_secret = make_token()
        gh.put_secret("E2B_WEBHOOK_SECRET", hook_secret, PRODUCTION)
        report("E2B_WEBHOOK_SECRET", "set")
    else:
        report("E2B_WEBHOOK_SECRET", "already set")
        if not hook_secret:
            raise SetupError("E2B_WEBHOOK_SECRET is set but this job was not given it")
    if not key:
        report("E2B lifecycle webhook", "not registered: no E2B_API_KEY")
        return
    headers = {"X-API-Key": key, "Accept": "application/json"}
    status, hooks = call("GET", f"{E2B_API}/events/webhooks", headers=headers)
    if status != 200 or not isinstance(hooks, list):
        raise SetupError(f"E2B's webhook listing failed ({status})")
    ours = [hook for hook in hooks if isinstance(hook, dict) and hook.get("url") == HOOK_URL]
    if len(ours) > 1:
        raise SetupError(f"E2B holds {len(ours)} webhooks for {HOOK_URL}: keep one")
    if ours:
        state = "enabled" if ours[0].get("enabled") else "disabled"
        if not made:
            report("E2B lifecycle webhook", f"already registered, {state}")
            return
        # Its secret is not the one just made: give it this one.
        hook_id = urllib.parse.quote(str(ours[0].get("id", "")), safe="")
        status, _ = call("PATCH", f"{E2B_API}/events/webhooks/{hook_id}", headers=headers,
                         body={"signatureSecret": hook_secret})
        if status != 200:
            raise SetupError(f"giving E2B's webhook the new secret failed ({status})")
        report("E2B lifecycle webhook", f"registered, its secret replaced, {state}")
        return
    status, _ = call("POST", f"{E2B_API}/events/webhooks", headers=headers, body={
        "name": HOOK_NAME, "url": HOOK_URL, "events": list(HOOK_EVENTS), "enabled": False,
        "signatureSecret": hook_secret})
    if status not in (200, 201):
        raise SetupError(f"registering E2B's webhook failed ({status})")
    report("E2B lifecycle webhook", "registered, disabled until the cutover")


# --- the repository variables ----------------------------------------------------------


def check_issuer(issuer: str, call: Fetch = fetch) -> None:
    """The issuer's discovery names it exactly (the deploy kit checks the rest)."""
    status, discovery = call("GET", f"{issuer}/.well-known/openid-configuration",
                             headers={"Accept": "application/json"})
    if status != 200 or not isinstance(discovery, dict) or discovery.get("issuer") != issuer:
        raise SetupError(f"the issuer's discovery does not name {issuer} ({status})")


def claims_of(receipt: object, issuer: str) -> dict[str, object]:
    """`{iss, auth_time}` from the canary's receipt, held to the kit's own check."""
    if not isinstance(receipt, dict) or receipt.get("iss") != issuer:
        raise SetupError("the canary's token receipt does not name OWNER_ISSUER")
    auth_time = receipt.get("auth_time")
    if isinstance(auth_time, bool) or not isinstance(auth_time, int) or auth_time <= 0:
        raise SetupError("the canary's AuthKit token carries no auth_time")
    return {"iss": issuer, "auth_time": auth_time}


def canary_receipt(gh: GitHub, call: Fetch = fetch) -> object:
    """The newest receipt the uptime canary kept on `main`, or a SetupError."""
    status, runs = gh.call("GET", f"/actions/workflows/{CANARY_WORKFLOW}/runs?branch=main"
                                  "&per_page=30")
    if status != 200 or not isinstance(runs, dict):
        raise SetupError(f"listing the uptime canary's runs failed ({status})")
    for run in runs.get("workflow_runs") or []:
        if run.get("head_branch") != "main" or run.get("path") != (
                f".github/workflows/{CANARY_WORKFLOW}"):
            continue
        status, found = gh.call("GET", f"/actions/runs/{int(run['id'])}/artifacts"
                                       f"?name={CLAIMS_ARTIFACT}")
        if status != 200 or not isinstance(found, dict):
            raise SetupError(f"listing a canary run's artifacts failed ({status})")
        for artifact in found.get("artifacts") or []:
            if artifact.get("expired") or artifact.get("name") != CLAIMS_ARTIFACT:
                continue
            # GitHub answers with a redirect to storage, which is fetched without the token.
            status, where = gh.call("GET", f"/actions/artifacts/{int(artifact['id'])}/zip",
                                    follow=False)
            if status != 302 or not isinstance(where, str):
                raise SetupError(f"downloading the canary's receipt failed ({status})")
            status, data = call("GET", where, raw=True)
            if status != 200 or not isinstance(data, bytes):
                raise SetupError(f"downloading the canary's receipt failed ({status})")
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                return json.loads(archive.read(CLAIMS_FILE))
    raise SetupError("no uptime canary run on main kept a token receipt yet: wait for one "
                     "(every 5 minutes) and run this again")


def variables(gh: GitHub, issuer: str, client_id: str, call: Fetch = fetch) -> bool:
    """The three variables, each only where absent. False when one is left differing."""
    check_issuer(issuer, call)
    ok = True
    for name, value in (("OWNER_ISSUER", issuer), ("WORKOS_CLIENT_ID", client_id)):
        status = gh.set_variable(name, value)
        ok = ok and not status.startswith("differs")
        report(name, status)
    if gh.variable("OWNER_TOKEN_CLAIMS") is not None:
        report("OWNER_TOKEN_CLAIMS", "already set")
        return ok
    claims = claims_of(canary_receipt(gh, call), issuer)
    report("OWNER_TOKEN_CLAIMS", gh.set_variable("OWNER_TOKEN_CLAIMS", json.dumps(claims)))
    return ok


# --- the review App --------------------------------------------------------------------


def _main_only(gh: GitHub, env: str) -> bool:
    status, record = gh.call("GET", f"/environments/{env}")
    if status != 200 or not isinstance(record, dict):
        raise SetupError(f"reading the {env} environment failed ({status})")
    if record.get("deployment_branch_policy") != {"protected_branches": False,
                                                  "custom_branch_policies": True}:
        return False
    status, policies = gh.call("GET", f"/environments/{env}/deployment-branch-policies")
    if status != 200 or not isinstance(policies, dict):
        raise SetupError(f"reading the {env} environment's branches failed ({status})")
    return [(p.get("name"), p.get("type", "branch")) for p in
            policies.get("branch_policies") or []] == [("main", "branch")]


def ensure_environment(gh: GitHub, env: str = REVIEW) -> None:
    """`env`, made main-only when absent; one that exists must already be main-only."""
    status, _ = gh.call("GET", f"/environments/{env}")
    if status == 404:
        status, _ = gh.call("PUT", f"/environments/{env}", {"deployment_branch_policy": {
            "protected_branches": False, "custom_branch_policies": True}})
        if status != 200:
            raise SetupError(f"making the {env} environment failed ({status})")
        status, _ = gh.call("POST", f"/environments/{env}/deployment-branch-policies",
                            {"name": "main", "type": "branch"})
        if status != 200:
            raise SetupError(f"limiting the {env} environment to main failed ({status})")
        report(f"{env} environment", "made, main only")
    elif status == 200:
        report(f"{env} environment", "already there")
    else:
        raise SetupError(f"reading the {env} environment failed ({status})")
    if not _main_only(gh, env):
        raise SetupError(f"the {env} environment takes other branches than main: limit it to "
                         "main (Settings > Environments) and run this again")


def dispatch_input(name: str) -> str:
    """A workflow_dispatch input, read from the event file: an input in a step's `env:`
    would be printed in that step's log."""
    path = os.environ.get("GITHUB_EVENT_PATH", "")
    if not path:
        return ""
    event = json.loads(Path(path).read_text(encoding="utf-8"))
    return str((event.get("inputs") or {}).get(name) or "").strip()


def review_app(gh: GitHub, code: str, owner: str, call: Fetch = fetch) -> None:
    """The App the manifest page made: its key into `review`, its id as REVIEW_APP_ID."""
    if not code:
        report("REVIEW_APP_PRIVATE_KEY", "skipped: no review_app_code given")
        return
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", code):
        raise SetupError("review_app_code is not a manifest code")
    ensure_environment(gh, REVIEW)
    if gh.has_secret("REVIEW_APP_PRIVATE_KEY", REVIEW):
        report("REVIEW_APP_PRIVATE_KEY", "already set: the code was not used (delete the "
                                         "App it made)")
        return
    # GitHub's manifest flow, step 3: the code, within its hour, needs no token.
    status, app = call("POST", f"{GITHUB_API}/app-manifests/{code}/conversions",
                       headers={"Accept": "application/vnd.github+json"})
    if status != 201 or not isinstance(app, dict):
        raise SetupError(f"GitHub refused the manifest code ({status}): it is one-time and "
                         "lasts an hour; make the App again from the page")
    slug = str(app.get("slug", ""))
    made_by = str((app.get("owner") or {}).get("login", ""))
    if made_by.lower() != owner.lower():
        raise SetupError(f"the App {slug} belongs to {made_by}, not {owner}: delete it and "
                         "make it from the page again")
    pem = str(app.get("pem", ""))
    if not pem.startswith("-----BEGIN RSA PRIVATE KEY-----"):
        raise SetupError(f"the App {slug} came back with no private key")
    try:
        gh.put_secret("REVIEW_APP_PRIVATE_KEY", pem, REVIEW)
    except SetupError as error:
        raise SetupError(f"{error}; the App {slug} exists: generate a private key in its "
                         "settings and store it by hand") from None
    report("REVIEW_APP_PRIVATE_KEY", "set (review)")
    report("REVIEW_APP_ID", gh.set_variable("REVIEW_APP_ID", str(int(app["id"])),
                                            replace=True))
    report(f"App {slug}", f"made; install it: https://github.com/apps/{slug}/installations/new")


# --- the command line ------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("secret")
    one.add_argument("name")
    one.add_argument("--make", choices=sorted(MAKERS), required=True)
    sub.add_parser("e2b")
    names = sub.add_parser("variables")
    names.add_argument("--issuer", required=True)
    names.add_argument("--client-id", required=True)
    sub.add_parser("review-app")
    args = parser.parse_args(argv)
    try:
        gh = GitHub(os.environ.get("GH_TOKEN", ""), os.environ.get("GITHUB_REPOSITORY", ""))
        if args.command == "secret":
            report(args.name, ensure_secret(gh, args.name, MAKERS[args.make]))
        elif args.command == "e2b":
            e2b(gh, os.environ.get("E2B_API_KEY", ""), os.environ.get("E2B_WEBHOOK_SECRET", ""))
        elif args.command == "variables":
            if not variables(gh, args.issuer, args.client_id):
                return 1
        else:
            review_app(gh, dispatch_input("review_app_code"),
                       os.environ.get("GITHUB_REPOSITORY_OWNER", ""))
    except SetupError as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
