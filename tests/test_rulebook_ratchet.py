"""The rulebook only shrinks.

`AGENTS.md` § *The rulebook only shrinks* states the rule; `scripts/check_context_budget.py`
pins every rulebook file at the byte size it had after the 2026-09-26 cut, plus
two AGGREGATE pins over `docs/reference/` and `.agents/skills/`. This file is the
pawl: it proves the pins hold, that growth past a pin goes red, that a pin cannot
rise without another falling, and that a shrink cannot bank headroom for later.

Why a ratchet at all: the rule set grew from ~17.6 KB (2026-04-28) to 62,082 B
while the budget invariant was registered and violated the whole time, because
nothing failed. A measurement without something that fails is not a ratchet.

Three holes found by review, each with a test below:

* **Ceiling, not monotonic** (Astra): a file could shrink well under its pin and
  regrow with the gate green -> `test_pins_leave_no_stale_headroom`.
* **Raise the pin with the file** (Astra): editing `CONFIG` is just a diff ->
  `test_raising_a_pin_requires_displacing_another`.
* **Offload to an unpinned file** (Fable): write the next rule into a file nobody
  pinned -> the aggregates, and `test_aggregate_pins_cover_the_rule_directories`.

Bytes, not words or lines: bytes are what the model pays for, and one measure per
file avoids a second authority that can disagree with the first.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BUDGET_SCRIPT = REPO_ROOT / "scripts" / "check_context_budget.py"

# The five files AGENTS.md names as the rulebook. Pinned here as well as in the
# script so that silently DROPPING a file from the pinned set — the cheapest way
# to escape a pin — fails too.
RULEBOOK = (
    "AGENTS.md",
    "CLAUDE.md",
    "docs/reference/executable-gates.md",
)

# Deleted on purpose by the 2026-09-26 recut. A procedure doc is where a rule goes
# to hide, so their ABSENCE is pinned the same way a size is.
FORBIDDEN = (
    "docs/reference/quality-gates.md",
    "docs/reference/delivery-flow.md",
)

# The directories a rule (or a transcript of one) can be offloaded into.
RULE_DIRS = ("docs/reference/*.md", ".agents/skills/*/SKILL.md", "docs/reviews/*")

# The two numbers this file duplicates, and why they are worth duplicating:
# without them, RAISING a pin is just an edit to CONFIG that no check objects to,
# so "the rulebook only shrinks" would rest on a reviewer noticing. Capping the
# TOTALS makes displacement mechanical — a pin may go up only if another comes
# down by at least as much — while lowering any pin stays free.
POST_CUT_TOTAL = 4403         # sum of the per-file pins
POST_CUT_AGGREGATE_TOTAL = 596860   # sum of the directory pins



def _load_budget_module():
    spec = importlib.util.spec_from_file_location("check_context_budget_ratchet", BUDGET_SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def cb():
    return _load_budget_module()


# ---------------------------------------------------------------- the pinned set


def test_every_rulebook_file_is_pinned(cb) -> None:
    pinned = {b.path for b in cb.CONFIG}
    assert pinned == set(RULEBOOK), (
        "the pinned set drifted from the rulebook AGENTS.md declares; dropping a "
        "file from CONFIG is the cheapest way to escape its pin"
    )


def test_the_deleted_procedure_docs_stay_deleted(cb) -> None:
    """Recreating one is how the rulebook grows back, so it fails like an overrun."""
    assert set(cb.FORBIDDEN) == set(FORBIDDEN)
    for rel in FORBIDDEN:
        assert not (REPO_ROOT / rel).exists(), f"{rel} came back"


def test_recreating_a_deleted_doc_goes_red(cb, tmp_path: Path) -> None:
    _fake_repo(cb, tmp_path)
    revived = tmp_path / FORBIDDEN[0]
    revived.parent.mkdir(parents=True, exist_ok=True)
    revived.write_bytes(b"# quality gates")

    _results, _combined, hard_busted, _imported, missing = cb.run(tmp_path)

    assert hard_busted
    assert any(FORBIDDEN[0] in entry for entry in missing)


def test_aggregate_pins_cover_the_rule_directories(cb) -> None:
    """Per-file pins alone leave an offload hole: write the rule somewhere unpinned."""
    assert {a.pattern for a in cb.AGGREGATES} == set(RULE_DIRS)


def test_no_file_grows_past_its_pin(cb) -> None:
    """One-directional: growth past a pin fails; shrinking always passes.

    This used to also require every pin to sit within 250 B of its file
    (600 B for aggregates), so shrinking banked no headroom. That made the pin
    a second copy of the file's size: two PRs that each shrank AGENTS.md both
    had to lower the same pin, and the second one collided in the merge queue.
    Lowering a pin is now optional (lead decision, 2026-10-01). The founder's
    rule -- the rulebook only shrinks -- is still enforced where it matters:
    no file may grow past its pin, and a pin may rise only by displacing
    another (test_raising_a_pin_requires_displacing_another).
    """
    for budget in cb.CONFIG:
        actual = cb.measure(budget, REPO_ROOT).bytes
        assert actual <= budget.max_bytes, (
            f"{budget.path} is {actual} B, past its {budget.max_bytes} B pin. "
            "The rulebook only shrinks: cut it, or raise this pin by lowering another."
        )


def test_no_aggregate_grows_past_its_pin(cb) -> None:
    for agg in cb.AGGREGATES:
        result = cb.measure_aggregate(agg, REPO_ROOT)
        assert result.bytes <= agg.max_bytes, (
            f"{agg.label} totals {result.bytes} B, past its {agg.max_bytes} B pin."
        )


def test_raising_a_pin_requires_displacing_another(cb) -> None:
    """A new rule must displace an old one — enforced, not left to a reviewer.

    Lowering any pin is free (the total drops). Raising one only passes if another
    pin drops by at least as much, which is what "displace" means.
    """
    total = sum(b.max_bytes for b in cb.CONFIG)
    assert total <= POST_CUT_TOTAL, (
        f"pinned total rose to {total} (was {POST_CUT_TOTAL}). Raising a pin needs "
        "another pin lowered by at least as much; if the rulebook genuinely shrank "
        "elsewhere, lower POST_CUT_TOTAL in the same diff."
    )
    aggregate = sum(a.max_bytes for a in cb.AGGREGATES)
    assert aggregate <= POST_CUT_AGGREGATE_TOTAL, (
        f"aggregate pin total rose to {aggregate} (was {POST_CUT_AGGREGATE_TOTAL})"
    )


def test_line_caps_are_unchecked(cb) -> None:
    """Bytes are the ratchet; a reflow that cuts words must never fail on lines."""
    assert all(b.max_lines == 0 for b in cb.CONFIG)


# ------------------------------------------------------------------- the gate


def test_no_rulebook_file_exceeds_its_pin(cb) -> None:
    results, combined, hard_busted, _imported, missing = cb.run(REPO_ROOT)
    over = [f"{r.path}: {r.bytes} > {r.max_bytes}" for r in results if r.over_bytes]
    assert not over, "rulebook grew past its pin: " + "; ".join(over)
    assert not missing, f"a pinned rulebook file is gone: {missing}"
    assert combined <= cb.COMBINED_HARD_BYTES
    assert not hard_busted


def test_combined_ceiling_is_not_loose(cb) -> None:
    """The combined ceiling tracks the always-loaded files, not a wish."""
    always = sum(b.max_bytes for b in cb.CONFIG if b.always_loaded)
    assert cb.COMBINED_HARD_BYTES <= always


def test_the_rule_is_stated_where_it_now_lives() -> None:
    """The principle is PLAN.md's; the loop carries the one line an agent acts on.

    Before the 2026-09-26 recut both sentences sat in AGENTS.md. The founder's bar
    moved principles to `PLAN.md`, so asserting the old wording in the always-loaded
    file would pin the rulebook to the shape the recut removed.
    """
    plan = (REPO_ROOT / "PLAN.md").read_text(encoding="utf-8")
    assert "The rulebook only shrinks" in plan
    assert "A new rule must displace an old one" in plan

    agents = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "A new rule deletes an old one" in agents


def _load_sync_module():
    spec = importlib.util.spec_from_file_location(
        "sync_direction_ratchet", REPO_ROOT / "scripts" / "sync_direction.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_direction_copy_matches_readme() -> None:
    """The ratchet skips AGENTS.md's Direction block; this is why that is safe.

    The block must be README's verbatim, and capped, or it is an unpinned place
    to write rules. Fix: edit README.md, then run python scripts/sync_direction.py.
    """
    assert _load_sync_module().problems(REPO_ROOT) == []


# ------------------------------------------------------- can it actually fail?


def test_a_drifted_or_oversized_direction_goes_red(tmp_path: Path) -> None:
    sd = _load_sync_module()

    def block(body: str) -> str:
        return f"{sd.START}\n{body}\n{sd.END}\n"

    (tmp_path / "README.md").write_text("# R\n" + block("1. new"), encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("# A\n" + block("1. old") + "rules\n", encoding="utf-8")
    assert sd.main(["--check", "--root", str(tmp_path)]) == 1

    assert sd.main(["--root", str(tmp_path)]) == 0
    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert agents == "# A\n" + block("1. new") + "rules\n"

    long = "\n".join(f"{n}. line" for n in range(sd.MAX_LINES + 1))
    (tmp_path / "README.md").write_text(block(long), encoding="utf-8")
    assert sd.main(["--root", str(tmp_path)]) == 1, "over the line cap even when synced"


def test_rules_written_outside_the_direction_block_still_count(cb, tmp_path: Path) -> None:
    """Only the marked block is exempt; the rest of AGENTS.md is still pinned."""
    _fake_repo(cb, tmp_path)
    agents = tmp_path / "AGENTS.md"
    agents.write_bytes(b"<!-- direction:start -->" + b"d" * 500 + b"<!-- direction:end -->"
                       + agents.read_bytes())
    assert not cb.run(tmp_path)[2], "the direction block is not a rule"
    agents.write_bytes(agents.read_bytes() + b"x")
    assert cb.run(tmp_path)[2], "one rule byte past the pin is red"

    _fake_repo(cb, tmp_path)
    claude = tmp_path / "CLAUDE.md"
    claude.write_bytes(b"<!-- direction:start --><!-- direction:end -->" + claude.read_bytes())
    assert cb.run(tmp_path)[2], "only AGENTS.md carries the synced block"


def _fake_repo(cb, root: Path, overrides: dict[str, int] | None = None) -> dict[str, int]:
    """A tree that satisfies every pin exactly, so one deliberate change is the
    only thing a test is measuring.

    Each aggregate gets a filler file sized to top its set up to its pin, because
    an aggregate whose glob matches nothing reports MISSING — correctly, but that
    would drown out the signal these tests are after.
    """
    sizes = {b.path: b.max_bytes for b in cb.CONFIG}
    sizes.update(overrides or {})
    for rel, size in sizes.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)

    for agg in cb.AGGREGATES:
        already = cb.measure_aggregate(agg, root).bytes
        filler = root / agg.pattern.replace("*", "_filler")
        filler.parent.mkdir(parents=True, exist_ok=True)
        filler.write_bytes(b"x" * max(0, agg.max_bytes - already))
    return sizes


def test_a_tree_at_every_pin_is_green(cb, tmp_path: Path) -> None:
    """Guards the guard: if the baseline tree were red, nothing below means anything."""
    _fake_repo(cb, tmp_path)
    _results, _combined, hard_busted, _imported, missing = cb.run(tmp_path)
    assert not missing
    assert not hard_busted


def test_growth_past_a_pin_goes_red(cb, tmp_path: Path) -> None:
    grown = cb.CONFIG[-1].path          # a pointer-loaded file, the weaker half
    _fake_repo(cb, tmp_path, {grown: cb.CONFIG[-1].max_bytes + 1})

    results, _combined, hard_busted, _imported, _missing = cb.run(tmp_path)

    assert hard_busted, "one byte over a pin must fail"
    assert grown in [r.path for r in results if r.over_bytes]


def test_growth_in_an_unpinned_file_still_goes_red(cb, tmp_path: Path) -> None:
    """The offload hole Fable named: a new rule written where no per-file pin looks.

    `docs/reference/cloud-prepush-oracle.md` has no pin of its own. Growing it has
    to fail anyway, or the aggregate is decoration.
    """
    _fake_repo(cb, tmp_path)
    offload = tmp_path / "docs" / "reference" / "cloud-prepush-oracle.md"
    offload.write_bytes(b"y" * 400)

    results, _combined, hard_busted, _imported, _missing = cb.run(tmp_path)

    assert hard_busted
    assert "docs/reference/*.md" in [r.path for r in results if r.over_bytes]


def test_growth_in_a_skill_goes_red(cb, tmp_path: Path) -> None:
    """A rule moved into a skill is still a rule (lead directive, 2026-09-26)."""
    _fake_repo(cb, tmp_path)
    skill = tmp_path / ".agents" / "skills" / "peer-agents" / "SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_bytes(b"z" * 700)

    results, _combined, hard_busted, _imported, _missing = cb.run(tmp_path)

    assert hard_busted
    assert ".agents/skills/*/SKILL.md" in [r.path for r in results if r.over_bytes]


def test_an_aggregate_whose_glob_matches_nothing_is_missing(cb, tmp_path: Path) -> None:
    """A renamed directory must not read as "0 bytes, well under the pin"."""
    for budget in cb.CONFIG:
        path = tmp_path / budget.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * budget.max_bytes)

    _results, _combined, hard_busted, _imported, missing = cb.run(tmp_path)

    assert ".agents/skills/*/SKILL.md" in missing
    assert hard_busted


def test_shrinking_under_a_pin_passes_and_growing_past_it_fails(
    cb, tmp_path: Path,
) -> None:
    """The ceiling, both ways: being under a pin is fine, being over it is red.

    Regrowth within a pin that was never lowered is accepted by design
    (2026-10-01): it is the price of letting two shrinking PRs merge without
    both rewriting the same pin.
    """
    target = cb.CONFIG[-1]
    _fake_repo(cb, tmp_path, {target.path: target.max_bytes - 300})
    _results, _combined, hard_busted, _imported, _missing = cb.run(tmp_path)
    assert not hard_busted, "under a pin is fine"

    _fake_repo(cb, tmp_path, {target.path: target.max_bytes + 1})
    _results, _combined, hard_busted, _imported, _missing = cb.run(tmp_path)
    assert hard_busted, "one byte past a pin is red"


def test_deleting_a_rulebook_file_goes_red(cb, tmp_path: Path) -> None:
    """Deleting AGENTS.md must never be the cheapest way to satisfy its own pin."""
    _fake_repo(cb, tmp_path)
    (tmp_path / "AGENTS.md").unlink()

    _results, _combined, hard_busted, _imported, missing = cb.run(tmp_path)

    assert missing == ["AGENTS.md"]
    assert hard_busted


def test_pointer_files_do_not_count_toward_the_always_loaded_total(cb, tmp_path: Path) -> None:
    """A docs/reference procedure is pinned, but it is not paid for every turn.

    Folding it into COMBINED would make the per-turn payload read ~11 KB heavier
    than it is, and would push the combined total over its ceiling on a tree that
    is exactly at every pin.
    """
    _fake_repo(cb, tmp_path)

    _results, combined, hard_busted, _imported, _missing = cb.run(tmp_path)

    always = sum(b.max_bytes for b in cb.CONFIG if b.always_loaded)
    assert combined == always
    assert not hard_busted
