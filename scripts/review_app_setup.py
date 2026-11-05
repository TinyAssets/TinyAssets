"""The review App's GitHub side, made from the founder's own computer.

The founder makes the App from the manifest page (`platform/deploy/review-app.html`), which
GitHub answers with a one-time `code` (valid one hour) in the address bar. Whoever turns that
code in first gets the App's private key, so it never goes into a workflow, a log or this
public repo: the founder's own Claude session runs this on their computer, with their own
`gh` login, the same hour:

    python3 scripts/review_app_setup.py --code <code, or the whole address-bar URL>

It makes the `review` environment main-only when absent (and refuses one that takes any
other ref), stops if `REVIEW_APP_PRIVATE_KEY` is already there, turns the code in
(`POST /app-manifests/{code}/conversions`, which takes no token), checks the App is the
organization's, then stores its private key as the `review` secret REVIEW_APP_PRIVATE_KEY
(`gh secret set`, on stdin) and its id as the repository variable REVIEW_APP_ID. It never
prints the key; it prints names, statuses and the App's install link.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

REPO = "TinyAssets/TinyAssets"
OWNER = "TinyAssets"
REVIEW = "review"
KEY = "REVIEW_APP_PRIVATE_KEY"
APP_ID = "REVIEW_APP_ID"
CONVERSIONS = "https://api.github.com/app-manifests/{code}/conversions"
MAIN_ONLY = {"protected_branches": False, "custom_branch_policies": True}

Run = Callable[..., subprocess.CompletedProcess]


class SetupError(RuntimeError):
    """A name and what went wrong, never a value."""


def code_of(text: str) -> str:
    """The manifest code, given bare or as the URL GitHub redirected to."""
    text = text.strip()
    if "?" in text:
        text = (urllib.parse.parse_qs(urllib.parse.urlsplit(text).query).get("code") or [""])[0]
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", text):
        raise SetupError("that is not a manifest code")
    return text


def convert(code: str) -> tuple[int, object]:
    """GitHub's manifest flow, step 3, with no token (its REST docs list no token type)."""
    request = urllib.request.Request(CONVERSIONS.format(code=code), data=b"", method="POST",
                                     headers={"Accept": "application/vnd.github+json",
                                              "User-Agent": "tinyassets-review-app-setup"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        return error.code, None


class Gh:
    """The founder's own `gh`: REST calls with their login, and `gh secret set`."""

    def __init__(self, repo: str = REPO, run: Run = subprocess.run) -> None:
        self.repo, self._run = repo, run

    def _gh(self, args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
        return self._run(["gh", *args], input=stdin, capture_output=True, text=True,
                         check=False)

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

    def secret_names(self, env: str) -> set[str]:
        done = self._gh(["secret", "list", "--env", env, "--repo", self.repo, "--json", "name"])
        if done.returncode != 0:
            raise SetupError(f"listing the {env} environment's secrets failed")
        return {row["name"] for row in json.loads(done.stdout or "[]")}

    def set_secret(self, name: str, value: str, env: str) -> None:
        done = self._gh(["secret", "set", name, "--env", env, "--repo", self.repo], value)
        if done.returncode != 0:
            raise SetupError(f"storing {name} failed (exit {done.returncode})")

    def set_variable(self, name: str, value: str) -> None:
        done = self._gh(["variable", "set", name, "--body", value, "--repo", self.repo])
        if done.returncode != 0:
            raise SetupError(f"setting the variable {name} failed (exit {done.returncode})")


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


def review_app(code: str, gh: Gh, turn_in: Callable[[str], tuple[int, object]] = convert,
               say: Callable[[str], None] = print) -> None:
    say(f"{REVIEW} environment: {ensure_environment(gh)}")
    if KEY in gh.secret_names(REVIEW):
        raise SetupError(f"{KEY} is already set; the code was not used: delete the App it "
                         "made (organization settings, GitHub Apps)")
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
        gh.set_secret(KEY, pem, REVIEW)
    except SetupError as error:
        raise SetupError(f"{error}; the App {slug} exists: generate a private key in its "
                         "settings and store it with gh secret set") from None
    say(f"{KEY}: set ({REVIEW})")
    gh.set_variable(APP_ID, str(int(app["id"])))
    say(f"{APP_ID}: set")
    say(f"App {slug}: made; install it on {REPO} alone: "
        f"https://github.com/apps/{slug}/installations/new")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--code", required=True,
                        help="the code, or the whole URL GitHub redirected to")
    args = parser.parse_args(argv)
    try:
        review_app(code_of(args.code), Gh())
    except SetupError as error:
        print(f"review_app_setup: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
