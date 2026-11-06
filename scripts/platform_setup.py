"""The platform's one-time setup, run on the founder's own computer with their own `gh`.

The founder's Claude session runs it (Windows: `python`, PowerShell or cmd alike). GitHub's
API takes each write from the founder's `gh` login (`repo` scope), so no setup token exists.
Every value is set only where it is absent, and travels on stdin alone: never in an argv,
on disk or in the output, which names each value and its status, nothing more.

    python scripts/platform_setup.py secrets    # ROUTING_MAC_KEY, E2B_WEBHOOK_SECRET, the keyrings
    python scripts/platform_setup.py variables  # OWNER_ISSUER, WORKOS_CLIENT_ID
    Get-Clipboard | python scripts/platform_setup.py workos   # a WorkOS key into `production`
    # (Git Bash: powershell.exe -NoProfile -Command Get-Clipboard | python ... workos)
    python scripts/platform_setup.py workos --delete-repo-copies  # then the repository copies
    python scripts/platform_setup.py review-app --code "<code, or the URL GitHub landed on>"

E2B's lifecycle webhook is the deploy kit's to register (`platform/deploy/deploy.ts`).
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
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

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

    def secret(self, name: str, env: str | None) -> dict | None:
        """The held secret's record (its name and `updated_at`; GitHub never returns a
        value), or None when it is absent."""
        status, body = self.call("GET", f"{self._secrets(env)}/{name}")
        if status == 404:
            return None
        if status != 200:
            raise SetupError(f"reading whether {name} is set failed ({status})")
        return body if isinstance(body, dict) else {}

    def has_secret(self, name: str, env: str | None) -> bool:
        return self.secret(name, env) is not None

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
    """A WorkOS secret key, never a labelled staging one. WorkOS's current keys carry no
    environment label (`sk_` alone; 2026-11-05), so the environment is the dashboard's:
    the key is made with Production selected. A staging label in any case, or a body too
    short to be a key, is refused."""
    return re.fullmatch(r"sk_(?!(?i:test_))[A-Za-z0-9_]{20,}", value) is not None


def bare(text: str) -> str:
    """Pasted text without the spaces or quotes a shell or a copy left around it."""
    return text.strip().strip("'\"").strip()


def read_key(prompt: str) -> str:
    """The pasted key: one line of stdin when it is not a terminal (an agent's shell, or
    `Get-Clipboard | python ...`; Windows' getpass reads only the console), else a prompt
    that does not echo."""
    if not sys.stdin.isatty():
        return sys.stdin.readline().rstrip("\r\n")
    return getpass.getpass(prompt)


def _held(record: dict) -> str:
    return f"already set (updated {record.get('updated_at', 'at an unknown time')})"


def workos(gh: Gh, ask: Callable[[str], str] = read_key, *,
           delete_repo_copies: bool = False) -> None:
    """Two steps. First: a production WorkOS key, from stdin or a prompt that does not
    echo, into `production`, only when it holds none; a held one is shown with its
    `updated_at`. Then, with `delete_repo_copies` and only once `production` holds the key,
    the repository-level copies are deleted; nothing is asked in that step."""
    held = gh.secret(WORKOS_KEY, PRODUCTION)
    if delete_repo_copies:
        if held is None:
            raise SetupError(f"production holds no {WORKOS_KEY}: store it first; the "
                             "repository copies are left")
        say(WORKOS_KEY, _held(held))
        for name in WORKOS_REPO_COPIES:
            say(f"{name} (repository)", "deleted" if gh.delete_secret(name, None)
                else "already gone")
        return
    if held is not None:
        say(WORKOS_KEY, _held(held))
    else:
        value = bare(ask("Paste the production WorkOS API key (sk_..., not shown): "))
        if not live_workos_key(value):
            raise SetupError("that is not a production WorkOS secret key: nothing stored")
        gh.set_secret(WORKOS_KEY, value, PRODUCTION)
        stored = gh.secret(WORKOS_KEY, PRODUCTION)
        if stored is None:
            raise SetupError(f"{WORKOS_KEY} does not read back from production")
        say(WORKOS_KEY, f"set (updated {stored.get('updated_at', 'now')})")
    say("repository copies", "left: once production's key above is the one you made, run "
                             "workos --delete-repo-copies")


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


def variables(gh: Gh, fetch: Fetch = fetch_json) -> bool:
    """The two variables, each only where absent. False when one is left differing."""
    check_issuer(OWNER_ISSUER, fetch)
    ok = True
    for name, value in (("OWNER_ISSUER", OWNER_ISSUER), ("WORKOS_CLIENT_ID", WORKOS_CLIENT_ID)):
        status = gh.set_variable(name, value)
        ok = ok and not status.startswith("differs")
        say(name, status)
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


def _policies(gh: Gh, env: str) -> list[tuple[object, object]] | None:
    """The environment's branch policies, or None when it takes branches by no list of
    its own (all, or protected ones)."""
    status, record = gh.call("GET", f"/environments/{env}")
    if status != 200 or not isinstance(record, dict):
        raise SetupError(f"reading the {env} environment failed ({status})")
    if record.get("deployment_branch_policy") != MAIN_ONLY:
        return None
    status, policies = gh.call("GET", f"/environments/{env}/deployment-branch-policies")
    if status != 200 or not isinstance(policies, dict):
        raise SetupError(f"reading the {env} environment's branches failed ({status})")
    return [(p.get("name"), p.get("type", "branch")) for p in
            policies.get("branch_policies") or []]


def _limit_to_main(gh: Gh, env: str) -> None:
    status, _ = gh.call("POST", f"/environments/{env}/deployment-branch-policies",
                        {"name": "main", "type": "branch"})
    if status != 200:
        raise SetupError(f"limiting the {env} environment to main failed ({status})")


def ensure_environment(gh: Gh, env: str = REVIEW) -> str:
    """`env`, made main-only when absent; one that exists must already be main-only."""
    status, _ = gh.call("GET", f"/environments/{env}")
    if status == 404:
        status, _ = gh.call("PUT", f"/environments/{env}",
                            {"deployment_branch_policy": MAIN_ONLY})
        if status != 200:
            raise SetupError(f"making the {env} environment failed ({status})")
        _limit_to_main(gh, env)
        made = "made, main only"
    elif status == 200:
        made = "already there"
        if _policies(gh, env) == []:
            # Made by a run that stopped before its policy: finish it.
            _limit_to_main(gh, env)
            made = "already there; limited to main now"
    else:
        raise SetupError(f"reading the {env} environment failed ({status})")
    if _policies(gh, env) != [("main", "branch")]:
        raise SetupError(f"the {env} environment takes other branches than main: limit it to "
                         "main (Settings > Environments) and run this again")
    return made


def review_app(code: str, gh: Gh, turn_in: Fetch = convert) -> None:
    """The App the manifest page made: its key into `review`, its id as REVIEW_APP_ID.
    Whoever turns the code in first gets the key, so this runs only on the founder's own
    computer, the hour the App is made."""
    say(f"{REVIEW} environment", ensure_environment(gh))
    held_id = gh.variable(REVIEW_ID)
    if gh.has_secret(REVIEW_KEY, REVIEW):
        say(REVIEW_KEY, f"already set (App {held_id or 'id unknown'}); the code was not "
                        "turned in: if it came from an App made just now, delete that one, "
                        f"not App {held_id or 'the one installed'}")
        return
    status, app = turn_in(code)
    if status != 201 or not isinstance(app, dict):
        if held_id:
            raise SetupError(f"GitHub refused the code ({status}); App {held_id} is "
                             f"{REVIEW_ID} without its key: generate a private key in its "
                             "settings and store it with gh secret set")
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
    app_id = str(int(app["id"]))
    # Neither is secret: named before anything is stored, so a failure below names its App.
    say(f"App {slug}", f"made, id {app_id}")
    say(REVIEW_ID, gh.set_variable(REVIEW_ID, app_id, replace=True))
    try:
        gh.set_secret(REVIEW_KEY, pem, REVIEW)
    except SetupError as error:
        raise SetupError(f"{error}; App {app_id} ({slug}) exists: generate a private key in "
                         "its settings and store it with gh secret set") from None
    say(REVIEW_KEY, f"set ({REVIEW})")
    say(f"App {slug}", f"install it on {REPO} alone: "
                       f"https://github.com/apps/{slug}/installations/new")


# --- the command line ------------------------------------------------------------------


def main(argv: list[str] | None = None, gh: Gh | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("secrets")
    sub.add_parser("variables")
    work = sub.add_parser("workos")
    work.add_argument("--delete-repo-copies", action="store_true",
                      help="the second step: delete the repository-level copies, once "
                           "production holds the key")
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
            workos(gh, delete_repo_copies=args.delete_repo_copies)
        else:
            review_app(code_of(args.code), gh)
    except SetupError as error:
        print(f"platform_setup: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
