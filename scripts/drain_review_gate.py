#!/usr/bin/env python3
"""Decide whether a pull request carries a current review receipt.

EVERY pull request needs one (2026-10-02). Two callers, one receipt format:

* `auto-enroll-merge.yml` arms auto-merge only for a head whose body carries a
  receipt (a pre-filter, so unreviewed heads never reach the queue);
* `pr-scope-guard.yml` -- the REQUIRED check -- asks, via `--blocking-review`,
  for the full receipt: the body's `Drain-Review-Verdict: APPROVE` lines, bound
  to this head, to an earlier head of the same tree-diff, or to the diff key,
  citing a comment on THIS PR by a trusted author that itself opens with the
  same approval.

Why every PR: until 2026-10-02 only drain/ branches and a narrow set of
gate-defining and authority paths needed a receipt, and on 2026-10-01
auto-enroll armed #4247 -- a superseded head with a known data-loss bug, no
receipt, and no path in that set -- and the merge queue admitted it. Before
2026-09-26 the verdict was only a PR comment, and PR #3989 auto-merged at the
exact head its reviewer had BLOCKED.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_DIFF_KEY_RE = re.compile(r"^[0-9a-f]{64}$")
# One `git diff --raw` record header with FULL object ids (SHA-1 or SHA-256).
_RAW_META_RE = re.compile(
    rb":[0-7]{6} [0-7]{6} ([0-9a-f]{40}|[0-9a-f]{64}) ([0-9a-f]{40}|[0-9a-f]{64}) [A-Z][0-9]*"
)
_ARTIFACT_RE = re.compile(
    r"Drain-Review-Artifact: "
    r"(docs/[A-Za-z0-9_./-]+\.md|https://github\.com/\S+)"
)

# A comment ON THIS PR, by URL. The three anchors are the three places a review
# verdict can live on a pull request: a top-level issue comment, a submitted
# review, and an inline review comment. Anything else — a docs path, a link to
# another PR, another repository, a bare commit URL — is not the durable,
# timestamped artifact this receipt is supposed to point at.
_COMMENT_ARTIFACT_RE = re.compile(
    r"Drain-Review-Artifact: (?P<url>https://github\.com/"
    r"(?P<repo>[A-Za-z0-9._-]+/[A-Za-z0-9._-]+)/pull/(?P<pr>[1-9][0-9]*)"
    r"#(?:issuecomment-[0-9]+|pullrequestreview-[0-9]+|discussion_r[0-9]+))"
)

# `author_association` as GitHub computes it at read time — trusted metadata,
# not something a comment body can claim. CONTRIBUTOR and NONE are excluded:
# anyone can comment on a public repo's PR, and the receipt must not be
# satisfiable by a drive-by comment.
_TRUSTED_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})


def review_allows_merge(
    *,
    head: str,
    body: str,
    artifact_must_be_comment_on: tuple[str, int] | None = None,
    trusted_comment_urls: frozenset[str] | None = None,
    diff_key: str | None = None,
    same_change: Callable[[str], bool] | None = None,
) -> bool:
    """True when the body carries a receipt for this head (or its diff key).

    With `artifact_must_be_comment_on=(repo, pr)` the receipt's artifact must
    additionally name a comment on THAT pull request, present in
    `trusted_comment_urls`. A missing inventory (the API read failed) denies:
    a receipt we cannot corroborate is not a receipt.
    """
    if not _SHA_RE.fullmatch(head):
        return False

    lines = leading_lines(body, 3)
    if not (_attests_approval(lines, head, diff_key, same_change=same_change) and len(lines) == 3):
        return False
    artifact = lines[2]
    if _ARTIFACT_RE.fullmatch(artifact) is None:
        return False
    if artifact_must_be_comment_on is None:
        return True
    repo, pr = artifact_must_be_comment_on
    return artifact_names_trusted_comment(
        artifact, repo=repo, pr=pr, trusted_comment_urls=trusted_comment_urls
    )


def leading_lines(text: str, count: int) -> list[str]:
    """The first `count` non-blank lines, trailing whitespace removed.

    **A receipt is only read at the TOP of the text**, and that is the whole
    anti-hiding rule. Nothing can precede the first line of a document, so no
    construct can be open when it is read: an HTML comment, a fence, a
    `<details>`, a blockquote or a list all have to START somewhere, and if one
    does, the first non-blank line is its opener and not the verdict.

    This replaced a markdown scanner, and the reason is worth keeping. Three
    cross-family review rounds each found defects in that scanner, in BOTH
    directions -- approvals hidden in a nested `<details>`, in an HTML comment, in
    a lazily-continued blockquote, in a list-nested quote; and honest receipts
    wrongly refused after a heading, after a fence marker inside an HTML block,
    after a literal `<!--` in a code example. Each fix created the next round's
    findings, which `AGENTS.md` names as a loop rather than progress, and says to
    answer with a redesign: recurring findings in one area mean the shape is
    wrong. Modelling GitHub's renderer was the wrong shape. A position that
    cannot have anything in front of it needs no renderer.

    Trailing whitespace is stripped because an editor adding a space must not
    void a receipt, and trailing whitespace can hide nothing. Leading blank
    lines are skipped for the same reason.
    """
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        lines.append(line)
        if len(lines) == count:
            break
    return lines


def binding_head(text: str) -> str | None:
    """The commit a receipt's second line names, or None if it names a diff key."""
    lines = leading_lines(text, 2)
    if len(lines) < 2:
        return None
    match = re.fullmatch(r"Drain-Review-Head: ([0-9a-f]{40})", lines[1])
    return match.group(1) if match else None


def _attests_approval(
    lines: list[str],
    head: str,
    diff_key: str | None = None,
    *,
    same_change: Callable[[str], bool] | None = None,
) -> bool:
    """Do these leading lines OPEN with an approval of `head`, or of its diff?

    ONE definition of what an approval looks like, used for the PR body and for
    the cited comment alike. Exact string equality on the first two non-blank
    lines, in order, so `BLOCK`, a lower-case verdict, trailing prose, or a head
    line for any other commit all refuse.

    The second line binds the approval to what was reviewed, in one of three ways:

    * `Drain-Review-Head: <sha>` — this exact commit.
    * `Drain-Review-Head: <other sha>` — an earlier commit of the SAME change.
      Honoured only when `same_change(other)` is true, which the caller sets by
      comparing tree-diff hashes (`diff_key`). A rebase or merge-main that
      leaves every path's before/after blob unchanged carries the receipt.
      Any real edit, including a conflict resolution that changes bytes, does
      not. A missing commit is not the same change.
    * `Drain-Review-Diff: <key>` — this exact CHANGE, as `diff_key` computes it
      for the current head. Only honoured when the caller computed a key.
    """
    if len(lines) < 2 or lines[0] != "Drain-Review-Verdict: APPROVE":
        return False
    if lines[1] == f"Drain-Review-Head: {head}":
        return True
    if (
        diff_key is not None
        and _DIFF_KEY_RE.fullmatch(diff_key)
        and lines[1] == f"Drain-Review-Diff: {diff_key}"
    ):
        return True
    cited = binding_head("\n".join(lines))
    if (
        cited
        and cited != head
        and diff_key is not None
        and _DIFF_KEY_RE.fullmatch(diff_key)
        and same_change is not None
    ):
        return bool(same_change(cited))
    return False


def comment_attests_approval(
    comment_body: str,
    head: str,
    diff_key: str | None = None,
    *,
    same_change: Callable[[str], bool] | None = None,
) -> bool:
    """Does this comment OPEN by publishing an approval of `head`?

    Comment identity was not enough. Cross-family review 2026-09-26, finding 6:
    the gate accepted any trusted-author comment as the artifact, so a body
    receipt citing an OWNER comment that said `VERDICT: BLOCK` for this exact
    head passed. Checking the comment's own attestation is what makes the
    artifact evidence rather than a bookmark — and it closes the stale-comment
    gap too (finding 7), because the comment must approve this same change
    (this head, or an earlier commit with the same tree-diff), which needs no
    clock.
    """
    return _attests_approval(
        leading_lines(comment_body, 2), head, diff_key, same_change=same_change
    )


def artifact_names_trusted_comment(
    artifact_line: str,
    *,
    repo: str,
    pr: int,
    trusted_comment_urls: frozenset[str] | None,
) -> bool:
    """Does this artifact line name a trusted comment on THIS pull request?

    Three independent conditions, all required:

    * the URL is shaped like a comment anchor on `repo`'s PR `pr` — not a docs
      path, not another PR, not another repository;
    * that exact URL is in the inventory read from the API, so the comment
      actually EXISTS (a receipt can otherwise cite an invented comment id);
    * the inventory only ever contains comments whose `author_association` is
      trusted, so a drive-by commenter cannot supply the artifact.

    Fails closed when the inventory is unavailable.
    """
    if trusted_comment_urls is None:
        return False
    match = _COMMENT_ARTIFACT_RE.fullmatch(artifact_line)
    if match is None:
        return False
    # GitHub resolves owner/repo case-insensitively, so a stamper who types a
    # different casing must not be refused; the PR number is compared as the
    # canonical decimal string the regex already constrained.
    if match["repo"].lower() != repo.lower() or match["pr"] != str(pr):
        return False
    return match["url"].lower() in trusted_comment_urls


def published_approval_urls(
    stream: str,
    *,
    head: str,
    diff_key: str | None = None,
    same_change: Callable[[str], bool] | None = None,
) -> frozenset[str] | None:
    """URLs of comments that PUBLISH a trusted approval of `head`.

    The workflow reads the PR's issue comments, reviews and review comments and
    appends each object to one file (`gh api --jq '.[] | {...}'` emits one
    compact object per line, with the body's newlines JSON-escaped). Both
    filters happen HERE, not in a jq expression, so they are unit tested rather
    than buried in a shell string:

    * `author_association` must be trusted — GitHub computes it at read time, so
      it is not something a comment body can claim about itself;
    * the comment must itself attest `APPROVE` of this change: this head, an
      earlier commit with the same tree-diff, or the diff key. A trusted
      author's comment saying `VERDICT: BLOCK` is not an approval, and before
      this filter existed the gate accepted one as the artifact.

    Returns `None` on anything unparseable — a partially understood inventory
    must deny, never silently shrink to a set that a receipt cannot match and
    also never grow past what was actually read.
    """
    decoder = json.JSONDecoder()
    urls: set[str] = set()
    index = 0
    length = len(stream)
    while index < length:
        while index < length and stream[index].isspace():
            index += 1
        if index >= length:
            break
        try:
            obj, end = decoder.raw_decode(stream, index)
        except ValueError:
            return None
        index = end
        if not isinstance(obj, dict):
            return None
        url = obj.get("url")
        association = obj.get("association")
        body = obj.get("body")
        if not isinstance(url, str) or not isinstance(association, str):
            return None
        if not isinstance(body, str):
            # The projection uses `(.body // "")`, so an absent body means the
            # inventory is not the shape this gate reads. Refuse it.
            return None
        if association in _TRUSTED_ASSOCIATIONS and comment_attests_approval(
            body, head, diff_key, same_change=same_change
        ):
            urls.add(url.lower())
    return frozenset(urls)


def diff_key(base: str, head: str, *, cwd: Path | None = None) -> str:
    """The identity of the CHANGE a PR makes, independent of its commit history.

    sha256 over every path in `git diff merge-base(base, head)..head`, each
    with both modes and both FULL blob ids (`--raw --no-abbrev`, no rename
    detection). Blob ids are content hashes, so this pins exactly which bytes
    the PR replaces and with what, including binary files, and nothing else:

    * merge main in, or rebase, where main did not touch the PR's files: the
      merge base moves, but every path's before/after blob is unchanged -> SAME
      key;
    * any edit to the PR's own change, or a catch-up where main DID touch a
      file the PR changes (the "before" blob or the merged "after" blob moves)
      -> DIFFERENT key, so the reviewer re-reviews exactly when the reviewed
      change is no longer the change being merged.

    Deliberately not `git patch-id`: it hashes only textual hunk lines, so two
    different binary changes to the same path would share an id.

    Raises on any git failure; callers must treat that as "no key" (deny).
    """
    return diff_key_from_raw(_raw_diff(base, head, cwd=cwd))


def _raw_diff(base: str, head: str, *, cwd: Path | None = None) -> bytes:
    """`git diff --raw -z` from merge-base(base, head) to head, full ids."""
    def git(*args: str) -> bytes:
        return subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, check=True
        ).stdout

    merge_base = git("merge-base", base, head).strip().decode("ascii")
    # `--no-abbrev` is what makes the ids full: `--full-index` alone still
    # prints abbreviated ids in --raw output, and two different blobs can share
    # an abbreviation (cross-family review 2026-09-27 built such a pair).
    return git(
        "diff",
        "--raw",
        "--no-renames",
        "--full-index",
        "--no-abbrev",
        # A gitlink change must never vanish from the key: without this, a
        # `.gitmodules` entry with `ignore = all` hides submodule edits.
        "--ignore-submodules=none",
        "-z",
        merge_base,
        head,
    )


def _raw_records(raw: bytes) -> list[tuple[bytes, bytes]]:
    """(path, meta) of `git diff --raw -z --no-abbrev`, sorted; refuse anything else."""
    fields = raw.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    if len(fields) % 2:
        raise ValueError("unexpected `git diff --raw -z` output")
    pairs = sorted(zip(fields[1::2], fields[0::2]))
    for _path, meta in pairs:
        if _RAW_META_RE.fullmatch(meta) is None:
            raise ValueError(f"unexpected `git diff --raw` record: {meta!r}")
    return pairs


def diff_key_from_raw(raw: bytes, *, keep: Callable[[bytes], bool] | None = None) -> str:
    """Hash `git diff --raw -z --no-abbrev` output, or only its `keep` paths."""
    digest = hashlib.sha256()
    for path, meta in _raw_records(raw):
        if keep is not None and not keep(path):
            continue
        # Length-prefixed, never delimiter-joined: a path may contain a tab or
        # a newline, and joining would let one path spell two entries.
        for part in (path, meta):
            digest.update(len(part).to_bytes(8, "big"))
            digest.update(part)
    return digest.hexdigest()


_REGULAR_BLOB_MODES = frozenset({"100644", "100755"})


def _read_text(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


# Test modules pytest collects. `.dockerignore` keeps tests/ out of the
# production image. conftest.py and helpers run under every test, so they are
# never test modules here.
_TEST_MODULE_RE = re.compile(rb"tests/(?:[^/]+/)*(?:test_[^/]*|[^/]*_test)\.py")
_REPO_ROOT = Path(__file__).resolve().parents[1]
_FLOOR_TITLE_RE = re.compile(r"\s*\[floor\]", re.I)


def is_floor_class(title: str | None, labels: str | None) -> bool:
    """ADR-015 floor class: a `[floor]` title or a `floor` label. Unknown = floor."""
    if title is None:
        return True
    names = {n.strip().lower() for n in (labels or "").split(",")}
    return bool(_FLOOR_TITLE_RE.match(title)) or "floor" in names


def gate_test_patterns(root: Path = _REPO_ROOT) -> tuple[set[str], set[str]]:
    """(path globs, basenames) of test files a workflow or script names.

    Workflows' `paths:` entries and literal `tests/...py` names, and every
    `test_*.py` or `tests.<module>` a script names or imports: changing one
    changes a gate, so it never carries a review.
    """
    globs: set[str] = set()
    names: set[str] = set()
    workflows = sorted((root / ".github" / "workflows").glob("*.y*ml"))
    for path in [*workflows, *sorted((root / "scripts").rglob("*.py"))]:
        try:
            # A regex names `test_x\.py`; read it as the file it names.
            text = path.read_text(encoding="utf-8", errors="replace").replace("\\.", ".")
        except OSError:
            continue
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if path.suffix in (".yml", ".yaml") and re.fullmatch(r"\s*paths:\s*", line):
                for item in lines[i + 1 :]:
                    entry = re.fullmatch(r"\s*-\s*['\"]?([^'\"#]+?)['\"]?\s*", item)
                    if entry is None:
                        break
                    if entry.group(1).startswith("tests/"):
                        globs.add(entry.group(1))
        globs.update(re.findall(r"tests/[\w./-]*?\.py\b", text))
        names.update(re.findall(r"\b(?:test_\w+|\w+_test)\.py\b", text))
        for module in re.findall(r"\btests((?:\.\w+)+)", text):
            globs.add("tests" + module.replace(".", "/") + ".py")
    return globs, names


def is_gate_test(path: bytes, patterns: tuple[set[str], set[str]]) -> bool:
    text = path.decode("utf-8", "replace")
    globs, names = patterns
    return text.rsplit("/", 1)[-1] in names or any(fnmatch.fnmatchcase(text, g) for g in globs)


_BANNED_NAMES = frozenset({"setattr", "delattr", "exec", "eval", "globals", "vars", "__import__"})


_MUTATORS = frozenset(
    {"update", "setdefault", "pop", "popitem", "clear", "append", "extend", "insert",
     "remove", "discard", "add", "putenv", "unsetenv", "chdir", "umask"}
)


def _literal(node: ast.expr) -> bool:
    try:
        ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return False
    return True


def _root(node: ast.expr) -> ast.expr:
    while isinstance(node, (ast.Attribute, ast.Subscript, ast.Call)):
        node = node.func if isinstance(node, ast.Call) else node.value
    return node


def _inert_decorator(node: ast.expr) -> bool:
    while isinstance(node, (ast.Call, ast.Attribute)):
        if isinstance(node, ast.Call):
            if not all(_literal(a) for a in node.args) or not all(
                _literal(k.value) for k in node.keywords
            ):
                return False
            node = node.func
        else:
            node = node.value
    return isinstance(node, ast.Name) and node.id == "pytest"


def _inert_def(node: ast.stmt) -> bool:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        args = node.args
        defaults = [*args.defaults, *(d for d in args.kw_defaults if d is not None)]
        return all(_inert_decorator(d) for d in node.decorator_list) and all(
            _literal(d) for d in defaults
        )
    if isinstance(node, ast.ClassDef):
        return (
            not node.decorator_list
            and not node.bases
            and not node.keywords
            and all(_inert_body(s, in_class=True) for s in node.body)
        )
    return False


def _inert_body(node: ast.stmt, *, in_class: bool = False) -> bool:
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
        return True
    if isinstance(node, (ast.Import, ast.ImportFrom)) and not in_class:
        return True
    if isinstance(node, ast.Pass):
        return True
    return _inert_def(node)


def appendable_test(source: bytes) -> bool:
    """Only imports, docstrings, `pytest`-decorated defs and plain classes at
    module level, and no write to anything shared anywhere: a new module that
    collection runs cannot change what another test sees."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return False
    if not all(_inert_body(s) for s in tree.body):
        return False
    imported = {
        (a.asname or a.name).split(".")[0]
        for s in ast.walk(tree)
        if isinstance(s, (ast.Import, ast.ImportFrom))
        for a in s.names
    }
    if "*" in imported:
        return False
    for node in ast.walk(tree):
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            return False
        if isinstance(node, ast.Name) and node.id in _BANNED_NAMES:
            return False
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            return False
        shared = None
        if isinstance(node, (ast.Attribute, ast.Subscript)) and isinstance(
            node.ctx, (ast.Store, ast.Del)
        ):
            shared = _root(node.value)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _MUTATORS
        ):
            shared = _root(node.func.value)
        if shared is not None and (
            not isinstance(shared, ast.Name) or shared.id in imported
        ):
            return False
    return True


def carries_review(
    reviewed_raw: bytes,
    current_raw: bytes,
    *,
    read_blob: Callable[[str], bytes] | None = None,
    gate_patterns: tuple[set[str], set[str]] | None = None,
) -> bool:
    """Does a review of one change (`git diff --raw` bytes) cover another?

    Yes when they are identical, or, given `read_blob`, when the current change
    is the reviewed one plus ONLY appended test modules: each new against the
    base, untouched by the reviewed change, named by no workflow or script, and
    `appendable_test`. An edit to any existing test, or to a test the review
    saw, voids the receipt.
    """
    if diff_key_from_raw(reviewed_raw) == diff_key_from_raw(current_raw):
        return True
    if read_blob is None:
        return False
    patterns = gate_test_patterns() if gate_patterns is None else gate_patterns
    reviewed_paths = {p for p, _ in _raw_records(reviewed_raw)}
    appended: set[bytes] = set()
    for path, meta in _raw_records(current_raw):
        if path in reviewed_paths:
            continue
        fields = meta.split(b" ")
        if (
            fields[0] != b":000000"
            or fields[1] not in (b"100644", b"100755")
            or fields[-1] != b"A"
            or _TEST_MODULE_RE.fullmatch(path) is None
            or is_gate_test(path, patterns)
            or not appendable_test(read_blob(fields[3].decode("ascii")))
        ):
            return False
        appended.add(path)
    return diff_key_from_raw(reviewed_raw) == diff_key_from_raw(
        current_raw, keep=lambda p: p not in appended
    )


def same_change_as(
    base: str,
    current_key: str,
    *,
    head: str | None = None,
    cwd: Path | None = None,
    test_carry: bool = False,
) -> Callable[[str], bool]:
    """True when a review of `cited` still covers the current change.

    Without `head`: `cited` has the same tree-diff against `base` as
    `current_key`. With `head`, whose diff must hash to `current_key`, and
    `test_carry` (never for the floor class): also when the current change only
    appends test modules (`carries_review`).

    Any git failure is "not the same change". A receipt must not carry across
    a commit we cannot read.
    """

    def same(cited: str) -> bool:
        if not _SHA_RE.fullmatch(cited) or not _DIFF_KEY_RE.fullmatch(current_key):
            return False
        try:
            if head is None:
                return diff_key(base, cited, cwd=cwd) == current_key
            if not _SHA_RE.fullmatch(head):
                return False
            current_raw = _raw_diff(base, head, cwd=cwd)
            if diff_key_from_raw(current_raw) != current_key:
                return False
            def read_blob(sha: str) -> bytes:
                return subprocess.run(
                    ["git", "cat-file", "blob", sha], cwd=cwd, capture_output=True, check=True
                ).stdout

            return carries_review(
                _raw_diff(base, cited, cwd=cwd),
                current_raw,
                read_blob=read_blob if test_carry else None,
                gate_patterns=gate_test_patterns(cwd or _REPO_ROOT) if test_carry else None,
            )
        except (subprocess.CalledProcessError, ValueError, OSError):
            return False

    return same


def _carry_comparer(args: argparse.Namespace) -> Callable[[str], bool] | None:
    key = args.diff_key or ""
    base = getattr(args, "base", "") or ""
    if not base or not _DIFF_KEY_RE.fullmatch(key):
        return None
    return same_change_as(
        base,
        key,
        head=args.head,
        test_carry=not is_floor_class(args.title, args.labels),
    )


def _blocking_review(args: argparse.Namespace) -> int:
    """`--blocking-review`: does the body carry a corroborated receipt?"""
    print(
        f"every PR needs a Drain-Review receipt for head {args.head}"
        + (f" or diff key {args.diff_key}" if args.diff_key else ""),
        file=sys.stderr,
    )

    body = _read_text(args.body_file)
    comments = _read_text(args.review_comments_file)
    key = args.diff_key or None
    carry = _carry_comparer(args)
    trusted = (
        None
        if comments is None
        else published_approval_urls(
            comments, head=args.head, diff_key=key, same_change=carry
        )
    )
    if body is not None and review_allows_merge(
        head=args.head,
        body=body,
        artifact_must_be_comment_on=(args.review_repo, args.review_pr),
        trusted_comment_urls=trusted,
        diff_key=key,
        same_change=carry,
    ):
        print("allow")
        return 0
    print("deny")
    return 2


def _list_carry_heads(argv: list[str]) -> int:
    """Print head SHAs a receipt names, other than the current head.

    The workflow fetches these so an unchanged rebase can be compared. A
    missing or unreadable file prints nothing for that source. Garbage in the
    comment inventory prints the body's SHA only; the gate itself still denies
    a receipt it cannot corroborate.
    """
    parser = argparse.ArgumentParser(prog="drain_review_gate.py --list-carry-heads")
    parser.add_argument("--body-file", type=Path, required=True)
    parser.add_argument("--review-comments-file", type=Path)
    parser.add_argument("--head", default="")
    args = parser.parse_args(argv)
    shas: list[str] = []

    def add(text: str) -> None:
        cited = binding_head(text)
        if cited and cited != args.head and cited not in shas:
            shas.append(cited)

    add(_read_text(args.body_file) or "")
    comments = _read_text(args.review_comments_file)
    if comments:
        decoder = json.JSONDecoder()
        index = 0
        length = len(comments)
        while index < length:
            while index < length and comments[index].isspace():
                index += 1
            if index >= length:
                break
            try:
                obj, end = decoder.raw_decode(comments, index)
            except ValueError:
                break
            index = end
            body = obj.get("body") if isinstance(obj, dict) else None
            if isinstance(body, str):
                add(body)
    for sha in shas:
        print(sha)
    return 0


# A lane's declared files. A lane branched from another lane's WIP commit and
# carried a 689-line deploy refactor through five reviews (2026-10-21), because
# every reviewer read the lane's own commits, not its diff against main. A PR
# that touches a release-critical path must say which files it owns, and every
# PR that says so is held to it: a file outside the lane is a smuggled lineage
# or a second intent, and either way it is refused before review starts.
_LANE_RE = re.compile(r"^Lane:[ \t]*(\S.*)$", re.MULTILINE)


def declared_lane(body: str) -> list[str] | None:
    """The body's `Lane:` globs (`dir/` is a prefix), or None when undeclared."""
    match = _LANE_RE.search(body)
    if not match:
        return None
    globs = [g.strip("`") for g in re.split(r"[,\s]+", match.group(1))]
    return [g for g in globs if g] or None


def outside_lane(paths: list[str], globs: list[str]) -> list[str]:
    def inside(path: str) -> bool:
        return any(
            path.startswith(g) if g.endswith("/") else fnmatch.fnmatchcase(path, g)
            for g in globs
        )

    return [p for p in paths if p and not inside(p)]


def _lane_scope(argv: list[str]) -> int:
    """`--lane-scope --body-file F [--require]`, changed paths on stdin."""
    parser = argparse.ArgumentParser(prog="drain_review_gate.py --lane-scope")
    parser.add_argument("--body-file", type=Path, required=True)
    parser.add_argument("--require", action="store_true",
                        help="the diff touches a release-critical path")
    args = parser.parse_args(argv)
    globs = declared_lane(_read_text(args.body_file) or "")
    if globs is None:
        if args.require:
            print("this PR touches a release-critical path: declare its files as "
                  "`Lane: <glob> <dir/> ...` in the body", file=sys.stderr)
            return 2
        return 0
    stray = outside_lane(sys.stdin.read().splitlines(), globs)
    if stray:
        print("files outside the declared Lane (branch from origin/main, or "
              "widen the Lane):", file=sys.stderr)
        for path in stray:
            print(f"  {path}", file=sys.stderr)
        return 2
    return 0


def _print_diff_key(argv: list[str]) -> int:
    """`--print-diff-key BASE HEAD`: the value a reviewer stamps as Drain-Review-Diff."""
    parser = argparse.ArgumentParser(prog="drain_review_gate.py --print-diff-key")
    parser.add_argument("base", help="the PR's base, e.g. origin/main")
    parser.add_argument("head", nargs="?", default="HEAD")
    args = parser.parse_args(argv)
    try:
        print(diff_key(args.base, args.head))
    except (subprocess.CalledProcessError, ValueError) as exc:
        print(f"could not compute the diff key: {exc}", file=sys.stderr)
        return 2
    return 0


def main() -> int:
    if sys.argv[1:2] == ["--print-diff-key"]:
        return _print_diff_key(sys.argv[2:])
    if sys.argv[1:2] == ["--list-carry-heads"]:
        return _list_carry_heads(sys.argv[2:])
    if sys.argv[1:2] == ["--lane-scope"]:
        return _lane_scope(sys.argv[2:])
    parser = argparse.ArgumentParser()
    parser.add_argument("--head", required=True)
    parser.add_argument("--body-file", type=Path, required=True)
    parser.add_argument(
        "--base",
        default="",
        help="The PR base commit. With a valid --diff-key, a Drain-Review-Head "
        "receipt for another commit is accepted when that commit's tree-diff "
        "against this base equals the current head's. Empty means only the "
        "exact head (or an explicit Drain-Review-Diff) can match.",
    )
    parser.add_argument(
        "--title",
        default=None,
        help="PR title. Absent, or a [floor] title or `floor` label: no "
        "test-module carry (ADR-015 floor class).",
    )
    parser.add_argument("--labels", default="", help="PR labels, comma-separated.")
    parser.add_argument(
        "--diff-key",
        default="",
        help="The PR's diff key as computed by the workflow (diff_key), which "
        "lets a `Drain-Review-Diff:` receipt match and lets an unchanged "
        "rebase carry a head receipt. Empty or malformed means only an exact "
        "`Drain-Review-Head:` of --head can match.",
    )
    parser.add_argument(
        "--blocking-review",
        action="store_true",
        help="The required check's decision: prints allow (exit 0) or deny "
        "(exit 2), naming the head and diff key a receipt must carry on "
        "stderr. Requires --review-repo, --review-pr, --review-comments-file.",
    )
    parser.add_argument("--review-repo", default="", help="owner/repo of this PR.")
    parser.add_argument("--review-pr", type=int, help="This PR's number.")
    parser.add_argument(
        "--review-comments-file",
        type=Path,
        help="JSON objects ({url, association}) for this PR's comments, "
        "reviews and review comments. Unreadable => deny.",
    )
    args = parser.parse_args()

    if args.blocking_review:
        if args.review_pr is None or not args.review_repo:
            parser.error("--blocking-review needs --review-pr and --review-repo")
        return _blocking_review(args)

    try:
        body = args.body_file.read_text(encoding="utf-8")
    except OSError:
        print("deny")
        return 2

    if review_allows_merge(
        head=args.head,
        body=body,
        diff_key=args.diff_key or None,
        same_change=_carry_comparer(args),
    ):
        print("allow")
        return 0
    print("deny")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
