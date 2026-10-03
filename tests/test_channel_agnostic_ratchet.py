"""The platform must not know what GitHub is.

Founder, 2026-09-03: *"must also be all agnostic shapes. no github spasific code
should excist on the plateform, nor should any other spasific channel code
excist. users can build what they need to work with any other plateform they
want in what ever way they want to"*

A rule nobody measures is a rule nobody keeps. `scripts/check_channel_agnostic.py`
counts channel names reaching the runtime in the user substrate and compares
against a committed baseline, so no (file, channel) count can grow past it.
Lowering the baseline after a deletion is optional: shrink-then-regrow up to
the recorded ceiling is accepted (lead decision 2026-10-01) so that two PRs
that each delete channel code never collide in the merge queue.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check_channel_agnostic.py"


def _module():
    spec = importlib.util.spec_from_file_location("check_channel_agnostic", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_channel_agnostic"] = module
    spec.loader.exec_module(module)
    return module


def test_the_substrate_has_not_grown_channel_specific_code():
    """The ratchet itself. Deleting is free; adding is not."""
    assert _module().main([]) == 0


def test_the_baseline_is_a_ceiling_not_a_copy():
    """Every (file, channel) count is at or below the committed baseline.

    This used to require an EXACT match, which made the baseline a second copy
    of the tree: every PR that deleted channel code had to rewrite it, and two
    such PRs in one merge-queue batch broke each other. It was the most
    frequent merge-queue failure in the four days to 2026-10-01 (17 runs). Growth
    is what the rule forbids, so growth is what fails; lowering the baseline
    after a deletion is optional (`--update`), and any later PR may do it.
    """
    module = _module()
    baseline = module.load_baseline()
    grown = {
        key: (count, baseline.get(key, 0))
        for key, count in module.survey().items()
        if count > baseline.get(key, 0)
    }
    assert not grown, f"channel-specific code grew past the baseline: {grown}"


def test_docstrings_do_not_count(tmp_path):
    """Most mentions in this tree are prose explaining why something IS
    agnostic. Counting those would make the rule unmeetable, so nobody would
    keep it."""
    module = _module()
    source = tmp_path / "prose.py"
    source.write_text(
        '"""This module is deliberately agnostic: no GitHub logic lives here."""\n'
        "\n"
        "def f():\n"
        '    """Not GitHub-specific either, despite the word GitHub."""\n'
        "    return 1\n",
        encoding="utf-8",
    )
    assert [t for t in module.runtime_strings(source) if "github" in t.lower()] == []


def test_a_runtime_literal_does_count(tmp_path):
    """The other half: a channel name the code actually uses is a hit."""
    module = _module()
    source = tmp_path / "real.py"
    source.write_text(
        '"""Agnostic, allegedly."""\n'
        "\n"
        "def f():\n"
        '    return "https://api.github.com"\n',
        encoding="utf-8",
    )
    hits = [t for t in module.runtime_strings(source) if "github" in t.lower()]
    assert hits == ["https://api.github.com"]


#: The file that owns the relocated-docstring exemption.
OWNER = "tinyassets/engine_mcp_server.py"


def _engine_copy(tmp_path, body: str, module):
    """A file AT the owning path, so path-scoped exemptions apply to it.

    The exemption is keyed on the repo-relative path, so a scoping test has to
    write at that path rather than pass an arbitrary temp file -- which is exactly
    the hole the PR #4000 review found: the first version of this test used
    `relocated.py` and passed, because the file did not matter.
    """
    target = tmp_path / OWNER
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    module.REPO_ROOT = tmp_path
    return target


def test_a_relocated_docstring_is_exempt_in_the_file_that_owns_it(tmp_path):
    """2026-09-26: `write_graph`'s chapters left its docstring for a module
    constant so they stop riding on every model round-trip of every served turn.
    The text did not change, so counting it now would make the rule unmeetable for
    exactly the reason docstrings are exempt."""
    module = _module()
    name = sorted(module.DOCUMENTATION_CONSTANTS[OWNER])[0]
    source = _engine_copy(
        tmp_path,
        '"""Agnostic, allegedly."""\n'
        "\n"
        f'{name} = """Ask for a GitHub key the way the site names it."""\n'
        '_OTHER_CONSTANT = """A GitHub mention in an unlisted constant."""\n'
        "\n"
        "def f():\n"
        '    return "https://api.github.com"\n',
        module,
    )
    hits = [t for t in module.runtime_strings(source) if "github" in t.lower()]
    assert "Ask for a GitHub key the way the site names it." not in hits
    # An unlisted constant and a real runtime literal still count.
    assert "A GitHub mention in an unlisted constant." in hits
    assert "https://api.github.com" in hits


def test_the_reviewer_probe_no_longer_hides_a_runtime_literal(tmp_path):
    """The PR #4000 blocking finding, as a test.

    The reviewer planted `tinyassets/zz_probe_effector.py` assigning an exempt
    NAME to a GitHub URL inside a function and calling `urlopen` on it, plus a
    Slack URL under another exempt name in a class -- and the gate reported clean
    at 621. A copy-pasted name must never switch the rule off.
    """
    module = _module()
    names = sorted(module.DOCUMENTATION_CONSTANTS[OWNER])
    probe = tmp_path / "tinyassets" / "zz_probe_effector.py"
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text(
        "import urllib.request\n"
        "\n"
        "\n"
        "def call():\n"
        f'    {names[0]} = "https://api.github.com/repos"\n'
        f"    return urllib.request.urlopen({names[0]})\n"
        "\n"
        "\n"
        "class Client:\n"
        f'    {names[-1]} = "slack.com/api/chat.postMessage"\n',
        encoding="utf-8",
    )
    module.REPO_ROOT = tmp_path
    hits = [
        text for text in module.runtime_strings(probe)
        if "github" in text.lower() or "slack" in text.lower()
    ]
    assert "https://api.github.com/repos" in hits, "the function-level literal is hidden"
    assert "slack.com/api/chat.postMessage" in hits, "the class-level literal is hidden"


def test_the_exempt_name_counts_inside_a_function_or_class_in_the_owning_file(tmp_path):
    """Module level is the claim, so `tree.body` has to be what enforces it."""
    module = _module()
    name = sorted(module.DOCUMENTATION_CONSTANTS[OWNER])[0]
    source = _engine_copy(
        tmp_path,
        '"""Agnostic, allegedly."""\n'
        "\n"
        "\n"
        "def f():\n"
        f'    {name} = "https://api.github.com/inside-a-function"\n'
        f"    return {name}\n"
        "\n"
        "\n"
        "class C:\n"
        f'    {name} = "slack.com/inside-a-class"\n',
        module,
    )
    hits = [
        text for text in module.runtime_strings(source)
        if "github" in text.lower() or "slack" in text.lower()
    ]
    assert "https://api.github.com/inside-a-function" in hits
    assert "slack.com/inside-a-class" in hits


def test_a_second_module_assignment_to_the_name_revokes_the_exemption(tmp_path):
    """Assigned twice is not a relocated docstring; it is a name being reused."""
    module = _module()
    name = sorted(module.DOCUMENTATION_CONSTANTS[OWNER])[0]
    source = _engine_copy(
        tmp_path,
        '"""Agnostic, allegedly."""\n'
        "\n"
        f'{name} = """Chapter prose mentioning GitHub."""\n'
        f'{name} = "https://api.github.com/second-assignment"\n',
        module,
    )
    hits = [t for t in module.runtime_strings(source) if "github" in t.lower()]
    assert "Chapter prose mentioning GitHub." in hits
    assert "https://api.github.com/second-assignment" in hits


def test_every_exempt_documentation_constant_still_exists():
    """A stale name in the exemption list is an exemption nobody can audit."""
    module = _module()
    from tinyassets import engine_mcp_server

    for name in module.DOCUMENTATION_CONSTANTS[OWNER]:
        assert isinstance(getattr(engine_mcp_server, name), str)
    assert (REPO_ROOT / OWNER).is_file()
    # Every keyed path must exist, or the exemption points at nothing.
    for rel in module.DOCUMENTATION_CONSTANTS:
        assert (REPO_ROOT / rel).is_file(), rel


def test_the_platform_acting_as_itself_is_listed_not_hidden():
    """Billing its own customers and shipping its own releases are not user
    capabilities. They are exempt BY NAME so the exemption can be argued with,
    rather than by a pattern that quietly swallows new files."""
    module = _module()
    assert "tinyassets/billing/stripe_adapter.py" in module.PLATFORM_OWN
    for rel in module.PLATFORM_OWN:
        assert (REPO_ROOT / rel).is_file(), f"{rel} is exempt but does not exist"


@pytest.mark.parametrize("channel", ["github", "slack", "notion", "stripe"])
def test_the_rule_is_about_any_channel_not_just_github(channel):
    """The founder said GitHub because it was in front of them, and then said
    'nor should any other spasific channel code excist'."""
    assert channel in _module().CHANNELS


@pytest.mark.parametrize(
    "vendor", ["openai", "chatgpt", "anthropic", "claude", "ollama", "openrouter"]
)
def test_the_same_rule_covers_compute(vendor):
    """Founder, same day: "the llm's the universe has access to ... all
    agnostic shapes. we shouldnt have a chatgpt spacific path."

    `connect_compute` already promises exactly that in its own contract -- ANY
    compute provider, no per-provider code, no allowlist -- so the count is the
    distance between the promise and the tree."""
    assert vendor in _module().VENDORS
