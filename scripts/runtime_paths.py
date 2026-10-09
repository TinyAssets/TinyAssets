#!/usr/bin/env python3
"""Does a commit range change what production runs? The deploy chain's one classifier.

Every deploy recreates the daemon container, and a recreate kills every
in-flight user turn and run. So a merge that changes nothing production runs
must not deploy -- and a merge that changes anything it runs must never be
skipped. This module is the single answer to "is this range runtime-affecting?"
for all three places that ask it:

* ``build-image.yml`` -- skip building (and therefore deploying) when the pushed
  head is runtime-equivalent to the sha production's receipt names.
* ``release-reconcile.yml`` -- the drift backstop asks for the newest
  runtime-affecting commit, so a skipped merge is never reported as drift and
  never re-deployed by the timer.
* ``deployed_sha.py --assert-contains`` -- a commit that is runtime-equivalent
  to the served sha is reported as served (exit 0, labelled as such), because
  the running image's source tree equals it on every runtime path.

What counts as runtime is read MECHANICALLY from the tree being judged, never
from a hand-kept list that can drift:

* every source of a ``COPY``/``ADD`` in the head's ``Dockerfile`` (plus the
  Dockerfile and ``.dockerignore`` themselves);
* every file ``deploy/install-host-uptime-services.sh`` installs on the host
  (its ``RUNTIME_FILES`` array) and all of ``deploy/``;
* the deploy chain's own workflows -- ``build-image.yml``, everything its
  ``workflow_run`` triggers reach that holds the droplet SSH key, anything they
  call, the composite actions they use, and every workflow in the
  ``production-host-mutation`` group. An edit to one is inert until the next
  deploy, so it deploys itself;
* the Python the deploy runs: every ``scripts/*.py`` those workflows, actions,
  the host manifest or the Dockerfile name, plus the transitive closure of
  their local imports (:func:`python_import_closure`). An import that cannot
  be resolved makes all of ``scripts/`` count.

**Fail open.** Anything this module cannot establish -- an unreadable
Dockerfile, a wildcard ``COPY``, an unknown production sha, a production sha
that is not an ancestor of the head, a git failure -- is answered "runtime /
build". It errs toward a redundant deploy, never toward skipping a change.

Stdlib only: it runs in privileged workflow jobs that install nothing.

    python scripts/runtime_paths.py decide --base <served-sha> --head <sha>
    python scripts/runtime_paths.py newest-runtime-commit --rev <sha>
    python scripts/runtime_paths.py classify --base <sha> --head <sha>
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

DOCKERFILE = "Dockerfile"
HOST_MANIFEST = "deploy/install-host-uptime-services.sh"

#: Changes to these always change production, whatever the Dockerfile says.
BUILD_DEFINITION = (DOCKERFILE, ".dockerignore")

#: Host inputs that are not in the image: the compose bundle, the fail-safe
#: deploy script and every systemd unit. The Python the deploy runs is not
#: hand-listed: :func:`python_import_closure` derives it from every
#: ``scripts/*.py`` a deploy-chain workflow, composite action, the host
#: manifest or the Dockerfile names, following local imports transitively.
HOST_PATHS = ("deploy/",)
ACTIONS_DIR = ".github/actions/"
_SCRIPT_MENTION = re.compile(r"(?<![\w./-])(scripts/[A-Za-z0-9_./-]+\.py)\b")

#: The deploy chain's root. Every workflow reachable from it through
#: ``workflow_run`` triggers or ``uses: ./.github/workflows/...`` calls is part
#: of the deploy (deploy-prod, then install-host-services), and so is every
#: workflow declaring the droplet's :data:`HOST_MUTATION_GROUP`. An edit to any
#: of them changes what the next deploy does to the host -- inert until then --
#: so it is runtime: it builds, deploys itself, and is never "equivalent".
WORKFLOWS_DIR = ".github/workflows/"
DEPLOY_CHAIN_ROOT = ".github/workflows/build-image.yml"
HOST_MUTATION_GROUP = "production-host-mutation"
#: The droplet SSH credential; a workflow that names it can write the host.
HOST_CREDENTIAL = "secrets.DO_SSH_KEY"

#: How far back ``newest-runtime-commit`` walks before giving up. Giving up
#: returns the starting commit, i.e. "treat it as runtime" (fail open).
DEFAULT_WALK_LIMIT = 2000

_GLOB_CHARS = re.compile(r"[*?\[]")


class ClassifyError(Exception):
    """git could not answer; callers must treat the range as runtime."""


# --- git -------------------------------------------------------------------


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _git_ok(repo: Path, *args: str) -> str:
    proc = _git(repo, *args)
    if proc.returncode != 0:
        raise ClassifyError(f"git {' '.join(args)}: {(proc.stderr or '').strip()}")
    return proc.stdout


def resolve(repo: Path, rev: str) -> str:
    return _git_ok(repo, "rev-parse", "--verify", f"{rev}^{{commit}}").strip()


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    return _git(repo, "merge-base", "--is-ancestor", ancestor, descendant).returncode == 0


# --- what the tree says is runtime ----------------------------------------


def _logical_lines(text: str) -> list[str]:
    """Dockerfile lines with ``\\`` continuations joined and comments dropped."""
    lines: list[str] = []
    buf = ""
    for raw in text.splitlines():
        stripped = raw.strip()
        if not buf and (not stripped or stripped.startswith("#")):
            continue
        if buf and stripped.startswith("#"):
            continue  # comment lines inside a continuation are ignored by docker
        if stripped.endswith("\\"):
            buf += stripped[:-1] + " "
            continue
        lines.append(buf + stripped)
        buf = ""
    if buf:
        lines.append(buf)
    return lines


def dockerfile_copy_sources(text: str) -> tuple[list[str], bool]:
    """Build-context sources of every ``COPY``/``ADD``.

    Returns ``(sources, everything)``. ``everything`` is True when a source
    cannot be bounded to a path -- ``.``, a wildcard at the context root, an
    unparseable JSON form -- so every path must count as runtime (fail open).
    Copies ``--from`` another stage read no build context and are skipped.
    """
    sources: list[str] = []
    everything = False
    for line in _logical_lines(text):
        parts = line.split(None, 1)
        if len(parts) < 2 or parts[0].upper() not in {"COPY", "ADD"}:
            continue
        rest = parts[1].strip()
        tokens: list[str] = []
        flags: list[str] = []
        # Flags always precede the operands, in both the shell and JSON forms.
        while rest.startswith("--"):
            flag, _, rest = rest.partition(" ")
            flags.append(flag)
            rest = rest.strip()
        if any(flag.startswith("--from") for flag in flags):
            continue
        if rest.startswith("["):
            try:
                parsed = json.loads(rest)
            except json.JSONDecodeError:
                everything = True
                continue
            if not isinstance(parsed, list) or not all(isinstance(t, str) for t in parsed):
                everything = True
                continue
            tokens = parsed
        else:
            tokens = rest.split()
        if len(tokens) < 2:
            everything = True
            continue
        for src in tokens[:-1]:
            if src.startswith("<<"):
                continue  # heredoc body: no build-context input
            if re.match(r"^[a-z][a-z0-9+.-]*://", src, re.IGNORECASE):
                continue  # remote ADD: not a repo path
            normalized = src
            while normalized.startswith("./"):
                normalized = normalized[2:]
            normalized = normalized.lstrip("/")
            match = _GLOB_CHARS.search(normalized)
            if match:
                # Bound a wildcard by the directory it sits under.
                normalized = normalized[: match.start()].rpartition("/")[0]
            normalized = normalized.rstrip("/")
            if normalized in {"", "."}:
                everything = True
                continue
            sources.append(normalized)
    return sources, everything


def host_manifest_files(text: str) -> list[str] | None:
    """The ``RUNTIME_FILES=( ... )`` array of the host-uptime installer."""
    match = re.search(r"^RUNTIME_FILES=\(\s*\n(.*?)^\)", text, re.MULTILINE | re.DOTALL)
    if not match:
        return None
    files: list[str] = []
    for raw in match.group(1).splitlines():
        item = raw.split("#", 1)[0].strip().strip("'\"")
        if item:
            files.append(item)
    return files


def _parse(source: str | None) -> ast.Module | None:
    if source is None:
        return None
    try:
        return ast.parse(source)
    except SyntaxError:
        return None


@dataclass(frozen=True)
class RuntimeInputs:
    """What production runs from one tree."""

    paths: tuple[str, ...]
    everything: bool = False
    notes: tuple[str, ...] = field(default=())

    def covers(self, path: str) -> bool:
        if self.everything:
            return True
        return any(path == p or path.startswith(p.rstrip("/") + "/") for p in self.paths)


_WORKFLOW_NAME = re.compile(r"^name:[ \t]*(.+?)[ \t]*$", re.MULTILINE)
_WORKFLOW_RUN_FLOW = re.compile(r"^[ \t]*workflows:[ \t]*\[([^\]]*)\]", re.MULTILINE)
_WORKFLOW_RUN_BLOCK = re.compile(
    r"^([ \t]*)workflows:[ \t]*\n((?:\1[ \t]*-[^\n]*\n)+)", re.MULTILINE
)
_LOCAL_CALL = re.compile(r"uses:[ \t]*['\"]?\./(\.github/workflows/[^\s'\"@]+)")
_LOCAL_ACTION = re.compile(r"uses:[ \t]*['\"]?\./(\.github/actions/[^\s'\"@]+?)/?['\"]?[ \t]*$",
                           re.MULTILINE)
_HOST_GROUP = re.compile(
    rf"^[ \t]*group:[ \t]*['\"]?{re.escape(HOST_MUTATION_GROUP)}['\"]?[ \t]*(?:#.*)?$",
    re.MULTILINE,
)


def _unquote(value: str) -> str:
    return value.strip().strip("'\"").strip()


def _triggering_workflow_names(text: str) -> set[str]:
    names: set[str] = set()
    for match in _WORKFLOW_RUN_FLOW.finditer(text):
        names.update(_unquote(item) for item in match.group(1).split(",") if item.strip())
    for match in _WORKFLOW_RUN_BLOCK.finditer(text):
        for line in match.group(2).splitlines():
            names.add(_unquote(line.strip().lstrip("-")))
    names.discard("")
    return names


def deploy_chain_workflows(workflows: dict[str, str]) -> list[str] | None:
    """Workflow files that make up, or write to the host alongside, the deploy.

    ``workflows`` maps each ``.github/workflows/*.yml`` path to its text. None
    when the chain's root is missing or unnamed -- the caller then counts every
    workflow (fail open).
    """
    root = workflows.get(DEPLOY_CHAIN_ROOT)
    if root is None or not _WORKFLOW_NAME.search(root):
        return None
    names = {path: _unquote(m.group(1)) for path, text in workflows.items()
             if (m := _WORKFLOW_NAME.search(text))}
    chain = {DEPLOY_CHAIN_ROOT}
    changed = True
    while changed:
        changed = False
        chain_names = {names[p] for p in chain if p in names}
        for path, text in workflows.items():
            if path in chain:
                continue
            if _triggering_workflow_names(text) & chain_names:
                chain.add(path)
                changed = True
        for path in list(chain):
            for called in _LOCAL_CALL.findall(workflows.get(path, "")):
                if called not in chain:
                    chain.add(called)
                    changed = True
    # Of what the deploy sets off, keep what can reach the host (or is called
    # by the chain). Post-deploy observers such as the uptime canary hold no
    # host credential; an edit to them changes nothing a deploy applies.
    kept = {
        path
        for path in chain
        if path == DEPLOY_CHAIN_ROOT
        or HOST_CREDENTIAL in workflows.get(path, "")
        or path not in workflows  # a called workflow that cannot be read
    }
    kept.update(
        called for path in chain for called in _LOCAL_CALL.findall(workflows.get(path, ""))
    )
    kept.update(path for path, text in workflows.items() if _HOST_GROUP.search(text))
    return sorted(kept)


_DYNAMIC_IMPORTERS = {"import_module", "__import__", "spec_from_file_location",
                      "run_path", "run_module", "SourceFileLoader"}


def _package_inits(path: str, files: set[str]) -> list[str]:
    """``__init__.py`` of every package enclosing ``path`` that exists."""
    parts = path.split("/")[:-1]
    return [
        init
        for i in range(1, len(parts) + 1)
        if (init := "/".join([*parts[:i], "__init__.py"])) in files
    ]


def _module_file(base: str, dotted: str, files: set[str]) -> str | None:
    stem = "/".join(p for p in [base, *dotted.split(".")] if p)
    for candidate in (f"{stem}.py", f"{stem}/__init__.py"):
        if candidate in files:
            return candidate
    return None


def _is_local_name(top: str, roots: list[str], files: set[str]) -> bool:
    """Does the repo carry a module or package with this top-level name?"""
    for root in roots:
        stem = f"{root}/{top}" if root else top
        if f"{stem}.py" in files or any(f.startswith(f"{stem}/") for f in files):
            return True
    return False


def python_import_closure(
    seeds: list[str],
    files: set[str],
    read: Callable[[str], str | None],
    stop: Callable[[str], bool],
    image_files: frozenset[str] = frozenset(),
) -> tuple[list[str], list[str]]:
    """Repo files the ``seeds`` import, transitively (seeds included).

    Imports resolve against the importing file's directory (``sys.path[0]``
    when run as a script, and the ``sys.path.insert(0, scripts/)`` idiom), the
    repo root, and ``scripts/``. Standard-library and third-party names (no
    such module in the repo) are external. A local import that cannot be
    resolved, a dynamic import, or an unparseable file is returned in the
    second list -- the caller fails open on it. ``stop(path)`` is True for files
    already runtime by another rule (the image's package trees); their own
    imports are the image's concern and are not walked. A dynamic import in one
    of ``image_files`` (a file the Dockerfile copies) loads another image file
    by its installed path, and every such file is a COPY source already.
    """
    stdlib = set(getattr(sys, "stdlib_module_names", ()))
    seen: set[str] = set()
    unresolved: list[str] = []
    queue = [s for s in seeds if s in files]
    unresolved.extend(s for s in seeds if s not in files)
    while queue:
        path = queue.pop()
        if path in seen:
            continue
        seen.add(path)
        queue.extend(i for i in _package_inits(path, files) if i not in seen)
        if stop(path):
            continue
        tree = _parse(read(path))
        if tree is None:
            unresolved.append(path)
            continue
        here = path.rpartition("/")[0]
        roots = list(dict.fromkeys([here, "", "scripts"]))
        found: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                if name in _DYNAMIC_IMPORTERS and path not in image_files:
                    unresolved.append(f"{path} (dynamic import)")
                continue
            if isinstance(node, ast.Import):
                targets = [(alias.name, None) for alias in node.names]
                bases = roots
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    segments = here.split("/") if here else []
                    bases = ["/".join(segments[: max(0, len(segments) - node.level + 1)])]
                    if node.module is None:
                        targets = [(alias.name, None) for alias in node.names]
                    else:
                        targets = [(node.module, [a.name for a in node.names])]
                else:
                    targets = [(node.module or "", [a.name for a in node.names])]
                    bases = roots
            else:
                continue
            for dotted, names in targets:
                top = dotted.split(".")[0]
                external = top in stdlib or not _is_local_name(top, bases, files)
                if not node_is_relative(node) and external:
                    continue  # stdlib or an installed distribution
                hit = next((m for b in bases if (m := _module_file(b, dotted, files))), None)
                if hit is None and node_is_relative(node) and names is None:
                    # `from . import name` where name is an attribute of the package.
                    hit = _module_file(bases[0], "", files)
                if hit is None:
                    unresolved.append(f"{path}: {dotted}")
                    continue
                found.append(hit)
                base_dir = hit.rpartition("/")[0] if hit.endswith("__init__.py") else None
                for name in names or ():
                    if base_dir is not None and (sub := _module_file(base_dir, name, files)):
                        found.append(sub)
        queue.extend(f for f in found if f not in seen)
    return sorted(seen), unresolved


def node_is_relative(node: ast.AST) -> bool:
    return isinstance(node, ast.ImportFrom) and bool(node.level)


def runtime_inputs(repo: Path, rev: str) -> RuntimeInputs:
    """Runtime inputs as the tree at ``rev`` defines them.

    One ``ls-tree`` per revision; file contents are cached by blob id, so a
    history walk re-reads only the blobs that actually changed.
    """
    out = _git_ok(repo, "ls-tree", "-r", "-z", "--full-tree", rev)
    tree: dict[str, str] = {}
    for entry in out.split("\0"):
        if not entry:
            continue
        meta, _, path = entry.partition("\t")
        fields = meta.split()
        if len(fields) == 3 and fields[1] == "blob":
            tree[path] = fields[2]

    def read(path: str) -> str | None:
        blob = tree.get(path)
        if blob is None:
            return None
        if blob not in _BLOB_CACHE:
            _BLOB_CACHE[blob] = _git_ok(repo, "cat-file", "-p", blob)
        return _BLOB_CACHE[blob]

    return runtime_inputs_from(read, lambda: list(tree))


_BLOB_CACHE: dict[str, str] = {}


def runtime_inputs_from(
    read: Callable[[str], str | None],
    list_files: Callable[[], list[str] | None],
) -> RuntimeInputs:
    """Runtime inputs from any tree.

    ``read(path)`` is None for a missing file; ``list_files()`` returns every
    tracked path, or None when it cannot tell (then workflows, actions and
    scripts all count).
    """
    notes: list[str] = []
    paths: list[str] = [*BUILD_DEFINITION, *HOST_PATHS]
    everything = False

    listed = list_files()
    files = set(listed) if listed is not None else None
    if files is None:
        paths.extend([WORKFLOWS_DIR, ACTIONS_DIR, "scripts/"])
        notes.append("tree unlistable -- workflows, actions and scripts all count")

    dockerfile = read(DOCKERFILE)
    image_sources: list[str] = []
    if dockerfile is None:
        everything = True
        notes.append("no Dockerfile at head -- every path counts as runtime")
    else:
        image_sources, wildcard = dockerfile_copy_sources(dockerfile)
        paths.extend(image_sources)
        if wildcard:
            everything = True
            notes.append("Dockerfile copies an unbounded source -- every path counts")

    manifest = read(HOST_MANIFEST)
    host_files = host_manifest_files(manifest) if manifest is not None else None
    if host_files is None:
        # Cannot tell which scripts the host runs: all of them count.
        paths.append("scripts/")
        notes.append("host manifest unreadable -- all of scripts/ counts as runtime")
        host_files = []
    paths.extend(host_files)

    # What the deploy chain runs, and every script it names.
    mention_texts: list[str] = [dockerfile or ""]
    if files is not None:
        workflows = {
            p: t
            for p in sorted(files)
            if p.startswith(WORKFLOWS_DIR)
            and p.endswith((".yml", ".yaml"))
            and (t := read(p)) is not None
        }
        chain = deploy_chain_workflows(workflows)
        if chain is None:
            paths.extend([WORKFLOWS_DIR, "scripts/"])
            notes.append("deploy chain unreadable -- every workflow and script counts")
        else:
            paths.extend(chain)
            mention_texts.extend(workflows.get(p, "") for p in chain)
            # Composite actions the chain uses (and the actions they use).
            pending = [a for p in chain for a in _LOCAL_ACTION.findall(workflows.get(p, ""))]
            actions: set[str] = set()
            while pending:
                action = pending.pop().rstrip("/")
                if action in actions:
                    continue
                actions.add(action)
                paths.append(f"{action}/")
                text = read(f"{action}/action.yml") or read(f"{action}/action.yaml")
                if text is None:
                    notes.append(f"{action} unreadable -- all of scripts/ counts")
                    paths.append("scripts/")
                    continue
                mention_texts.append(text)
                pending.extend(_LOCAL_ACTION.findall(text))
        mention_texts.extend(read(p) or "" for p in host_files if not p.endswith(".py"))

        seeds = sorted(
            {m for text in mention_texts for m in _SCRIPT_MENTION.findall(text)}
            | {p for p in [*host_files, *image_sources] if p.endswith(".py")}
        )
        image_trees = [s for s in image_sources if not s.endswith(".py")]

        def already_runtime(path: str) -> bool:
            return any(path == t or path.startswith(t.rstrip("/") + "/") for t in image_trees)

        closure, unresolved = python_import_closure(
            seeds, files, read, already_runtime,
            frozenset(p for p in image_sources if p.endswith(".py")))
        paths.extend(closure)
        if unresolved:
            # Fail open: whatever it could have reached counts.
            paths.append("scripts/")
            notes.append(
                "unresolvable import -- all of scripts/ counts: " + "; ".join(unresolved[:5])
            )

    return RuntimeInputs(
        paths=tuple(dict.fromkeys(paths)),
        everything=everything,
        notes=tuple(notes),
    )


# --- ranges ----------------------------------------------------------------


def changed_paths(repo: Path, base: str | None, head: str) -> list[str]:
    """Paths that differ between two trees; every file when there is no base.

    ``--no-renames`` so a file moved OUT of a runtime directory still reports
    its old path.
    """
    if base is None:
        out = _git_ok(repo, "ls-tree", "-r", "--name-only", head)
    else:
        out = _git_ok(repo, "diff", "--no-renames", "--name-only", base, head)
    return [line for line in out.splitlines() if line]


def runtime_changes(repo: Path, base: str | None, head: str) -> list[str]:
    """Runtime-affecting paths between ``base`` and ``head`` (tree to tree).

    Cumulative, not per-commit: a range of batched merges is judged as a whole,
    so a docs merge after an undeployed runtime merge still reports the runtime
    path. Raises :class:`ClassifyError` when git cannot answer.
    """
    inputs = runtime_inputs(repo, head)
    return [path for path in changed_paths(repo, base, head) if inputs.covers(path)]


def first_parent(repo: Path, rev: str) -> str | None:
    out = _git_ok(repo, "rev-list", "--parents", "-n", "1", rev).split()
    return out[1] if len(out) > 1 else None


def runtime_commits(repo: Path, base: str, head: str) -> list[str]:
    """Commits in ``base..head`` that each changed runtime (vs first parent)."""
    commits = _git_ok(repo, "rev-list", f"{base}..{head}").split()
    return [c for c in commits if runtime_changes(repo, first_parent(repo, c), c)]


def newest_runtime_commit(repo: Path, rev: str, limit: int = DEFAULT_WALK_LIMIT) -> str:
    """Newest first-parent ancestor of ``rev`` (inclusive) that changed runtime.

    Returns ``rev`` itself when the walk cannot find one within ``limit``
    commits -- fail open, the caller then treats ``rev`` as undeployed runtime.
    """
    start = resolve(repo, rev)
    commits = _git_ok(
        repo, "rev-list", "--first-parent", f"--max-count={limit}", start
    ).split()
    for commit in commits:
        if runtime_changes(repo, first_parent(repo, commit), commit):
            return commit
    return start


@dataclass(frozen=True)
class Decision:
    build: bool
    reason: str
    runtime_paths: tuple[str, ...] = ()

    @property
    def word(self) -> str:
        return "build" if self.build else "skip"


def decide(repo: Path, base: str | None, head: str) -> Decision:
    """Should ``head`` be built and deployed, given production serves ``base``?"""
    try:
        head_sha = resolve(repo, head)
    except ClassifyError as exc:
        return Decision(True, f"cannot resolve head {head!r}: {exc}")
    if not base:
        return Decision(True, "production's served sha is unknown")
    try:
        base_sha = resolve(repo, base)
    except ClassifyError:
        return Decision(True, f"production serves {base[:12]}, which this checkout does not have")
    if not is_ancestor(repo, base_sha, head_sha):
        return Decision(
            True, f"production serves {base_sha[:12]}, which is not an ancestor of {head_sha[:12]}"
        )
    try:
        hits = runtime_changes(repo, base_sha, head_sha)
    except ClassifyError as exc:
        return Decision(True, f"could not classify {base_sha[:12]}..{head_sha[:12]}: {exc}")
    if hits:
        shown = ", ".join(hits[:8]) + (f" (+{len(hits) - 8} more)" if len(hits) > 8 else "")
        return Decision(
            True,
            f"runtime inputs changed since production's {base_sha[:12]}: {shown}",
            tuple(hits),
        )
    return Decision(
        False,
        f"production's {base_sha[:12]} already serves every runtime input of {head_sha[:12]}",
    )


# --- CLI -------------------------------------------------------------------


def _one_line(text: str) -> str:
    return " ".join(text.split())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", type=Path, default=REPO_ROOT)
    sub = ap.add_subparsers(dest="command", required=True)

    p_decide = sub.add_parser("decide", help="build/skip for head given the served sha")
    p_decide.add_argument("--base", default="", help="sha production serves ('' = unknown)")
    p_decide.add_argument("--head", required=True)
    p_decide.add_argument(
        "--github-output", type=Path, help="append decision=/reason= lines to this file"
    )

    p_newest = sub.add_parser("newest-runtime-commit")
    p_newest.add_argument("--rev", required=True)
    p_newest.add_argument("--limit", type=int, default=DEFAULT_WALK_LIMIT)

    p_classify = sub.add_parser("classify", help="list runtime paths changed base..head")
    p_classify.add_argument("--base", required=True)
    p_classify.add_argument("--head", required=True)

    args = ap.parse_args(argv)
    repo: Path = args.repo

    if args.command == "decide":
        decision = decide(repo, args.base.strip() or None, args.head)
        print(f"{decision.word}: {decision.reason}")
        if args.github_output:
            with args.github_output.open("a", encoding="utf-8") as fh:
                fh.write(f"decision={decision.word}\n")
                fh.write(f"reason={_one_line(decision.reason)}\n")
        return 0

    try:
        if args.command == "newest-runtime-commit":
            print(newest_runtime_commit(repo, args.rev, args.limit))
            return 0
        hits = runtime_changes(repo, resolve(repo, args.base), resolve(repo, args.head))
    except ClassifyError as exc:
        print(f"cannot classify: {exc}", file=sys.stderr)
        return 2
    for hit in hits:
        print(hit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
