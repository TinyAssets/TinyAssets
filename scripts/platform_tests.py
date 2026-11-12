"""What a platform test file holds, counted as text: its cases, its expectations, its skips.

The tamper check (``test_hygiene_gate.py``) runs inside ``pull_request_target`` and
executes nothing from the pull request, so it cannot load a TypeScript file. It reads
the text instead. Comments, string and template contents and regular-expression
literals are blanked first, so a word in a test's title or a comment is never counted;
a template's ``${...}`` stays code.

* A case is a call of ``it`` or ``test``, with any modifiers (``it.each(...)``,
  ``test.concurrent(...)``): one per declaration, not per table row.
* An expectation is a call of ``expect`` (``expect.soft`` too) or of node's
  ``assert`` (``assert(...)``, ``assert.equal(...)``). ``expect.any(...)`` and the
  other asymmetric matchers are not expectations.
* A skip is ``.skip``, ``.todo``, ``.skipIf`` or ``.runIf`` on ``it``, ``test`` or
  ``describe``, or a ``skip(...)`` call on a test's context.

A method of the same name (``/re/.test(s)``, ``x.it(...)``) is not counted. Cases a
shared kit makes from a table (``configTests(...)``) are not seen in the file that calls
it: a row taken from that table is for the review to read.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: The files counted: every platform test, whatever its runner.
PLATFORM_TEST = re.compile(r"^platform/(?:[^/]+/)*[^/]+\.test\.(?:ts|mts|mjs|js)$")

_NOT_AFTER = r"(?<![\w.$])"
_CASE = re.compile(_NOT_AFTER + r"(?:it|test)\b(?:\s*\.\s*\w+)*\s*[(`]")
_EXPECT = re.compile(_NOT_AFTER + r"(?:expect(?:\s*\.\s*soft)?|assert(?:\s*\.\s*\w+)?)\s*\(")
_SKIP = re.compile(
    _NOT_AFTER + r"(?:it|test|describe)(?:\s*\.\s*\w+)*?\s*\.\s*(?:skip|todo|skipIf|runIf)\b"
    r"|\.\s*skip\s*\("
)
#: After one of these (or at the start), a `/` opens a regular expression, not a division.
_REGEX_BEFORE = set("(,=:[!&|?{};+-*%<>~^")
_REGEX_WORDS = ("return", "typeof", "case", "do", "else", "in", "of", "void", "yield", "await")


def is_platform_test(path: str | None) -> bool:
    return bool(path) and PLATFORM_TEST.match(path) is not None


def code_only(text: str) -> str:
    """The text with comments, string contents and regex literals blanked to spaces.

    Newlines are kept, so the result lines up with the source. A template literal's
    ``${...}`` expressions stay code, nested to any depth.
    """
    out = list(text)
    n = len(text)
    i = 0
    #: For each open `${`, the brace depth at which it closes back into its template.
    templates: list[int] = []
    depth = 0

    def blank(start: int, end: int) -> None:
        for k in range(start, min(end, n)):
            if out[k] != "\n":
                out[k] = " "

    def last_code(before: int) -> str:
        k = before - 1
        while k >= 0 and out[k].isspace():
            k -= 1
        if k < 0:
            return ""
        if out[k].isalnum() or out[k] in "_$":
            start = k
            while start > 0 and (out[start - 1].isalnum() or out[start - 1] in "_$"):
                start -= 1
            return "".join(out[start:k + 1])
        return out[k]

    def template_body(start: int) -> int:
        """Blank a template's text from `start`; return where code resumes."""
        k = start
        while k < n:
            c = text[k]
            if c == "\\":
                k += 2
                continue
            if c == "`":
                blank(start, k)
                return k + 1
            if c == "$" and k + 1 < n and text[k + 1] == "{":
                blank(start, k)
                templates.append(depth)
                return k + 2
            k += 1
        blank(start, n)
        return n

    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if c == "/" and nxt == "/":
            end = text.find("\n", i)
            end = n if end < 0 else end
            blank(i, end)
            i = end
        elif c == "/" and nxt == "*":
            end = text.find("*/", i + 2)
            end = n if end < 0 else end + 2
            blank(i, end)
            i = end
        elif c in "'\"":
            k = i + 1
            while k < n and text[k] != c and text[k] != "\n":
                k += 2 if text[k] == "\\" else 1
            blank(i + 1, k)
            i = k + 1
        elif c == "`":
            i = template_body(i + 1)
        elif c == "/":
            before = last_code(i)
            if before == "" or before in _REGEX_BEFORE or before in _REGEX_WORDS:
                k = i + 1
                in_class = False
                while k < n and text[k] != "\n":
                    if text[k] == "\\":
                        k += 2
                        continue
                    if text[k] == "[":
                        in_class = True
                    elif text[k] == "]":
                        in_class = False
                    elif text[k] == "/" and not in_class:
                        break
                    k += 1
                blank(i + 1, k)
                i = k + 1
            else:
                i += 1
        elif c == "{":
            depth += 1
            i += 1
        elif c == "}":
            if templates and templates[-1] == depth:
                templates.pop()
                i = template_body(i + 1)
            else:
                depth -= 1
                i += 1
        else:
            i += 1
    return "".join(out)


@dataclass(frozen=True)
class Counts:
    cases: int = 0
    expects: int = 0
    skips: int = 0


def counts(text: str | None) -> Counts:
    if not text:
        return Counts()
    code = code_only(text)
    return Counts(
        cases=len(_CASE.findall(code)),
        expects=len(_EXPECT.findall(code)),
        skips=len(_SKIP.findall(code)),
    )


def tampering(before: Counts, after: Counts) -> list[str]:
    """Each way `after` holds less than `before`: fewer cases, else fewer expectations
    (a removed case takes its expectations with it), and more skips."""
    found: list[str] = []
    if after.cases < before.cases:
        found.append(f"cases removed ({before.cases} -> {after.cases})")
    elif after.expects < before.expects:
        found.append(f"expectations loosened ({before.expects} -> {after.expects})")
    if after.skips > before.skips:
        found.append(f"new skip or todo ({before.skips} -> {after.skips})")
    return found
