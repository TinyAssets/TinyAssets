#!/usr/bin/env python3
"""Catch a queued or armed PR that went stale against main BEFORE it poisons a group.

Measured over the 24 hours to 2026-10-02: 241 merge-group Tests runs for 76
merges, 99 of them failed. A large share were semantic conflicts. The PR was
green on its own head, but main had since merged something it collides with
(a shrink-only pin list against a merged deletion, a workspace-mask test
against newly added provider views). Its group failed, it was ejected, and
every group cumulatively built behind it was rebuilt, costing 20-30 minutes a
cycle.

When main moves, the first few queued or armed PRs (queue order) are checked:

1. **Textual conflict.** A merge onto new main leaves unmerged paths.
2. **Semantic conflict.** Only the tests that both sides can affect run on the
   merged tree: the PR's selection (scripts/affected_tests.py over its diff)
   intersected with main's selection (over what main changed since the PR's
   merge base). A failure counts only if it fails again on a re-run AND passes
   on main alone, so a flake or a test already broken on main never costs a
   builder a rebase. A huge intersection is left to the queue.

Three commands, three trust levels (.github/workflows/queue-freshness.yml):

    list   trusted: picks the candidates; its output is the matrix. Only a
           head with a valid Drain-Review receipt NOW (has_receipt: the same
           drain_review_gate.py decision pr-scope-guard makes) is a candidate,
           i.e. a reviewer stamped this exact code. Codex (#4293 round 2)
           showed a probe job cannot be a hard boundary: PR code runs as the
           runner user and can rewrite the upload action that follows it. So
           the boundary is "only reviewed code runs here"; the split below is
           defence in depth.
    probe  runs ONE PR's code with no GitHub token in its environment and
           writes a verdict about that PR only. The workflow names its artifact
           from the trusted matrix, and `act` ignores any PR number inside it.
    act    trusted: never runs PR code. Re-derives a textual conflict itself
           (`git merge-tree`), takes a semantic verdict only from that PR's own
           artifact, acts only while main and the head are both unchanged, and
           re-reads the PR right before each mutation.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

MAX_SHARED_TESTS = 60
# Probes per push to main, in queue order. The PRs about to enter a group come
# first; anything further back is checked on a later push.
MAX_PROBES = 6
FIRST_RUN_TIMEOUT = 900
CONFIRM_TIMEOUT = 300
LABEL = "stale-vs-main"
MARKER = "<!-- queue-freshness:{head}:{main} -->"
GH_TIMEOUT = 60
STALE = ("conflict", "semantic-conflict")
# Never handed to PR code (Codex on #4293: the conftest import inherited them).
_SECRET_ENV = (
    "GH_TOKEN", "GITHUB_TOKEN", "ACTIONS_RUNTIME_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
)

_CANDIDATES_Q = """query($owner:String!,$name:String!,$after:String){
repository(owner:$owner,name:$name){
mergeQueue(branch:"main"){entries(first:100){nodes{pullRequest{number}}}}
pullRequests(states:OPEN,first:100,after:$after){pageInfo{hasNextPage endCursor}
nodes{id number isDraft headRefOid baseRefOid baseRefName author{login}
autoMergeRequest{enabledAt}}}}}"""

_ONE_Q = """query($owner:String!,$name:String!,$n:Int!){repository(owner:$owner,name:$name){
pullRequest(number:$n){id state isDraft headRefOid baseRefName isInMergeQueue
author{login} autoMergeRequest{enabledAt}}}}"""


# ---- the decision (pure) -----------------------------------------------------


def probe_set(pr_selection: list[str] | None, main_selection: list[str] | None,
              cap: int = MAX_SHARED_TESTS) -> tuple[list[str] | None, str]:
    """(test files to run on the merged tree, why). ``None`` means don't run."""
    if pr_selection is None and main_selection is None:
        return None, "both sides select the whole suite; left to the queue"
    if pr_selection is None:
        candidates = list(main_selection or [])
    elif main_selection is None:
        candidates = list(pr_selection)
    else:
        candidates = sorted(set(pr_selection) & set(main_selection))
    if not candidates:
        return [], "no test is reachable from both sides"
    if len(candidates) > cap:
        return None, f"{len(candidates)} shared test files exceed the cap {cap}; left to the queue"
    return sorted(candidates), f"{len(candidates)} shared test file(s)"


def blame(first_run: list[str], merged_again: set[str], main_alone: set[str]) -> list[str]:
    """Failures the PR is responsible for: fail twice merged, pass on main alone."""
    return sorted(t for t in first_run if t in merged_again and t not in main_alone)


def outcome(blamed: list[str], gate_ok: bool, why: str) -> dict:
    """The probe's verdict once the shared tests have run on the merged tree."""
    if blamed:
        return {"verdict": "semantic-conflict", "why": why, "failures": blamed}
    if not gate_ok:
        # The gate failed for a reason not pinned on this PR (a stale quarantine
        # entry, a flake, main already broken): never "fresh" (Codex, #4293 r2).
        return {"verdict": "unchecked",
                "why": f"{why}; the gate failed with nothing to blame on this PR"}
    return {"verdict": "fresh", "why": f"{why} pass on the merged tree"}


def actionable(trusted: dict, verdict: dict | None) -> dict | None:
    """The verdict ``act`` may use for the PR the trusted matrix named, else None.

    The PR number and head come from ``trusted``; the artifact only contributes
    its verdict, and only if it is about that same head.
    """
    if not verdict or verdict.get("head") != trusted["head"]:
        return None
    if verdict.get("verdict") not in STALE or not isinstance(verdict.get("why"), str):
        return None
    failures = verdict.get("failures", [])
    if not (isinstance(failures, list) and all(isinstance(f, str) for f in failures)):
        return None
    if verdict["verdict"] == "semantic-conflict" and not failures:
        return None
    return {"number": trusted["number"], "head": trusted["head"],
            "verdict": verdict["verdict"], "why": verdict["why"][:500],
            "failures": failures}


def render_comment(verdict: dict, main: str) -> str:
    # No @-mention: every PR here is opened from the founder's account, so a
    # mention pages him all night. Builders watch their own PRs and the label.
    lines = [
        MARKER.format(head=verdict["head"], main=main),
        "### Stale against main: taken out of the queue before it reached a group",
        "",
        f"Main moved to `{main[:12]}`, and this head (`{verdict['head'][:12]}`) no "
        "longer merges cleanly with it, so it was taken out of the merge queue and "
        "auto-merge was disabled (scripts/queue_freshness.py). Merge or rebase onto "
        "main and push; a `Drain-Review-Diff:` receipt survives that if the change "
        "itself is unchanged.",
        "",
        f"**{verdict['verdict']}**: {verdict['why']}",
    ]
    if verdict.get("failures"):
        lines += ["", "Failing on the merged tree (twice), passing on main alone:"]
        lines += [f"- `{t}`" for t in verdict["failures"][:30]]
    return "\n".join(lines) + "\n"


# ---- I/O ---------------------------------------------------------------------


def _untrusted_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in _SECRET_ENV}


def _run(*cmd: str, cwd: Path | None = None, check: bool = True,
         timeout: int | None = None, untrusted: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd or REPO_ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=check, timeout=timeout,
                          env=_untrusted_env() if untrusted else None)


def _gh(*args: str) -> str:
    return _run("gh", *args, timeout=GH_TIMEOUT).stdout


def _graphql(query: str, **variables: object) -> dict:
    args = ["api", "graphql", "-f", f"query={query}"]
    for key, value in variables.items():
        if value is not None:
            args += ["-F", f"{key}={value}"]
    return json.loads(_gh(*args))["data"]


def candidates(repo: str) -> list[dict]:
    """Open, non-draft PRs into main that are queued or armed: queue order, then armed."""
    owner, name = repo.split("/", 1)
    order: list[int] = []
    prs: list[dict] = []
    after = None
    while True:
        data = _graphql(_CANDIDATES_Q, owner=owner, name=name, after=after)["repository"]
        if not order:
            order = [n["pullRequest"]["number"] for n in data["mergeQueue"]["entries"]["nodes"]]
        page = data["pullRequests"]
        prs += page["nodes"]
        if not page["pageInfo"]["hasNextPage"]:
            break
        after = page["pageInfo"]["endCursor"]
    queued = set(order)
    out = []
    for pr in prs:
        if pr["isDraft"] or pr["baseRefName"] != "main":
            continue
        if pr["number"] in queued or pr["autoMergeRequest"]:
            out.append({"number": pr["number"], "head": pr["headRefOid"],
                        "base": pr["baseRefOid"],
                        "author": (pr.get("author") or {}).get("login", "")})
    return sorted(out, key=lambda p: order.index(p["number"]) if p["number"] in queued
                  else len(order))


def has_receipt(repo: str, number: int, head: str, base: str) -> bool:
    """True if this PR carries a valid Drain-Review receipt for ``head`` NOW.

    The same decision pr-scope-guard makes (scripts/drain_review_gate.py
    --blocking-review over the body and trusted comments), run here on
    default-branch code. A passing check NAME is not enough: a head can keep a
    success from before #4255 made receipts universal (Codex on #4293 r3).
    Any failure to decide is "no receipt".
    """
    with tempfile.TemporaryDirectory(prefix="ta-receipt-") as tmp:
        try:
            body = json.loads(_gh("api", f"repos/{repo}/pulls/{number}", "--jq", "{b: .body}"))
            (Path(tmp) / "body.md").write_text(body.get("b") or "", encoding="utf-8")
            comments = Path(tmp) / "comments.ndjson"
            with comments.open("w", encoding="utf-8") as fh:
                for endpoint in (f"issues/{number}/comments", f"pulls/{number}/comments",
                                 f"pulls/{number}/reviews"):
                    fh.write(_gh("api", "--paginate", f"repos/{repo}/{endpoint}", "--jq",
                                 ".[] | {url: .html_url, association: .author_association,"
                                 " body: (.body // \"\")}"))
            _run("git", "fetch", "-q", "--no-tags", "--filter=blob:none", "origin",
                 base, f"+refs/pull/{number}/head")
            key = _run(sys.executable, "scripts/drain_review_gate.py", "--print-diff-key",
                       base, head, check=False).stdout.strip()
            decision = _run(
                sys.executable, "scripts/drain_review_gate.py", "--blocking-review",
                "--head", head, "--diff-key", key, "--body-file", str(Path(tmp) / "body.md"),
                "--review-repo", repo, "--review-pr", str(number),
                "--review-comments-file", str(comments), check=False, timeout=GH_TIMEOUT)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError):
            return False
    return decision.returncode == 0


def _selection(files: list[str], root: Path) -> list[str] | None:
    # affected_tests imports the merged tree's conftests: PR code. The probe job
    # carries no token, and _SECRET_ENV is dropped here as well.
    import affected_tests

    saved = {k: os.environ.pop(k) for k in _SECRET_ENV if k in os.environ}
    try:
        selected, _ = affected_tests.select(files, root)
    except RuntimeError:
        return None
    finally:
        os.environ.update(saved)
    return selected


def _junit_failures(junit: Path, asked: list[str] | None = None) -> set[str] | None:
    """Failing node ids, or None if the report is missing or unreadable.

    With ``asked``, an asked-for id that did not run counts as failing.
    """
    import ci_required_tests as gate

    if not junit.exists():
        return None
    try:
        failing, ran = gate.collect_outcomes(junit)
    except Exception:  # noqa: BLE001 - a broken report is "unchecked", never "fresh"
        return None
    if asked is None:
        return failing
    return (failing | (set(asked) - ran)) & set(asked)


def _still_failing(tree: Path, node_ids: list[str], tmp: str, tag: str) -> set[str]:
    """Re-run just these node ids in ``tree``; the ones that fail again."""
    junit = Path(tmp) / f"confirm-{tag}.xml"
    # Same junit family as the gate run: under the default xunit2 the report
    # loses `file` and every id reads as "did not run" (Codex on #4293).
    try:
        _run(sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
             "-o", "junit_family=xunit1", f"--basetemp={tmp}/c-{tag}",
             f"--junitxml={junit}", *node_ids,
             cwd=tree, check=False, timeout=CONFIRM_TIMEOUT, untrusted=True)
    except subprocess.TimeoutExpired:
        return set(node_ids)
    failing = _junit_failures(junit, node_ids)
    return set(node_ids) if failing is None else failing


def probe_one(number: int, head: str, main: str) -> dict:
    verdict = {"number": number, "head": head}
    _run("git", "fetch", "-q", "--no-tags", "origin", f"+refs/pull/{number}/head")
    if _run("git", "rev-parse", "FETCH_HEAD").stdout.strip() != head:
        return {**verdict, "verdict": "unchecked", "why": "the head moved since it was listed"}
    merge_base = _run("git", "merge-base", head, main).stdout.strip()
    if merge_base == main:
        return {**verdict, "verdict": "fresh", "why": "already based on current main"}
    main_delta = _run("git", "diff", "--name-only", merge_base, main).stdout.split()
    pr_files = _run("git", "diff", "--name-only", merge_base, head).stdout.split()
    with tempfile.TemporaryDirectory(prefix="ta-fresh-") as tmp:
        tree = Path(tmp) / "w"
        _run("git", "worktree", "add", "-q", "--detach", str(tree), main)
        try:
            merged = _run("git", "-c", "user.name=freshness", "-c", "user.email=f@localhost",
                          "merge", "-q", "--no-edit", head, cwd=tree, check=False)
            if merged.returncode != 0:
                unmerged = _run("git", "diff", "--name-only", "--diff-filter=U",
                                cwd=tree).stdout.split()
                if unmerged:
                    return {**verdict, "verdict": "conflict",
                            "why": f"merging onto main conflicts in {len(unmerged)} file(s)"}
                return {**verdict, "verdict": "unchecked", "why": "the local merge failed"}
            run_files, why = probe_set(_selection(pr_files, tree), _selection(main_delta, tree))
            if not run_files:
                state = "fresh" if run_files == [] else "unchecked"
                return {**verdict, "verdict": state, "why": why}
            selection = Path(tmp) / "affected.txt"
            selection.write_text("".join(f"{f}\n" for f in run_files), encoding="utf-8")
            junit = Path(tmp) / "junit.xml"
            try:
                gate_run = _run(
                    sys.executable, "scripts/ci_required_tests.py", "--junit", str(junit),
                    "--exclude-from", ".github/heavy-test-files.txt",
                    "--affected", str(selection), "--profile", "affected",
                    "--pytest-arg", f"--basetemp={tmp}/b",
                    cwd=tree, check=False, timeout=FIRST_RUN_TIMEOUT, untrusted=True)
            except subprocess.TimeoutExpired:
                return {**verdict, "verdict": "unchecked", "why": f"{why}; the run timed out"}
            failing = _junit_failures(junit)
            if failing is None:
                return {**verdict, "verdict": "unchecked", "why": f"{why}; no readable report"}
            import ci_required_tests as gate

            tolerated, flaky, _ = gate.parse_quarantine(tree / ".github/known-failing-tests.txt")
            failures = sorted(failing - tolerated - flaky)
            if failures:
                again = _still_failing(tree, failures, tmp, "merged")
                _run("git", "checkout", "-q", "--detach", main, cwd=tree)
                failures = blame(failures, again, _still_failing(tree, failures, tmp, "main"))
            return {**verdict, **outcome(failures, gate_run.returncode == 0, why)}
        finally:
            _run("git", "worktree", "remove", "--force", str(tree), check=False)


def _live(repo: str, number: int) -> dict:
    owner, name = repo.split("/", 1)
    return _graphql(_ONE_Q, owner=owner, name=name, n=number)["repository"]["pullRequest"]


def _main_sha(repo: str) -> str:
    return _gh("api", f"repos/{repo}/commits/main", "--jq", ".sha").strip()


def _really_conflicts(head: str, main: str, number: int) -> bool:
    """Trusted re-derivation of a textual conflict: no PR code runs."""
    _run("git", "fetch", "-q", "--no-tags", "origin", main, f"+refs/pull/{number}/head")
    out = _run("git", "merge-tree", "--write-tree", main, head, check=False)
    return out.returncode == 1


def _read_verdict(path: Path) -> dict | None:
    """One artifact's verdict, or None if missing or malformed; never fatal to the rest."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def _unchanged(pr: dict, head: str) -> bool:
    return (pr["state"] == "OPEN" and not pr["isDraft"] and pr["baseRefName"] == "main"
            and pr["headRefOid"] == head)


def act(repo: str, main: str, matrix: list[dict], verdict_dir: Path) -> int:
    # A textual conflict is re-derived against main as it is NOW. A semantic
    # verdict holds only for the main it was probed on, so a moved main drops
    # it; the run already pending for the new main probes again. Main and the
    # PR are re-read per candidate and before each mutation (Codex on #4293).
    for trusted in matrix:
        n = trusted["number"]
        raw = _read_verdict(verdict_dir / f"verdict-{n}" / "verdict.json")
        v = actionable(trusted, raw)
        if v is None:
            print(f"#{n}: {(raw or {}).get('verdict', 'no usable verdict')}; nothing to do.")
            continue
        current = _main_sha(repo)
        if v["verdict"] == "semantic-conflict" and current != main:
            print(f"#{n}: semantic verdict was for {main[:12]}; main moved, leaving it.")
            continue
        if v["verdict"] == "conflict" and not _really_conflicts(v["head"], current, n):
            print(f"#{n}: no textual conflict with current main {current[:12]}; leaving it.")
            continue
        before = _live(repo, n)
        if not (_unchanged(before, v["head"])
                and (before["isInMergeQueue"] or before["autoMergeRequest"])):
            print(f"#{n}: changed since the probe (head, state or queue); leaving it.")
            continue
        if not _remove(repo, n, v["head"], current):
            continue
        after = _live(repo, n)
        if after["headRefOid"] != v["head"]:
            print(f"#{n}: head moved during removal; not commenting.")
            continue
        if after["isInMergeQueue"] or after["autoMergeRequest"]:
            print(f"::warning::#{n}: still queued or armed after removal; not commenting.")
            continue
        marker = MARKER.format(head=v["head"], main=current)
        comments = _gh("api", "--paginate", f"repos/{repo}/issues/{n}/comments", "--jq", ".[].body")
        if marker not in comments:
            _gh("pr", "comment", str(n), "-R", repo, "--body", render_comment(v, current))
        _gh("label", "create", LABEL, "-R", repo, "--force", "--color", "FBCA04",
            "--description", "Stale against current main; rebase before re-queueing")
        _gh("pr", "edit", str(n), "-R", repo, "--add-label", LABEL)
        print(f"#{n}: {v['verdict']}; removed from the queue, disarmed, labelled.")
    return 0


def _remove(repo: str, number: int, head: str, main: str) -> bool:
    """Dequeue, then disarm, re-reading main and the PR before each.

    False as soon as either moved: the verdict was for that pair, and the run
    pending for the new main decides again (Codex on #4293 r3).
    """
    for field, mutation, key in (
        ("isInMergeQueue", "dequeuePullRequest", "id"),
        ("autoMergeRequest", "disablePullRequestAutoMerge", "pullRequestId"),
    ):
        if _main_sha(repo) != main:
            print(f"#{number}: main moved before {mutation}; leaving it.")
            return False
        pr = _live(repo, number)
        if not _unchanged(pr, head):
            print(f"#{number}: changed before {mutation} (head, state or base); leaving it.")
            return False
        if not pr[field]:
            continue
        try:
            _graphql(f"mutation($id:ID!){{{mutation}(input:{{{key}:$id}}){{clientMutationId}}}}",
                     id=pr["id"])
        except subprocess.CalledProcessError as exc:
            print(f"::warning::#{number}: {mutation} refused: {exc.stderr.strip()[:200]}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)
    ls = sub.add_parser("list")
    ls.add_argument("--repo", required=True)
    pr = sub.add_parser("probe")
    pr.add_argument("--number", type=int, required=True)
    pr.add_argument("--head", required=True)
    pr.add_argument("--main", required=True)
    pr.add_argument("--out", type=Path, required=True)
    ac = sub.add_parser("act")
    ac.add_argument("--repo", required=True)
    ac.add_argument("--main", required=True)
    ac.add_argument("--matrix", required=True, help="the list job's JSON output")
    ac.add_argument("--verdict-dir", type=Path, required=True)
    args = ap.parse_args()
    os.environ.setdefault("GIT_TERMINAL_PROMPT", "0")
    if args.command == "list":
        picked = []
        for cand in candidates(args.repo):
            if len(picked) == MAX_PROBES:
                break
            if has_receipt(args.repo, cand["number"], cand["head"], cand["base"]):
                picked.append(cand)
        print(json.dumps(picked))
        return 0
    if args.command == "probe":
        try:
            verdict = probe_one(args.number, args.head, args.main)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            verdict = {"number": args.number, "head": args.head, "verdict": "unchecked",
                       "why": f"probe failed: {type(exc).__name__}"}
        print(f"#{args.number}: {verdict['verdict']} - {verdict['why']}")
        args.out.write_text(json.dumps(verdict, indent=1), encoding="utf-8")
        return 0
    return act(args.repo, args.main, json.loads(args.matrix), args.verdict_dir)


if __name__ == "__main__":
    raise SystemExit(main())
