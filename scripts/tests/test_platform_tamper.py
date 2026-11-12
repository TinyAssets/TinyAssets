"""The tamper check reads the platform's tests: a weakened, removed or skipped case says why."""
from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SCRIPTS))
import platform_tests  # noqa: E402
import test_hygiene_gate as hygiene  # noqa: E402

_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
}
_FILE = "platform/owner/test/thing.test.ts"
_TEST = """import { describe, expect, it } from "vitest";

describe("thing", () => {
  it("adds", () => {
    expect(add(1, 2)).toBe(3);
    expect(add(0, 0)).toBe(0);
  });

  it("refuses a string", () => {
    expect(() => add("1", 2)).toThrow(/a number/);
  });
});
"""


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                          check=True, env=_ENV).stdout.strip()


def _commit(root: Path, files: dict[str, str | None]) -> str:
    for rel, text in files.items():
        path = root / rel
        if text is None:
            path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "--allow-empty", "-m", "c")
    return _git(root, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, str]:
    _git(tmp_path, "init", "-q", "-b", "main")
    return tmp_path, _commit(tmp_path, {_FILE: _TEST, "platform/owner/src/thing.ts": "x\n"})


def _failures(root: Path, base: str, files: dict[str, str | None], body: str = "") -> list[str]:
    head = _commit(root, files)
    delta = hygiene.compute_delta(base, head, root, dt.date(2026, 11, 12))
    return hygiene.evaluate(delta, body)


_REASON = "Test-Removal: wrong -- the second sum asserted nothing the first did not"


def test_a_weakened_platform_assertion_is_caught(repo) -> None:
    root, base = repo
    weakened = _TEST.replace("    expect(add(0, 0)).toBe(0);\n", "")
    change = {_FILE: weakened, "platform/owner/src/thing.ts": "y\n"}
    head = _commit(root, change)
    delta = hygiene.compute_delta(base, head, root, dt.date(2026, 11, 12))
    failures = hygiene.evaluate(delta, "")
    assert len(failures) == 1
    assert _FILE in failures[0] and "expectations loosened (3 -> 2)" in failures[0]
    # A reason the review reads is enough, beside product code too.
    assert hygiene.evaluate(delta, _REASON) == []


@pytest.mark.parametrize("edit,what", [
    (lambda t: t.replace('  it("refuses a string"', '  it.skip("refuses a string"'),
     "new skip or todo (0 -> 1)"),
    (lambda t: t[:t.index('  it("refuses a string"')] + "});\n", "cases removed (2 -> 1)"),
    (lambda t: None, "cases removed (2 -> 0)"),
    # An assertion turned into a comment is gone, though its text is still there.
    (lambda t: t.replace("    expect(add(0, 0))", "    // expect(add(0, 0))"),
     "expectations loosened (3 -> 2)"),
])
def test_a_skipped_removed_or_deleted_platform_test_is_caught(repo, edit, what) -> None:
    root, base = repo
    failures = _failures(root, base, {_FILE: edit(_TEST)})
    assert len(failures) == 1 and what in failures[0], failures


def test_a_move_a_new_case_or_a_comment_is_not_tampering(repo) -> None:
    root, base = repo
    grown = _TEST.replace(
        '  it("refuses a string"',
        '  // it("was", () => { expect(1) }) -- a note, not a case\n'
        '  it("subtracts", () => {\n    expect(sub(3, 2)).toBe(1);\n  });\n\n'
        '  it("refuses a string"',
    )
    assert grown != _TEST
    moved = "platform/owner/test/thing-moved.test.ts"
    assert _failures(root, base, {_FILE: None, moved: grown}) == []


def test_a_platform_test_is_counted_as_code_not_as_text() -> None:
    text = (
        'it("an it( a test( an expect(", () => {\n'
        "  /* it( expect( */ // test( expect(\n"
        "  expect(`a ${f(`b ${\"}\"}`)} expect(`).toBe(1);\n"
        "  expect(/it'(/.test(s)).toBe(true);\n"
        "  const half = a / b / c; expect(half).toBe(2);\n"
        "  assert.equal(n, 1); expect.any(Number);\n"
        "});\n"
        'test.each([1, 2])("row %i", (n) => { expect(n).toBeTruthy(); });\n'
        'describe.skipIf(ci)("z", () => { it("q", (ctx) => { ctx.skip(); }); });\n'
        'it.todo("later");\n'
    )
    code = platform_tests.code_only(text)
    assert len(code) == len(text) and code.count("\n") == text.count("\n")
    assert platform_tests.counts(text) == platform_tests.Counts(cases=4, expects=5, skips=3)


def test_every_platform_test_here_reads_as_cases_and_expectations() -> None:
    """The counter knows the platform's own style: every file holds a case it can see,
    but the two configs whose cases the config kit makes (`ci/kit/config.ts`)."""
    root = _SCRIPTS.parent
    listed = subprocess.run(["git", "-C", str(root), "ls-files", "platform"],
                            capture_output=True, text=True, check=True).stdout.split()
    files = [path for path in listed if platform_tests.is_platform_test(path)]
    assert len(files) > 100
    unseen = []
    for path in files:
        counted = platform_tests.counts((root / path).read_text(encoding="utf-8"))
        if counted.cases == 0 or counted.expects == 0:
            unseen.append(path)
    assert unseen == ["platform/files/guards/config.test.ts",
                      "platform/gateway/guards/config.test.ts"]
