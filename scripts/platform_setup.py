"""The platform's one-time setup, run on the founder's own computer with their own `gh`.

The founder's Claude session runs it (Windows: `python`, PowerShell or cmd alike). GitHub's
API takes each write from the founder's `gh` login (`repo` scope), so no setup token exists.
Every value is set only where it is absent, and travels on stdin alone: never in an argv,
on disk or in the output, which names each value and its status, nothing more.

    python scripts/platform_setup.py secrets    # ROUTING_MAC_KEY, E2B_WEBHOOK_SECRET, the keyrings
    python scripts/platform_setup.py variables  # OWNER_ISSUER, WORKOS_CLIENT_ID, OWNER_TOKEN_CLAIMS
    python scripts/platform_setup.py workos     # a pasted production WorkOS key into `production`
    python scripts/platform_setup.py review-app --code "<code, or the URL GitHub landed on>"

`variables` needs one uptime canary run on `main` after this change lands: OWNER_TOKEN_CLAIMS
is that run's receipt of its renewed AuthKit token (`iss` and `auth_time` alone). E2B's
lifecycle webhook is the deploy kit's to register (`platform/deploy/deploy.ts`).
"""

from __future__ import annotations

import argparse
import base64
import getpass
import json
import re
import secrets
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

REPO = "TinyAssets/TinyAssets"
OWNER = "TinyAssets"
PRODUCTION = "production"
REVIEW = "review"
# Checked live: AuthKit's discovery, and the AuthKit login's own client id.
OWNER_ISSUER = "https://unassuming-environment-16.authkit.app"
WORKOS_CLIENT_ID = "client_01KW15P0PD1R9ETW3DTP8R9G3B"
WORKOS_KEY = "WORKOS_API_KEY"
# Repository-level copies the `workos` move retires (no job outside `production` reads them).
WORKOS_REPO_COPIES = ("WORKOS_API_KEY", "WORKOS_AUTHKIT_DOMAIN")
# The review App's names, which the review workflow reads.
REVIEW_KEY = "REVIEW_APP_PRIVATE_KEY"
REVIEW_ID = "REVIEW_APP_ID"
CONVERSIONS = "https://api.github.com/app-manifests/{code}/conversions"
# The uptime canary's receipt of its renewed AuthKit token (scripts/canary_session.py).
CANARY_WORKFLOW = "uptime-canary.yml"
CLAIMS_ARTIFACT = "owner-token-claims"
CLAIMS_FILE = "owner-token-claims.json"
MAIN_ONLY = {"protected_branches": False, "custom_branch_policies": True}
USER_AGENT = "tinyassets-platform-setup"

Run = Callable[..., subprocess.CompletedProcess]
Fetch = Callable[[str], tuple[int, object]]


class SetupError(RuntimeError):
    """A name and what went wrong, never a value."""


def say(name: str, status: str) -> None:
    print(f"{name}: {status}")


class Gh:
    """The founder's own `gh`: REST calls with their login, and `gh secret` / `gh variable`.

    `gh` runs as an argv list with no shell, so nothing is quoted by hand on any system; a
    value goes on stdin as UTF-8 bytes, so Windows neither turns its newlines into CRLF nor
    reads gh's answer in its own code page."""

    def __init__(self, repo: str = REPO, run: Run = subprocess.run) -> None:
        self.repo, self._run = repo, run

    def _gh(self, args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
        done = self._run(["gh", *args], input=None if stdin is None else stdin.encode("utf-8"),
                         capture_output=True, check=False)
        out = done.stdout.decode("utf-8") if isinstance(done.stdout, bytes) else done.stdout
        return subprocess.CompletedProcess(["gh", *args], done.returncode, out or "")

    def call(self, method: str, path: str, body: object = None) -> tuple[int, object]:
        """One REST call through `gh api -i`: its HTTP status and its JSON."""
        args = ["api", "-i", "--method", method, f"repos/{self.repo}{path}"]
        if body is not None:
            args += ["--input", "-"]
        done = self._gh(args, None if body is None else json.dumps(body))
        head, _, rest = done.stdout.replace("\r\n", "\n").partition("\n\n")
        status = re.match(r"HTTP/\S+ (\d{3})", head)
        if status is None:
            raise SetupError(f"gh api {method} {path} gave no answer (exit {done.returncode})")
        try:
            return int(status.group(1)), json.loads(rest) if rest.strip() else None
        except ValueError:
            return int(status.group(1)), None

    @staticmethod
    def _secrets(env: str | None) -> str:
        return f"/environments/{env}/secrets" if env else "/actions/secrets"

    def has_secret(self, name: str, env: str | None) -> bool:
        status, _ = self.call("GET", f"{self._secrets(env)}/{name}")
        if status in (200, 404):
            return status == 200
        raise SetupError(f"reading whether {name} is set failed ({status})")

    def set_secret(self, name: str, value: str, env: str | None) -> None:
        """`gh secret set`, the value on stdin: gh seals it to the environment's key."""
        if not value:
            raise SetupError(f"{name} has no value: nothing stored")
        where = ["--env", env] if env else []
        done = self._gh(["secret", "set", name, *where, "--repo", self.repo], value)
        if done.returncode != 0:
            raise SetupError(f"storing {name} failed (exit {done.returncode})")

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
        """Sets the variable, its value on stdin; one already set is left unless `replace`."""
        held = self.variable(name)
        if held == value:
            return "already set"
        if held is not None and not replace:
            return "differs: left as it is"
        done = self._gh(["variable", "set", name, "--repo", self.repo], value)
        if done.returncode != 0:
            raise SetupError(f"setting the variable {name} failed (exit {done.returncode})")
        return "set" if held is None else "replaced"

    def runs(self, workflow: str) -> list[dict]:
        done = self._gh(["run", "list", "--workflow", workflow, "--branch", "main", "--limit",
                         "30", "--json", "databaseId,headBranch", "--repo", self.repo])
        if done.returncode != 0:
            raise SetupError(f"listing {workflow}'s runs failed (exit {done.returncode})")
        return json.loads(done.stdout or "[]")

    def download(self, run_id: int, artifact: str, into: str) -> bool:
        """`gh run download` of one run's artifact; False when that run kept none."""
        done = self._gh(["run", "download", str(run_id), "--name", artifact, "--dir", into,
                         "--repo", self.repo])
        return done.returncode == 0


# --- the production secrets ----------------------------------------------------------------


def make_mac() -> str:
    """32 random bytes, base64url: `ROUTING_MAC_KEY` (`doors/src/routing-mac.ts`)."""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")


def make_token() -> str:
    return secrets.token_urlsafe(32)


def make_keyring() -> str:
    """A first keyring, in memory, as `deploy.ts keyring-new` writes one: `{active, keys}`,
    one 32-byte key `k1`, base64 (the kit's `keyring()` takes it; its guard test proves so)."""
    return json.dumps({"active": "k1", "keys": {"k1": base64.b64encode(
        secrets.token_bytes(32)).decode("ascii")}}, separators=(",", ":"))


MADE: dict[str, Callable[[], str]] = {
    "ROUTING_MAC_KEY": make_mac, "E2B_WEBHOOK_SECRET": make_token,
    "GATEWAY_KEYRING": make_keyring, "FILES_KEYRING": make_keyring}


def ensure_secret(gh: Gh, name: str, make: Callable[[], str], env: str = PRODUCTION) -> str:
    """Stores `make()` as `name` only when it is absent; `make` runs only then."""
    if gh.has_secret(name, env):
        return "already set"
    gh.set_secret(name, make(), env)
    return "set"


def made_secrets(gh: Gh) -> None:
    for name, make in MADE.items():
        say(name, ensure_secret(gh, name, make))


# --- WorkOS -----------------------------------------------------------------------------


def live_workos_key(value: str) -> bool:
    """The production WorkOS environment's key (since 2026-08-29), never a staging one."""
    return re.fullmatch(r"sk_live_[A-Za-z0-9_]+", value) is not None


def bare(text: str) -> str:
    """Pasted text without the spaces or quotes a shell or a copy left around it."""
    return text.strip().strip("'\"").strip()


def workos(gh: Gh, ask: Callable[[str], str] = getpass.getpass) -> None:
    """A production WorkOS key, pasted at a prompt that does not echo, into `production`;
    then the repository-level copies retired. Asks nothing when `production` holds one."""
    if gh.has_secret(WORKOS_KEY, PRODUCTION):
        say(WORKOS_KEY, "already set")
    else:
        value = bare(ask("Paste the production WorkOS API key (sk_live_..., not shown): "))
        if not live_workos_key(value):
            raise SetupError("that is not a production WorkOS key (sk_live_): nothing stored")
        gh.set_secret(WORKOS_KEY, value, PRODUCTION)
        say(WORKOS_KEY, "set")
    for name in WORKOS_REPO_COPIES:
        say(f"{name} (repository)", "deleted" if gh.delete_secret(name, None)
            else "already gone")


# --- the repository variables ----------------------------------------------------------


def fetch_json(url: str) -> tuple[int, object]:
    request = urllib.request.Request(url, headers={"Accept": "application/json",
                                                   "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        return error.code, None


def check_issuer(issuer: str, fetch: Fetch = fetch_json) -> None:
    """The issuer's discovery names it exactly (the deploy kit checks the rest)."""
    status, discovery = fetch(f"{issuer}/.well-known/openid-configuration")
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


def canary_receipt(gh: Gh) -> object:
    """The newest receipt an uptime canary run on `main` kept, or a SetupError."""
    for run in gh.runs(CANARY_WORKFLOW):
        if run.get("headBranch") != "main":
            continue
        with tempfile.TemporaryDirectory() as into:
            if gh.download(int(run["databaseId"]), CLAIMS_ARTIFACT, into):
                found = list(Path(into).rglob(CLAIMS_FILE))
                if len(found) != 1:
                    raise SetupError("the canary's receipt is not one file")
                return json.loads(found[0].read_text(encoding="utf-8"))
    raise SetupError("no uptime canary run on main kept a token receipt yet: wait for one "
                     "(every 5 minutes, once this change is on main) and run this again")


def variables(gh: Gh, fetch: Fetch = fetch_json) -> bool:
    """The three variables, each only where absent. False when one is left differing."""
    check_issuer(OWNER_ISSUER, fetch)
    ok = True
    for name, value in (("OWNER_ISSUER", OWNER_ISSUER), ("WORKOS_CLIENT_ID", WORKOS_CLIENT_ID)):
        status = gh.set_variable(name, value)
        ok = ok and not status.startswith("differs")
        say(name, status)
    if gh.variable("OWNER_TOKEN_CLAIMS") is not None:
        say("OWNER_TOKEN_CLAIMS", "already set")
        return ok
    claims = claims_of(canary_receipt(gh), OWNER_ISSUER)
    say("OWNER_TOKEN_CLAIMS", gh.set_variable("OWNER_TOKEN_CLAIMS", json.dumps(claims)))
    return ok


# --- the review App --------------------------------------------------------------------


def code_of(text: str) -> str:
    """The manifest code, given bare or as the URL GitHub redirected to."""
    text = bare(text)
    if "?" in text:
        text = (urllib.parse.parse_qs(urllib.parse.urlsplit(text).query).get("code") or [""])[0]
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", text):
        raise SetupError("that is not a manifest code")
    return text


def convert(code: str) -> tuple[int, object]:
    """GitHub's manifest flow, step 3, with no token (its REST docs list no token type)."""
    request = urllib.request.Request(CONVERSIONS.format(code=code), data=b"", method="POST",
                                     headers={"Accept": "application/vnd.github+json",
                                              "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        return error.code, None


def _main_only(gh: Gh, env: str) -> bool:
    status, record = gh.call("GET", f"/environments/{env}")
    if status != 200 or not isinstance(record, dict):
        raise SetupError(f"reading the {env} environment failed ({status})")
    if record.get("deployment_branch_policy") != MAIN_ONLY:
        return False
    status, policies = gh.call("GET", f"/environments/{env}/deployment-branch-policies")
    if status != 200 or not isinstance(policies, dict):
        raise SetupError(f"reading the {env} environment's branches failed ({status})")
    return [(p.get("name"), p.get("type", "branch")) for p in
            policies.get("branch_policies") or []] == [("main", "branch")]


def ensure_environment(gh: Gh, env: str = REVIEW) -> str:
    """`env`, made main-only when absent; one that exists must already be main-only."""
    status, _ = gh.call("GET", f"/environments/{env}")
    if status == 404:
        status, _ = gh.call("PUT", f"/environments/{env}",
                            {"deployment_branch_policy": MAIN_ONLY})
        if status != 200:
            raise SetupError(f"making the {env} environment failed ({status})")
        status, _ = gh.call("POST", f"/environments/{env}/deployment-branch-policies",
                            {"name": "main", "type": "branch"})
        if status != 200:
            raise SetupError(f"limiting the {env} environment to main failed ({status})")
        made = "made, main only"
    elif status == 200:
        made = "already there"
    else:
        raise SetupError(f"reading the {env} environment failed ({status})")
    if not _main_only(gh, env):
        raise SetupError(f"the {env} environment takes other branches than main: limit it to "
                         "main (Settings > Environments) and run this again")
    return made


def review_app(code: str, gh: Gh, turn_in: Fetch = convert) -> None:
    """The App the manifest page made: its key into `review`, its id as REVIEW_APP_ID.
    Whoever turns the code in first gets the key, so this runs only on the founder's own
    computer, the hour the App is made."""
    say(f"{REVIEW} environment", ensure_environment(gh))
    if gh.has_secret(REVIEW_KEY, REVIEW):
        raise SetupError(f"{REVIEW_KEY} is already set; the code was not used: delete the App "
                         "it made (organization settings, GitHub Apps)")
    status, app = turn_in(code)
    if status != 201 or not isinstance(app, dict):
        raise SetupError(f"GitHub refused the code ({status}): it is one-time and lasts an "
                         "hour; make the App again from the page")
    slug = str(app.get("slug", ""))
    made_by = str((app.get("owner") or {}).get("login", ""))
    if made_by.lower() != OWNER.lower():
        raise SetupError(f"the App {slug} belongs to {made_by}, not {OWNER}: delete it and "
                         "make it from the page again")
    pem = str(app.get("pem", ""))
    if not pem.startswith("-----BEGIN RSA PRIVATE KEY-----"):
        raise SetupError(f"the App {slug} came back with no private key")
    try:
        gh.set_secret(REVIEW_KEY, pem, REVIEW)
    except SetupError as error:
        raise SetupError(f"{error}; the App {slug} exists: generate a private key in its "
                         "settings and store it with gh secret set") from None
    say(REVIEW_KEY, f"set ({REVIEW})")
    say(REVIEW_ID, gh.set_variable(REVIEW_ID, str(int(app["id"])), replace=True))
    say(f"App {slug}", f"made; install it on {REPO} alone: "
                       f"https://github.com/apps/{slug}/installations/new")


# --- the command line ------------------------------------------------------------------


def main(argv: list[str] | None = None, gh: Gh | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("secrets")
    sub.add_parser("variables")
    sub.add_parser("workos")
    app = sub.add_parser("review-app")
    app.add_argument("--code", required=True,
                     help="the code, or the whole URL GitHub redirected to")
    args = parser.parse_args(argv)
    gh = gh or Gh()
    try:
        if args.command == "secrets":
            made_secrets(gh)
        elif args.command == "variables":
            if not variables(gh):
                return 1
        elif args.command == "workos":
            workos(gh)
        else:
            review_app(code_of(args.code), gh)
    except SetupError as error:
        print(f"platform_setup: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
