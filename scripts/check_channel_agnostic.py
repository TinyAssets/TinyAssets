#!/usr/bin/env python3
"""No channel-specific code on the platform. A ratchet, not a wish.

Founder, 2026-09-03:

    must also be all agnostic shapes. no github spasific code should excist on
    the plateform, nor should any other spasific channel code excist. users can
    build what they need to work with any other plateform they want in what
    ever way they want to

Their universe agreed and wrote it into its brain: the platform gives users
general shape-building primitives, "not baked-in GitHub logic, not Slack logic,
not service-shaped special cases hidden in the substrate."

That is a rule you cannot hold by intention. This measures it.

    python scripts/check_channel_agnostic.py            # fail on anything new
    python scripts/check_channel_agnostic.py --report   # what is there today
    python scripts/check_channel_agnostic.py --update   # after DELETING some

The baseline records what exists now, per (file, channel). A new channel name
in the substrate fails; an increased count in a file that already has some
fails. Deleting is always allowed, and `--update` is how you record it. The
number can only go down.

Why the AST and not grep
------------------------
Most mentions of a channel in this tree are docstrings explaining why a thing is
agnostic, and counting those would make the rule unmeetable and therefore
ignored. Only string literals and identifiers that reach the runtime count.

What is deliberately not a violation
------------------------------------
`PLATFORM_OWN` names the code where the platform acts as ITSELF rather than
offering a user capability: billing its own customers through its own payment
processor, and shipping its own releases to its own forge. A user never composes
those, so no amount of channel-agnosticism in the user substrate removes them.
They are listed by name rather than skipped by pattern, so the exemption is
visible and arguable instead of silent.
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import sys
from collections import Counter

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PACKAGE = REPO_ROOT / "tinyassets"
BASELINE = REPO_ROOT / ".github" / "channel-specific-baseline.txt"

#: Names a user might want to reach. Deliberately broad: the rule is about ANY
#: channel getting special treatment, not about GitHub in particular.
CHANNELS = (
    "bitbucket", "discord", "gitea", "github", "gitlab", "gmail", "hubspot",
    "notion", "shopify", "slack", "stripe", "twilio", "twitter",
)

#: The same rule for COMPUTE. Founder, 2026-09-03: "the llm's the universe has
#: access to ... all agnostic shapes. we shouldnt have a chatgpt spacific path.
#: the request popup that comes up if your universe isnt powered should allow
#: the user to connect any llm source they want to thier universe."
#:
#: `connect_compute` already promises this in its own contract -- "ANY compute
#: provider (Kimi, OpenRouter, any OpenAI-compatible endpoint, or a CLI
#: subscription) ... no per-provider code, no allowlist" -- so what this counts
#: is the distance between that promise and the tree.
VENDORS = (
    "anthropic", "chatgpt", "claude", "codex", "gemini", "grok", "groq",
    "kimi", "mistral", "ollama", "openai", "openrouter",
)

#: The platform acting as itself, not as a user's substrate. See the module
#: docstring: listed by name so the exemption is visible.
PLATFORM_OWN = {
    "tinyassets/billing/stripe_adapter.py",      # bills its own customers
    "tinyassets/auto_ship.py",                   # ships its own releases
}

#: Module constants that ARE documentation, exempt on the same grounds as a
#: docstring -- keyed BY FILE, because a name is not a credential.
#:
#: 2026-09-26: `write_graph`'s long-form chapters moved out of its docstring into
#: these constants so they stop riding on every model round-trip of every served
#: turn (`openspec/changes/engine-tool-manual-on-demand/`). The text did not
#: change -- only where it lives -- so counting it now would make the rule
#: unmeetable for exactly the reason the docstring exemption exists.
#:
#: Scoped three ways after a blocking review of PR #4000, which planted
#: ``tinyassets/zz_probe_effector.py`` assigning one of these names to
#: ``https://api.github.com/repos`` inside a function and calling ``urlopen`` on
#: it -- and the gate reported clean. A copy-pasted name must never switch the
#: rule off. The exemption therefore holds ONLY in the owning file, ONLY for a
#: module-level assignment, and ONLY when that name is assigned exactly once in
#: that module. Everything else -- the same name in a function, in a class, in
#: another file, or assigned twice -- counts.
#:
#: The survey walks ``tinyassets/`` only, so the packaging mirror is not surveyed
#: today; it is listed anyway so widening the survey cannot silently start
#: counting the mirrored copy.
_MIRROR = (
    "packaging/claude-plugin/plugins/tinyassets-universe-server/runtime/"
    "tinyassets/engine_mcp_server.py"
)
_ENGINE_CHAPTERS = frozenset({
    "_WRITE_GRAPH_CONNECTIONS_CHAPTER",
    "_WRITE_GRAPH_CODE_NODES_CHAPTER",
    "_WRITE_GRAPH_WORKSPACES_CHAPTER",
})
DOCUMENTATION_CONSTANTS: dict[str, frozenset[str]] = {
    "tinyassets/engine_mcp_server.py": _ENGINE_CHAPTERS,
    _MIRROR: _ENGINE_CHAPTERS,
}


def documentation_constants_for(path: pathlib.Path) -> frozenset[str]:
    """Names exempt IN THIS FILE, empty for every other file."""
    try:
        rel = path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return frozenset()
    return DOCUMENTATION_CONSTANTS.get(rel, frozenset())


def runtime_strings(path: pathlib.Path):
    """String literals and identifiers that reach the runtime.

    Docstrings are excluded: in this tree they mostly explain why something is
    agnostic, and counting them would make the rule unmeetable. A constant named
    in ``DOCUMENTATION_CONSTANTS`` is a relocated docstring and excluded the same
    way -- its value only, never the rest of the file.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            doc = ast.get_docstring(node, clean=False)
            if doc is not None:
                docstrings.add(doc)
    # A relocated docstring: `NAME = """..."""` at MODULE level, in the file that
    # owns the name, assigned exactly once there. Scoped this narrowly because a
    # name is not a credential -- see DOCUMENTATION_CONSTANTS. `tree.body` (not
    # `ast.walk`) is what makes "module level" true rather than intended.
    exempt_names = documentation_constants_for(path)
    if exempt_names:
        module_assigned: Counter = Counter()
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        module_assigned[target.id] += 1
        for node in tree.body:
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if (
                isinstance(target, ast.Name)
                and target.id in exempt_names
                and module_assigned[target.id] == 1
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                docstrings.add(node.value.value)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value not in docstrings:
                yield node.value
        elif isinstance(node, ast.Name):
            yield node.id
        elif isinstance(node, ast.Attribute):
            yield node.attr


def survey() -> Counter:
    """``{(relative path, channel): count}`` across the user substrate."""
    counts: Counter = Counter()
    for path in sorted(PACKAGE.rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel in PLATFORM_OWN:
            continue
        for text in runtime_strings(path):
            low = text.lower()
            for name in (*CHANNELS, *VENDORS):
                if name in low:
                    counts[(rel, name)] += 1
                    break
    return counts


def load_baseline() -> Counter:
    counts: Counter = Counter()
    if not BASELINE.is_file():
        return counts
    for raw in BASELINE.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        path, channel, count = line.rsplit(":", 2)
        counts[(path, channel)] = int(count)
    return counts


def render(counts: Counter) -> str:
    lines = [
        "# Channel-specific code in the user substrate, per (file, channel).",
        "#",
        "# The founder's rule (2026-09-03): no GitHub-specific code on the",
        "# platform, and no other channel's either. This file is what exists",
        "# TODAY, so the number can only go down. Regenerate with:",
        "#     python scripts/check_channel_agnostic.py --update",
        "#",
        f"# Total: {sum(counts.values())} across {len({p for p, _ in counts})} files.",
        "",
    ]
    lines += [
        f"{path}:{channel}:{count}"
        for (path, channel), count in sorted(counts.items())
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--update", action="store_true",
                    help="rewrite the baseline (use after DELETING code)")
    ap.add_argument("--report", action="store_true",
                    help="print what is there today, change nothing")
    args = ap.parse_args(argv)

    current = survey()

    if args.update:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(render(current), encoding="utf-8", newline="\n")
        print(f"baseline written: {sum(current.values())} in {BASELINE}")
        return 0

    if args.report:
        for (path, channel), count in sorted(current.items(), key=lambda kv: -kv[1]):
            print(f"{count:>4}  {channel:<10} {path}")
        print(f"\ntotal {sum(current.values())} across "
              f"{len({p for p, _ in current})} files")
        return 0

    baseline = load_baseline()
    if not baseline:
        print(
            "no baseline recorded; run --update once to record what exists today",
            file=sys.stderr,
        )
        return 2

    regressions = [
        (key, count, baseline.get(key, 0))
        for key, count in sorted(current.items())
        if count > baseline.get(key, 0)
    ]
    if regressions:
        print("channel-specific code ADDED to the user substrate:", file=sys.stderr)
        for (path, channel), now, was in regressions:
            arrow = f"{was} -> {now}" if was else f"new ({now})"
            print(f"  {path}  {channel}: {arrow}", file=sys.stderr)
        print(
            "\nThe platform gives users shape-building primitives; a channel is "
            "something a USER composes, not something the substrate knows about. "
            "If this is the platform acting as itself (billing, its own releases), "
            "add the file to PLATFORM_OWN with a reason.",
            file=sys.stderr,
        )
        return 1

    removed = sum(baseline.values()) - sum(current.values())
    if removed > 0:
        print(f"channel-specific code is down {removed}; run --update to record it")
    else:
        print(f"channel-agnostic check clean ({sum(current.values())} at baseline)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
