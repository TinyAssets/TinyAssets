from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "drain_review_gate.py"
WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github"
    / "workflows"
    / "auto-enroll-merge.yml"
)
POLICY_WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github"
    / "workflows"
    / "pr-scope-guard.yml"
)
HEAD = "a" * 40


def _run_gate(
    tmp_path: Path,
    *,
    branch: str,
    head: str = HEAD,
    body: str = "",
    require_receipt: bool = False,
) -> subprocess.CompletedProcess[str]:
    body_path = tmp_path / "body.md"
    body_path.write_text(body, encoding="utf-8")
    # `branch` and `require_receipt` are kept so the drain-era cases below read
    # the same, but neither reaches the CLI: every branch needs a receipt now.
    del branch, require_receipt
    cmd = [
        sys.executable,
        str(SCRIPT),
        "--head",
        head,
        "--body-file",
        str(body_path),
    ]
    return subprocess.run(cmd, text=True, capture_output=True, check=False)


def _valid_body(*, head: str = HEAD) -> str:
    # The receipt OPENS the body: a receipt is only read at the top, so that
    # nothing can be open in front of it and hide it.
    return (
        "Drain-Review-Verdict: APPROVE\n"
        f"Drain-Review-Head: {head}\n"
        "Drain-Review-Artifact: docs/audits/drain-review.md\n"
        "\n## Review\n"
    )


def test_an_ordinary_branch_without_a_receipt_is_denied(tmp_path: Path) -> None:
    """Every PR needs a receipt (2026-10-02): #4247 was armed and queued without one."""
    completed = _run_gate(tmp_path, branch="fix/ordinary")

    assert completed.returncode == 2
    assert completed.stdout.strip() == "deny"


def test_require_receipt_denies_ordinary_branch_without_receipt(tmp_path: Path) -> None:
    # Gate-defining file edits force the receipt regardless of branch name.
    completed = _run_gate(tmp_path, branch="fix/ordinary", require_receipt=True)

    assert completed.returncode == 2
    assert completed.stdout.strip() == "deny"


def test_require_receipt_allows_ordinary_branch_with_receipt(tmp_path: Path) -> None:
    completed = _run_gate(
        tmp_path, branch="fix/ordinary", body=_valid_body(), require_receipt=True
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == "allow"


def test_drain_branch_allows_one_matching_approval_receipt(tmp_path: Path) -> None:
    completed = _run_gate(
        tmp_path,
        branch="drain/run/target-001",
        body=_valid_body(),
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == "allow"


def test_drain_branch_denies_missing_or_stale_receipt(tmp_path: Path) -> None:
    missing = _run_gate(tmp_path, branch="drain/run/target-001")
    stale = _run_gate(
        tmp_path,
        branch="drain/run/target-001",
        body=_valid_body(head="b" * 40),
    )

    assert missing.returncode == 2
    assert stale.returncode == 2
    assert missing.stdout.strip() == "deny"
    assert stale.stdout.strip() == "deny"


def test_drain_branch_denies_duplicate_or_malformed_receipt(
    tmp_path: Path,
) -> None:
    # A duplicated line INSIDE the receipt block displaces the artifact line,
    # so the top three lines are not a receipt. (A duplicate further down the
    # body is simply never read — see
    # `test_a_contradiction_below_the_receipt_is_not_read`.)
    duplicate = _run_gate(
        tmp_path,
        branch="drain/run/target-001",
        body=(
            "Drain-Review-Verdict: APPROVE\n"
            f"Drain-Review-Head: {HEAD}\n"
            f"Drain-Review-Head: {HEAD}\n"
            "Drain-Review-Artifact: docs/audits/drain-review.md\n"
        ),
    )
    malformed = _run_gate(
        tmp_path,
        branch="drain/run/target-001",
        body=(
            "Drain-Review-Verdict: approve\n"
            f"Drain-Review-Head: {HEAD.upper()}\n"
            "Drain-Review-Artifact: local/private.txt\n"
        ),
    )
    # A malformed receipt ON TOP is not rescued by a valid one below it.
    malformed_plus_valid = _run_gate(
        tmp_path,
        branch="drain/run/target-001",
        body=(
            "Drain-Review-Verdict: DENY\n"
            + "Drain-Review-Head: malformed\n\n"
            + _valid_body()
        ),
    )

    assert duplicate.returncode == 2
    assert malformed.returncode == 2
    assert malformed_plus_valid.returncode == 2


def test_auto_enroll_reconciles_drain_review_on_head_and_body_changes() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "edited" in text
    assert "scripts/drain_review_gate.py" in text
    assert "headRefOid" in text
    assert "gh pr merge \"$PR\" --repo \"$REPO\" --disable-auto" in text
    assert "--match-head-commit \"$HEAD_OID\"" in text
    assert (
        "actions/checkout@11d5960a326750d5838078e36cf38b85af677262"
        in text
    )


def test_required_scope_check_fails_closed_on_unreviewed_drain_head() -> None:
    text = POLICY_WORKFLOW.read_text(encoding="utf-8")

    assert "pull_request_target:" in text
    assert "edited" in text
    assert (
        "actions/checkout@11d5960a326750d5838078e36cf38b85af677262"
        in text
    )
    assert "scripts/drain_review_gate.py --blocking-review" in text
    assert "--head \"${HEAD_OID}\"" in text
    assert "--body-file \"$RUNNER_TEMP/pr-body-gate.md\"" in text


# ---------------------------------------------------------------------------
# Blocking-review receipt (2026-09-26). PR #3989 auto-merged at the exact head
# its Tier 2 reviewer had BLOCKED: the verdict was a PR comment, and only a
# failing REQUIRED check holds a PR.
# ---------------------------------------------------------------------------

REPO = "TinyAssets/TinyAssets"
PR = 4242
ARTIFACT_URL = f"https://github.com/{REPO}/pull/{PR}#issuecomment-5841421637"
TRUSTED_COMMENTS = ((ARTIFACT_URL, "OWNER"),)


def _attestation(head: str = HEAD, verdict: str = "APPROVE") -> str:
    """What the reviewer POSTS as a comment: the verdict, bound to the head.

    The receipt OPENS the comment. Nothing may precede it — that is the rule
    that replaced the markdown scanner.
    """
    return (
        f"Drain-Review-Verdict: {verdict}\n"
        f"Drain-Review-Head: {head}\n"
        f"\n## Tier 2 review: {verdict}\n\n"
        "I read the diff and ran the tests.\n"
    )


def _receipt_body(*, head: str = HEAD, url: str = ARTIFACT_URL, verdict: str = "APPROVE") -> str:
    """What the stamper puts in the PR BODY: the same claim, plus the citation."""
    return (
        f"Drain-Review-Verdict: {verdict}\n"
        f"Drain-Review-Head: {head}\n"
        f"Drain-Review-Artifact: {url}\n"
        "\n## Review\n\nStamped after the Tier 2 pass.\n"
    )


def _run_blocking(
    tmp_path: Path,
    *,
    body: str = "",
    head: str = HEAD,
    repo: str = REPO,
    pr: int = PR,
    comments: tuple[tuple[str, ...], ...] | None = TRUSTED_COMMENTS,
    comments_raw: str | None = None,
) -> subprocess.CompletedProcess[str]:
    body_path = tmp_path / "body.md"
    body_path.write_text(body, encoding="utf-8")
    comments_path = tmp_path / "comments.ndjson"
    if comments_raw is not None:
        comments_path.write_text(comments_raw, encoding="utf-8")
    elif comments is not None:
        # Exactly what `gh api --jq '.[] | {url, association, body}'` emits: one
        # compact JSON object per line, the body's newlines JSON-escaped. A row
        # may omit the body, in which case it attests APPROVE at this head — so
        # a test about URL identity or association is not also a body test.
        comments_path.write_text(
            "".join(
                json.dumps(
                    {
                        "url": row[0],
                        "association": row[1],
                        "body": row[2] if len(row) > 2 else _attestation(head),
                    }
                )
                + "\n"
                for row in comments
            ),
            encoding="utf-8",
        )
    cmd = [
        sys.executable,
        str(SCRIPT),
        "--blocking-review",
        "--head",
        head,
        "--body-file",
        str(body_path),
        "--review-repo",
        repo,
        "--review-pr",
        str(pr),
        "--review-comments-file",
        str(comments_path),
    ]
    return subprocess.run(cmd, text=True, capture_output=True, check=False)


def test_any_pr_without_a_receipt_is_denied_naming_what_is_missing(tmp_path: Path) -> None:
    # No receipt in the body, so the required check fails -- for every PR, not
    # only gate-defining or authority paths (2026-10-02). The reason names the
    # head a receipt must carry, so a blocked builder knows what to stamp.
    completed = _run_blocking(tmp_path)

    assert completed.returncode == 2
    assert completed.stdout.strip() == "deny"
    assert f"every PR needs a Drain-Review receipt for head {HEAD}" in completed.stderr


def test_a_valid_receipt_unblocks_the_pr(tmp_path: Path) -> None:
    completed = _run_blocking(tmp_path, body=_receipt_body())

    assert completed.returncode == 0
    assert completed.stdout.strip() == "allow"
    # The requirement is still reported, so the PR says what had to be carried.
    assert "every PR needs a Drain-Review receipt" in completed.stderr


@pytest.mark.parametrize(
    "body,why",
    [
        (_receipt_body(verdict="BLOCK"), "a BLOCK verdict can never satisfy the gate"),
        (_receipt_body(verdict="DENY"), "nor any other word"),
        (_receipt_body(verdict="approve"), "nor lower-case approve"),
        (_receipt_body(verdict="APPROVE with reservations"), "nor APPROVE plus prose"),
        (_receipt_body(head="b" * 40), "a receipt for another head is stale"),
        (_receipt_body(head=HEAD.upper()), "the head must be lower-case hex"),
        ("", "no receipt at all"),
        (
            f"Drain-Review-Verdict: BLOCK\nDrain-Review-Head: {HEAD}\n"
            f"Drain-Review-Artifact: {ARTIFACT_URL}\n\n" + _receipt_body(),
            "a visible BLOCK on top cannot be overridden by an APPROVE below it",
        ),
    ],
)
def test_mutating_the_verdict_or_head_fails_closed(tmp_path: Path, body: str, why: str) -> None:
    completed = _run_blocking(tmp_path, body=body)

    assert completed.returncode == 2, why
    assert completed.stdout.strip() == "deny"


@pytest.mark.parametrize(
    "url,why",
    [
        ("docs/audits/drain-review.md", "a docs path is not a comment on this PR"),
        (
            f"https://github.com/{REPO}/pull/{PR + 1}#issuecomment-5841421637",
            "a comment on a DIFFERENT PR",
        ),
        (
            f"https://github.com/someone/else/pull/{PR}#issuecomment-5841421637",
            "a comment in a different repository",
        ),
        (f"https://github.com/{REPO}/pull/{PR}#issuecomment-1", "a comment id that does not exist"),
        (f"https://github.com/{REPO}/pull/{PR}", "the PR itself, with no comment anchor"),
        (
            f"https://github.com/{REPO}/commit/{'c' * 40}",
            "a commit URL",
        ),
        (
            f"https://github.com/{REPO}/pull/{PR}#issuecomment-5841421637 (approved)",
            "trailing prose on the artifact line",
        ),
        (
            f"https://evil.example/{REPO}/pull/{PR}#issuecomment-5841421637",
            "a look-alike host",
        ),
    ],
)
def test_artifact_must_name_a_real_comment_on_this_pr(tmp_path: Path, url: str, why: str) -> None:
    completed = _run_blocking(
        tmp_path, body=_receipt_body(url=url)
    )

    assert completed.returncode == 2, why
    assert completed.stdout.strip() == "deny"


@pytest.mark.parametrize(
    "anchor",
    ["issuecomment-5841421637", "pullrequestreview-991234", "discussion_r778899"],
)
def test_a_verdict_may_live_in_a_comment_review_or_review_comment(
    tmp_path: Path, anchor: str
) -> None:
    url = f"https://github.com/{REPO}/pull/{PR}#{anchor}"
    completed = _run_blocking(
        tmp_path,
        body=_receipt_body(url=url),
        comments=((url, "OWNER"),),
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == "allow"


def test_repo_casing_in_the_artifact_url_is_tolerated(tmp_path: Path) -> None:
    # GitHub resolves owner/repo case-insensitively; refusing a stamper who
    # typed a different casing would be a wall, not a gate.
    url = f"https://github.com/tinyassets/tinyassets/pull/{PR}#issuecomment-5841421637"
    completed = _run_blocking(
        tmp_path,
        body=_receipt_body(url=url),
        comments=((url.replace("tinyassets/tinyassets", REPO), "OWNER"),),
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == "allow"


@pytest.mark.parametrize(
    "association",
    ["CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR", "NONE", "MANNEQUIN", ""],
)
def test_an_untrusted_commenter_cannot_supply_the_artifact(
    tmp_path: Path, association: str
) -> None:
    # Anyone can comment on a public repo's PR. Trust comes from GitHub's
    # author_association, read from the API, never from the comment body.
    completed = _run_blocking(
        tmp_path,
        body=_receipt_body(),
        comments=((ARTIFACT_URL, association),),
    )

    assert completed.returncode == 2
    assert completed.stdout.strip() == "deny"


@pytest.mark.parametrize("association", ["OWNER", "MEMBER", "COLLABORATOR"])
def test_write_side_associations_may_supply_the_artifact(
    tmp_path: Path, association: str
) -> None:
    completed = _run_blocking(
        tmp_path,
        body=_receipt_body(),
        comments=((ARTIFACT_URL, association),),
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == "allow"


@pytest.mark.parametrize(
    "comments_raw,why",
    [
        (None, "the comment API read failed, so the workflow removed the file"),
        ("", "an empty inventory cannot corroborate anything"),
        ("not json at all\n", "unparseable inventory"),
        (
            json.dumps({"url": ARTIFACT_URL, "association": "OWNER"}) + "\n{oops",
            "a trailing partial object means we did not read the whole inventory",
        ),
        (
            json.dumps({"url": ARTIFACT_URL}) + "\n",
            "a row with no association cannot be trusted",
        ),
        (
            json.dumps([{"url": ARTIFACT_URL, "association": "OWNER"}]) + "\n",
            "an array where objects were expected",
        ),
    ],
)
def test_an_uncorroborated_receipt_fails_closed(
    tmp_path: Path, comments_raw: str | None, why: str
) -> None:
    completed = _run_blocking(
        tmp_path,
        body=_receipt_body(),
        comments=None,
        comments_raw=comments_raw,
    )

    assert completed.returncode == 2, why
    assert completed.stdout.strip() == "deny"


def test_pretty_printed_inventory_still_parses(tmp_path: Path) -> None:
    # raw_decode, not a line split: if gh ever stops emitting compact objects
    # the gate must keep reading them rather than silently see an empty set.
    completed = _run_blocking(
        tmp_path,
        body=_receipt_body(),
        comments=None,
        comments_raw=json.dumps(
            {"url": ARTIFACT_URL, "association": "OWNER", "body": _attestation()}, indent=2
        )
        + "\n",
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == "allow"


HIDDEN_RECEIPT_BODIES = {
    "html comment": (
        "VERDICT: BLOCK. Do not merge.\n\n"
        "<!--\n"
        "Drain-Review-Verdict: APPROVE\n"
        f"Drain-Review-Head: {HEAD}\n"
        f"Drain-Review-Artifact: {ARTIFACT_URL}\n"
        "-->\n"
    ),
    "unclosed html comment": (
        "VERDICT: BLOCK. Do not merge.\n\n"
        "<!--\n"
        "Drain-Review-Verdict: APPROVE\n"
        f"Drain-Review-Head: {HEAD}\n"
        f"Drain-Review-Artifact: {ARTIFACT_URL}\n"
    ),
    "fenced 'what NOT to do' example": (
        "Never write this:\n\n"
        "```\n"
        "Drain-Review-Verdict: APPROVE\n"
        f"Drain-Review-Head: {HEAD}\n"
        f"Drain-Review-Artifact: {ARTIFACT_URL}\n"
        "```\n"
    ),
    "tilde fence": (
        "~~~text\n"
        "Drain-Review-Verdict: APPROVE\n"
        f"Drain-Review-Head: {HEAD}\n"
        f"Drain-Review-Artifact: {ARTIFACT_URL}\n"
        "~~~\n"
    ),
    "unclosed fence": (
        "```\n"
        "Drain-Review-Verdict: APPROVE\n"
        f"Drain-Review-Head: {HEAD}\n"
        f"Drain-Review-Artifact: {ARTIFACT_URL}\n"
    ),
    "details block": (
        "<details><summary>receipt format</summary>\n\n"
        "Drain-Review-Verdict: APPROVE\n"
        f"Drain-Review-Head: {HEAD}\n"
        f"Drain-Review-Artifact: {ARTIFACT_URL}\n"
        "\n</details>\n"
    ),
    "unclosed details block": (
        "<details><summary>receipt format</summary>\n\n"
        "Drain-Review-Verdict: APPROVE\n"
        f"Drain-Review-Head: {HEAD}\n"
        f"Drain-Review-Artifact: {ARTIFACT_URL}\n"
    ),
    # Round 2 of the same review: a NESTED details closed the outer one,
    # because the substitution was not recursive. The receipt is still behind
    # the outer toggle.
    "nested details block": (
        "<details><summary>outer</summary>\n"
        "<details>inner</details>\n\n"
        "Drain-Review-Verdict: APPROVE\n"
        f"Drain-Review-Head: {HEAD}\n"
        f"Drain-Review-Artifact: {ARTIFACT_URL}\n"
        "</details>\n"
    ),
    "details in capitals": (
        "<DETAILS><SUMMARY>x</SUMMARY>\n\n"
        "Drain-Review-Verdict: APPROVE\n"
        f"Drain-Review-Head: {HEAD}\n"
        f"Drain-Review-Artifact: {ARTIFACT_URL}\n"
        "</DETAILS>\n"
    ),
    # CommonMark lazy continuation: an unprefixed line under a `>` line is
    # still inside the quote, so this renders as somebody ELSE's approval being
    # quoted and disagreed with.
    "lazy blockquote continuation": (
        "> Someone else proposed this; I disagree:\n"
        "Drain-Review-Verdict: APPROVE\n"
        f"Drain-Review-Head: {HEAD}\n"
        f"Drain-Review-Artifact: {ARTIFACT_URL}\n"
    ),
    "explicitly quoted receipt": (
        "> Drain-Review-Verdict: APPROVE\n"
        f"> Drain-Review-Head: {HEAD}\n"
        f"> Drain-Review-Artifact: {ARTIFACT_URL}\n"
    ),
    "four-backtick fence closed with three": (
        "````\n"
        "Drain-Review-Verdict: APPROVE\n"
        f"Drain-Review-Head: {HEAD}\n"
        f"Drain-Review-Artifact: {ARTIFACT_URL}\n"
        "```\n"
    ),
    # The receipt sits AFTER a SHORTER fence marker, which per CommonMark does
    # not close the longer one. Put it before, as the row above does, and the
    # receipt is hidden whatever the length rule says — so that row cannot
    # detect a broken length comparison and this one can.
    "shorter marker does not close a longer fence": (
        "````\n"
        "an example\n"
        "```\n"
        "Drain-Review-Verdict: APPROVE\n"
        f"Drain-Review-Head: {HEAD}\n"
        f"Drain-Review-Artifact: {ARTIFACT_URL}\n"
        "````\n"
    ),
}


@pytest.mark.parametrize("why", sorted(HIDDEN_RECEIPT_BODIES))
def test_a_receipt_below_the_top_of_the_body_is_not_read(tmp_path: Path, why: str) -> None:
    """Every hiding place three review rounds found, refused by ONE rule.

    Each body below puts an approval somewhere a reader does not plainly see it:
    an HTML comment (which GitHub renders as nothing) under a visible
    `VERDICT: BLOCK. Do not merge.`; a fenced "what NOT to do" example; a
    `<details>` toggle; a nested `<details>` that closed the outer one; a lazily
    continued blockquote; a list-nested quote. The markdown scanner that chased
    them individually lost a defect to each of three rounds. A receipt is now
    read ONLY at the top of the text, where nothing can be open in front of it,
    so every one of these fails for the same reason: the first non-blank line is
    not the verdict.
    """
    completed = _run_blocking(
        tmp_path, body=HIDDEN_RECEIPT_BODIES[why]
    )

    assert completed.returncode == 2, why
    assert completed.stdout.strip() == "deny"


@pytest.mark.parametrize("why", sorted(HIDDEN_RECEIPT_BODIES))
def test_the_same_hiding_place_in_the_cited_comment_is_refused(tmp_path: Path, why: str) -> None:
    # The comment side of the same rule, so the attestation cannot be hidden
    # either. The body carries an honest receipt; only the comment is suspect.
    completed = _run_blocking(
        tmp_path,
        body=_receipt_body(),
        comments=((ARTIFACT_URL, "OWNER", HIDDEN_RECEIPT_BODIES[why]),),
    )

    assert completed.returncode == 2, why
    assert completed.stdout.strip() == "deny"


TRAILING_CONTENT = {
    "a fenced example of this very format": (
        "\n```\nDrain-Review-Verdict: APPROVE\n"
        "Drain-Review-Head: <the PR's current head>\n"
        "Drain-Review-Artifact: <comment URL>\n```\n"
    ),
    "an unclosed fence": "\n```\nnever closed\n",
    "an unclosed html comment": "\n<!--\nnever closed\n",
    "a nested details block": "\n<details><summary>a</summary>\n<details>b</details>\n</details>\n",
    "a lazily continued blockquote": "\n> quoted\nlazy continuation\n",
    "a list-nested quote": "\n- > quoted inside a list\n  still quoted\n",
    "a heading right after a quote": "\n> prior reviewer\n## Final review\n",
    "a fence marker inside an html block": "\n<details>\n```\n</details>\n",
    "a markdown table": "\n| a | b |\n|---|---|\n| 1 | 2 |\n",
    "an indented code block": "\n    Drain-Review-Verdict: BLOCK\n",
    "CRLF throughout": "\r\n## notes\r\n\r\nall fine\r\n",
}


@pytest.mark.parametrize("why", sorted(TRAILING_CONTENT))
def test_anything_may_follow_the_receipt(tmp_path: Path, why: str) -> None:
    """The anti-wall half, and the payoff for dropping the markdown scanner.

    Over-blocking is a wall, and the scanner produced three of them: an honest
    receipt was refused after a heading, after a fence marker inside an HTML
    block, and after a literal `<!--` in a code example. Because only the top of
    the text is read now, NOTHING that follows the receipt can void it — every
    construct that used to matter is listed here, including the three that broke
    it, plus a `<details>` a reviewer would fold evidence into and a fenced copy
    of this very format.
    """
    completed = _run_blocking(
        tmp_path,
        body=_receipt_body() + TRAILING_CONTENT[why],
        comments=((ARTIFACT_URL, "OWNER", _attestation() + TRAILING_CONTENT[why]),),
    )

    assert completed.returncode == 0, why
    assert completed.stdout.strip() == "allow"


@pytest.mark.parametrize(
    "body,why",
    [
        (
            f"Drain-Review-Verdict: APPROVE\nDrain-Review-Head: {HEAD}\n",
            "the body attests but cites nothing: there are only two lines",
        ),
        (
            f"Drain-Review-Verdict: APPROVE\nDrain-Review-Head: {HEAD}\n\n## notes\n",
            "the third non-blank line is prose, not an artifact",
        ),
        ("Drain-Review-Verdict: APPROVE\n", "one line"),
        ("", "no body at all"),
    ],
)
def test_the_body_needs_all_three_lines_and_denies_cleanly_without_them(
    tmp_path: Path, body: str, why: str
) -> None:
    # Cleanly, not by IndexError: the artifact is read positionally, so the
    # count check has to happen before the subscript. A crash would still fail
    # the step, but the gate must say why.
    completed = _run_blocking(tmp_path, body=body)

    assert completed.returncode == 2, why
    assert completed.stdout.strip() == "deny", why
    assert "Traceback" not in completed.stderr, why


def test_a_contradiction_below_the_receipt_is_not_read(tmp_path: Path) -> None:
    """A deliberate semantic change from the whole-text scan, stated openly.

    Only the top of the text is authoritative, so a `BLOCK` line further down no
    longer refuses. That is not a weakening: the direction that matters — a
    visible refusal at the top, with an approval hidden below — still denies, and
    is pinned in `test_mutating_the_verdict_or_head_fails_closed` and in every
    `HIDDEN_RECEIPT_BODIES` row. What is lost is refusing a text a reader would
    read as an approval, because the approval is the first thing in it.
    """
    trailing_block = f"\nDrain-Review-Verdict: BLOCK\nDrain-Review-Head: {HEAD}\n"
    completed = _run_blocking(
        tmp_path,
        body=_receipt_body() + trailing_block,
        comments=((ARTIFACT_URL, "OWNER", _attestation() + trailing_block),),
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == "allow"


@pytest.mark.parametrize(
    "prefix,why",
    [
        ("\n\n", "leading blank lines are skipped"),
        ("\r\n\r\n", "leading CRLF blank lines"),
        ("   \n", "a whitespace-only first line"),
    ],
)
def test_only_blank_lines_may_precede_the_receipt(tmp_path: Path, prefix: str, why: str) -> None:
    # Blank lines cannot hide anything, and an editor or a paste often adds one.
    completed = _run_blocking(
        tmp_path, body=prefix + _receipt_body()
    )

    assert completed.returncode == 0, why
    assert completed.stdout.strip() == "allow"


@pytest.mark.parametrize(
    "line,why",
    [
        ("Drain-Review-Verdict: APPROVE   ", "trailing spaces are tolerated"),
        ("Drain-Review-Verdict: APPROVE\t", "a trailing tab"),
    ],
)
def test_trailing_whitespace_does_not_void_a_receipt(tmp_path: Path, line: str, why: str) -> None:
    # Trailing whitespace hides nothing, so refusing it would only be a trap.
    body = f"{line}\nDrain-Review-Head: {HEAD}\nDrain-Review-Artifact: {ARTIFACT_URL}\n"
    completed = _run_blocking(tmp_path, body=body)

    assert completed.returncode == 0, why
    assert completed.stdout.strip() == "allow"


@pytest.mark.parametrize(
    "comment_body,why",
    [
        (
            _attestation(verdict="BLOCK"),
            "a trusted author's BLOCK is not an approval, however it is cited",
        ),
        (
            "## Tier 2 review: APPROVE\n\nLooks good to me.\n",
            "prose approval with no machine-readable attestation",
        ),
        (
            _attestation(head="b" * 40),
            "an approval of a DIFFERENT head — the stale-comment gap",
        ),
        (
            "<!--\nDrain-Review-Verdict: APPROVE\n" + f"Drain-Review-Head: {HEAD}\n-->\n",
            "an attestation hidden in an HTML comment is not published",
        ),
        (
            "```\nDrain-Review-Verdict: APPROVE\n" + f"Drain-Review-Head: {HEAD}\n```\n",
            "a fenced example in a comment is not an attestation",
        ),
        (
            "<details><summary>outer</summary>\n<details>inner</details>\n\n"
            f"Drain-Review-Verdict: APPROVE\nDrain-Review-Head: {HEAD}\n</details>\n",
            "nested details in the comment, behind the outer toggle",
        ),
        (
            "> Someone else proposed this; I disagree:\n"
            f"Drain-Review-Verdict: APPROVE\nDrain-Review-Head: {HEAD}\n",
            "a lazily-continued blockquote: a quoted approval is not this "
            "reviewer's approval",
        ),
        (
            _attestation(verdict="BLOCK") + "\n" + _attestation(),
            "a comment opening with BLOCK is a refusal, whatever follows it",
        ),
        ("", "an empty comment body"),
    ],
)
def test_the_cited_comment_must_itself_publish_the_approval(
    tmp_path: Path, comment_body: str, why: str
) -> None:
    """Cross-family review 2026-09-26, finding 6 (and 7).

    Comment identity was not enough: any trusted-author comment satisfied the
    artifact, so a body receipt citing an OWNER comment that said
    `VERDICT: BLOCK` for the exact head got through. The comment must attest
    APPROVE at the CURRENT head — which also closes the stale-comment gap
    without consulting any clock.
    """
    completed = _run_blocking(
        tmp_path,
        body=_receipt_body(),
        comments=((ARTIFACT_URL, "OWNER", comment_body),),
    )

    assert completed.returncode == 2, why
    assert completed.stdout.strip() == "deny"


def test_the_body_and_the_comment_must_agree_on_the_head(tmp_path: Path) -> None:
    # Both halves name the same head, and it is the PR's current head: allow.
    # Change either half alone and it refuses.
    agreeing = _run_blocking(
        tmp_path,
        body=_receipt_body(),
        comments=((ARTIFACT_URL, "OWNER", _attestation(HEAD)),),
    )
    body_stale = _run_blocking(
        tmp_path,
        body=_receipt_body(head="b" * 40),
        comments=((ARTIFACT_URL, "OWNER", _attestation(HEAD)),),
    )
    comment_stale = _run_blocking(
        tmp_path,
        body=_receipt_body(),
        comments=((ARTIFACT_URL, "OWNER", _attestation("b" * 40)),),
    )

    assert agreeing.returncode == 0
    assert agreeing.stdout.strip() == "allow"
    assert body_stale.returncode == 2
    assert comment_stale.returncode == 2


def test_a_second_trusted_comment_can_carry_the_approval(tmp_path: Path) -> None:
    # A review thread normally holds several comments, most of them not the
    # verdict. The gate must find the one that attests, not demand the PR have
    # exactly one comment.
    other = f"https://github.com/{REPO}/pull/{PR}#issuecomment-1111111111"
    completed = _run_blocking(
        tmp_path,
        body=_receipt_body(),
        comments=(
            (other, "OWNER", "Round 1: BLOCK, see below.\n"),
            (ARTIFACT_URL, "OWNER", _attestation()),
        ),
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == "allow"


def test_citing_the_wrong_comment_of_two_fails(tmp_path: Path) -> None:
    # The accidental-citation case from finding 6: the approval exists, but the
    # body points at the BLOCK comment instead.
    blocked = f"https://github.com/{REPO}/pull/{PR}#issuecomment-1111111111"
    completed = _run_blocking(
        tmp_path,
        body=_receipt_body(url=blocked),
        comments=(
            (blocked, "OWNER", _attestation(verdict="BLOCK")),
            (ARTIFACT_URL, "OWNER", _attestation()),
        ),
    )

    assert completed.returncode == 2
    assert completed.stdout.strip() == "deny"


def _workflow_regex(name: str) -> str:
    text = POLICY_WORKFLOW.read_text(encoding="utf-8")
    match = re.search(rf"^\s*{name}='(?P<pattern>.+)'\s*$", text, re.MULTILINE)
    assert match, f"{name} is gone from {POLICY_WORKFLOW.name}"
    return match.group("pattern")


def test_the_scope_declaration_set_is_what_the_label_rule_reads() -> None:
    """SENSITIVE_RE still decides which PRs need the infra-change label and
    which files count toward the hard caps, so its membership is pinned here
    (Codex round 2 on #4255: deleting the narrow receipt set also deleted the
    only tests of this regex)."""
    sensitive = re.compile(_workflow_regex("SENSITIVE_RE"))
    for path in (
        ".github/workflows/tests.yml",
        ".github/workflows/pr-scope-guard.yml",
        ".github/workflows/deploy-prod.yml",
        ".github/known-failing-tests.txt",
        ".github/heavy-test-files.txt",
        "scripts/ci_required_tests.py",
        "scripts/ci_structural_guards.py",
        "scripts/drain_review_gate.py",
        "deploy/install-host-uptime-services.sh",
        "Dockerfile",
        ".dockerignore",
    ):
        assert sensitive.match(path), f"{path} must stay release-critical"
    for path in (
        "scripts/check_context_budget.py",
        "tests/test_rulebook_ratchet.py",
        "tinyassets/auth/provider.py",
        "docs/reference/executable-gates.md",
        "packaging/claude-plugin/build_plugin.py",
    ):
        assert not sensitive.match(path), f"{path} must not newly need the label"


def _extract(pattern: str) -> str:
    """The workflow's own lines, so this test cannot drift from what runs."""
    text = POLICY_WORKFLOW.read_text(encoding="utf-8")
    match = re.search(pattern, text, re.MULTILINE)
    assert match, f"{pattern!r} is gone from {POLICY_WORKFLOW.name}"
    return match.group(0).strip()


@pytest.mark.parametrize(
    "grep_rc,expect_rc,why",
    [
        (0, 0, "matches found"),
        (1, 0, "no match is a normal answer"),
        (2, 1, "grep failed to execute: must NOT read as 'nothing matched'"),
        (141, 1, "killed by a signal"),
    ],
)
def test_a_grep_failure_never_reads_as_no_release_critical_paths(
    tmp_path: Path, grep_rc: int, expect_rc: int, why: str
) -> None:
    """Cross-family review 2026-09-26, finding 3.

    `grep -E "$SENSITIVE_RE" || true` swallowed an execution error as well as
    "no match", so an injected exit 2 produced an empty hit list — fail-OPEN on
    the exact question this gate answers. Runs the workflow's own `classify`
    lines against a stub grep.
    """
    bash = shutil.which("bash")
    if bash is None:  # pragma: no cover - CI runners all have bash
        pytest.skip("bash is required to exercise the workflow's own lines")

    # A shell FUNCTION, not a stub on PATH: a Windows `C:/...` directory cannot
    # go in a bash PATH (the drive colon is the separator), and the first
    # attempt silently ran the real grep and passed for the wrong reason.
    # Functions take precedence over PATH lookup inside the same shell.
    script = "\n".join(
        [
            "set -euo pipefail",
            "grep() {",
            f"  if [ {grep_rc} -eq 0 ]; then echo 'deploy/x.sh'; fi",
            f"  return {grep_rc}",
            "}",
            _extract(r"^\s*classify\(\) \{\n(?:.*\n)*?^\s*\}$"),
            _extract(r'^\s*HITS="\$\(classify .*$'),
            'echo "HITS=${HITS}"',
        ]
    )
    script_path = tmp_path / "grep-fragment.sh"
    script_path.write_text(script, encoding="utf-8", newline="\n")

    completed = subprocess.run(
        [bash, str(script_path)],
        text=True,
        capture_output=True,
        check=False,
        env={
            **os.environ,
            "CHANGED": "deploy/x.sh\ndocs/readme.md",
            "SENSITIVE_RE": "^deploy/",
        },
    )

    assert completed.returncode == expect_rc, f"{why}: {completed.stderr}"
    if expect_rc == 0:
        # And the hits it reports are grep's output, not something invented.
        expected = "deploy/x.sh" if grep_rc == 0 else ""
        assert completed.stdout.strip() == f"HITS={expected}", why
    else:
        assert "failing closed" in completed.stderr


def test_the_blocked_summary_tells_a_human_exactly_what_to_do(tmp_path: Path) -> None:
    """The gate's human-facing output, rendered by running its own lines.

    This is the only thing a blocked stamper reads, and nothing else asserts it.
    It must substitute the REAL head (a placeholder would send them to stamp the
    wrong sha), give both steps, and state the placement rule — every one of the
    three cross-family rounds produced a wall, and "MOVE IT UP" is the recovery.
    """
    bash = shutil.which("bash")
    if bash is None:  # pragma: no cover - CI runners all have bash
        pytest.skip("bash is required to exercise the workflow's own lines")

    lines = POLICY_WORKFLOW.read_text(encoding="utf-8").splitlines()
    anchor = next(
        i for i, ln in enumerate(lines) if "Blocked — no Drain-Review receipt for head" in ln
    )
    start = next(i for i in range(anchor, 0, -1) if lines[i].strip() == "{")
    end = next(i for i in range(anchor, len(lines)) if '} >> "$GITHUB_STEP_SUMMARY"' in lines[i])
    indent = len(lines[start]) - len(lines[start].lstrip())
    block = "\n".join(
        ln[indent:] if ln.startswith(" " * indent) else ln for ln in lines[start : end + 1]
    )

    head = "1" * 40
    (tmp_path / "receipt-why.txt").write_text(
        "because the title declares Tier 2\n", encoding="utf-8"
    )
    script = tmp_path / "summary.sh"
    script.write_text(
        "set -euo pipefail\n" + block.replace('>> "$GITHUB_STEP_SUMMARY"', '>> "$OUT"') + "\n",
        encoding="utf-8",
        newline="\n",
    )
    completed = subprocess.run(
        [bash, str(script)],
        text=True,
        capture_output=True,
        check=False,
        env={
            **os.environ,
            "RUNNER_TEMP": str(tmp_path),
            "OUT": str(tmp_path / "summary.md"),
            "HEAD_OID": head,
            "DIFF_KEY": "f" * 64,
            "REPO": REPO,
            "PR": str(PR),
            "HIT_COUNT": "2",
            "LABELS": "",
        },
    )

    assert completed.returncode == 0, completed.stderr
    summary = (tmp_path / "summary.md").read_text(encoding="utf-8")
    # COUNTED, not `in`: the head line appears once per step, so an `in` check
    # was satisfied by step 2 while step 1 printed a placeholder. That is the
    # fifth assertion in this file that a different occurrence rescued.
    assert summary.count(f"Drain-Review-Head: {head}") == 2, (
        "both steps must show the real head, or the stamper signs the wrong sha"
    )
    assert f"https://github.com/{REPO}/pull/{PR}#issuecomment-<id>" in summary
    assert f"no Drain-Review receipt for head `{head}` (diff key `{'f' * 64}`)" in summary, (
        "the heading must name exactly what is missing"
    )
    assert "because the title declares Tier 2" in summary, "the reason must reach the reader"
    assert "FIRST two non-blank lines" in summary
    assert "FIRST three non-blank lines" in summary
    assert "MOVE IT UP" in summary, "a refused honest receipt needs the recovery step"
    # Both complaints at once: an undeclared release-critical PR also hears about
    # the label, instead of discovering it on the next round.
    assert "infra-change" in summary


def test_scope_guard_wires_the_blocking_review_decision() -> None:
    text = POLICY_WORKFLOW.read_text(encoding="utf-8")

    # Re-trigger events: stamping the BODY must re-run the check without a push,
    # and labelling must too. Asserted as the LIST, not as substrings: `labeled`
    # is a substring of `unlabeled`, so deleting `labeled` alone survived a
    # per-event `in text` check (cross-family review 2026-09-26).
    types = re.search(r"^\s*types: \[(?P<types>[^\]]*)\]", text, re.MULTILINE)
    assert types, "the pull_request_target types list is gone"
    declared = {t.strip() for t in types.group("types").split(",")}
    assert declared == {
        "opened",
        "reopened",
        "synchronize",
        "ready_for_review",
        "edited",
        "labeled",
        "unlabeled",
    }, declared
    # WHO needs a receipt is unchanged: the title and the labels are not inputs
    # to the decision. Those triggers were built and cut, so no plumbing for them
    # may come back without a deliberate edit here.
    assert "PR_TITLE" not in text, "the Tier 2 title trigger was cut"
    assert "--review-title" not in text, "the Tier 2 title trigger was cut"
    assert "--review-labels" not in text, "the infra-change trigger was cut"
    # The mode flag must REACH python, not merely exist in the file. Deleting
    # the array expansion from the invocation survived a bare
    # `"--blocking-review" in text` check (cross-family review round 2).
    assert "if ! python scripts/drain_review_gate.py --blocking-review \\" in text, (
        "the blocking-review decision must run for EVERY PR, not behind a footprint test"
    )
    for gone in ("RECEIPT_HITS", "AUTHORITY_RE", "GATE_RE", "--review-footprint-exempt",
                 "--review-hits-file", "authority_behavior_check"):
        assert gone not in text, f"{gone}: the narrow receipt set is deleted, not bypassed"
    for flag in (
        '--review-repo "${REPO}"',
        '--review-pr "${PR}"',
        '--review-comments-file "$COMMENTS_FILE"',
    ):
        assert flag in text, flag
    # The inventory is read from the API, for all three places a verdict lands.
    for endpoint in ('"issues/${PR}/comments"', '"pulls/${PR}/comments"', '"pulls/${PR}/reviews"'):
        assert endpoint in text, endpoint
    # The PROJECTION, not the word: `assert "author_association" in text` also
    # matched the comment prose explaining it, so replacing `.author_association`
    # with the literal "OWNER" — which would trust every commenter — stayed green
    # (cross-family review 2026-09-26). The body must be projected too, or the
    # gate cannot tell an approval from a BLOCK.
    assert (
        "--jq '.[] | {url: .html_url, association: .author_association, "
        'body: (.body // "")}\'' in text
    ), "the inventory must carry GitHub's own association AND the comment body"
    # A failed comment read must not fail an unrelated PR, but must leave no
    # inventory behind for one that needs a receipt. Asserted as the ORDERED
    # sequence inside the loop: the bare string also appears before the loop,
    # so a looser check stayed green when the in-loop removal was deleted.
    assert re.search(
        r'rm -f "\$COMMENTS_FILE"\s*\n\s*break\s*\n\s*fi\s*\n\s*done',
        text,
    ), "a partial inventory must be discarded, not read as the complete one"


# ---- diff-keyed receipts ---------------------------------------------------
#
# A `Drain-Review-Diff:` receipt binds the approval to the CHANGE, so merging
# main in or rebasing does not void it (every push used to), while any change to
# what is being merged does.


def _gate_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("drain_review_gate_diffkey", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Repo:
    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.invalid")
        self.git("config", "user.name", "t")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "core.autocrlf", "false")

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=self.root, capture_output=True, text=True, check=True
        ).stdout.strip()

    def commit(self, files: dict[str, bytes | None], message: str) -> str:
        for name, content in files.items():
            path = self.root / name
            if content is None:
                path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def key(self, head: str = "pr") -> str:
        return _gate_module().diff_key("main", head, cwd=self.root)


@pytest.fixture()
def pr_repo(tmp_path: Path) -> _Repo:
    repo = _Repo(tmp_path / "repo")
    repo.commit({"a.txt": b"a\n", "b.txt": b"b\n", "bin.dat": b"\x00\x01"}, "base")
    repo.git("checkout", "-q", "-b", "pr")
    repo.commit({"a.txt": b"a changed by the PR\n"}, "pr change")
    return repo


def test_diff_key_survives_merging_an_unrelated_main_change(pr_repo: _Repo) -> None:
    before = pr_repo.key()
    pr_repo.git("checkout", "-q", "main")
    pr_repo.commit({"b.txt": b"b changed on main\n"}, "main moves")
    pr_repo.git("checkout", "-q", "pr")
    pr_repo.git("merge", "-q", "--no-edit", "main")
    assert pr_repo.key() == before


def test_diff_key_survives_a_rebase_onto_an_unrelated_main_change(pr_repo: _Repo) -> None:
    before = pr_repo.key()
    pr_repo.git("checkout", "-q", "main")
    pr_repo.commit({"c.txt": b"new on main\n"}, "main moves")
    pr_repo.git("checkout", "-q", "pr")
    pr_repo.git("rebase", "-q", "main")
    assert pr_repo.key() == before


def test_diff_key_changes_when_the_pr_change_changes(pr_repo: _Repo) -> None:
    before = pr_repo.key()
    pr_repo.commit({"a.txt": b"a changed by the PR, differently\n"}, "edit")
    assert pr_repo.key() != before


def test_diff_key_changes_when_main_touched_a_file_the_pr_changes(pr_repo: _Repo) -> None:
    """The reviewed change is no longer the change being merged: re-review."""
    before = pr_repo.key()
    pr_repo.git("checkout", "-q", "main")
    pr_repo.commit({"a.txt": b"a\ntail added on main\n"}, "main edits the same file")
    pr_repo.git("checkout", "-q", "pr")
    subprocess.run(["git", "merge", "-q", "--no-edit", "main"], cwd=pr_repo.root)
    pr_repo.commit({"a.txt": b"a changed by the PR\ntail added on main\n"}, "resolve")
    assert pr_repo.key() != before


def test_diff_key_distinguishes_binary_changes(pr_repo: _Repo) -> None:
    """`git patch-id` would give these two the same id; blob ids do not."""
    pr_repo.commit({"bin.dat": b"\x00\x02"}, "binary one")
    one = pr_repo.key()
    pr_repo.commit({"bin.dat": b"\x00\x03"}, "binary two")
    assert pr_repo.key() != one


def test_diff_key_sees_deletions_and_mode_changes(pr_repo: _Repo) -> None:
    before = pr_repo.key()
    pr_repo.commit({"b.txt": None}, "delete")
    deleted = pr_repo.key()
    assert deleted != before
    pr_repo.git("update-index", "--chmod=+x", "a.txt")
    pr_repo.git("commit", "-q", "-m", "chmod")
    assert pr_repo.key() != deleted


def test_diff_key_fails_loudly_on_an_unknown_head(pr_repo: _Repo) -> None:
    with pytest.raises(subprocess.CalledProcessError):
        _gate_module().diff_key("main", "f" * 40, cwd=pr_repo.root)


KEY = "b" * 64


def _diff_receipt(*, key: str = KEY, url: str = ARTIFACT_URL) -> str:
    return (
        "Drain-Review-Verdict: APPROVE\n"
        f"Drain-Review-Diff: {key}\n"
        f"Drain-Review-Artifact: {url}\n"
    )


def test_a_diff_receipt_is_honoured_only_for_the_computed_key() -> None:
    gate = _gate_module()
    body = _diff_receipt()
    assert gate.review_allows_merge(head=HEAD, body=body, diff_key=KEY)
    assert not gate.review_allows_merge(head=HEAD, body=body, diff_key="c" * 64)
    # No key computed (git failed, or an old caller): only a head receipt can match.
    assert not gate.review_allows_merge(head=HEAD, body=body, diff_key=None)
    # A malformed key is never a binding, even if the body repeats it verbatim.
    assert not gate.review_allows_merge(
        head=HEAD, body=_diff_receipt(key="xyz"), diff_key="xyz"
    )
    # The head receipt keeps working alongside.
    head_body = (
        "Drain-Review-Verdict: APPROVE\n"
        f"Drain-Review-Head: {HEAD}\n"
        f"Drain-Review-Artifact: {ARTIFACT_URL}\n"
    )
    assert gate.review_allows_merge(head=HEAD, body=head_body, diff_key=KEY)


def test_a_comment_attests_a_diff_only_for_the_computed_key() -> None:
    gate = _gate_module()
    comment = f"Drain-Review-Verdict: APPROVE\nDrain-Review-Diff: {KEY}\n\nLGTM\n"
    assert gate.comment_attests_approval(comment, HEAD, KEY)
    assert not gate.comment_attests_approval(comment, HEAD, "c" * 64)
    assert not gate.comment_attests_approval(comment, HEAD)
    blocked = f"Drain-Review-Verdict: BLOCK\nDrain-Review-Diff: {KEY}\n"
    assert not gate.comment_attests_approval(blocked, HEAD, KEY)


def test_cli_accepts_a_diff_receipt_with_the_workflow_key(tmp_path: Path) -> None:
    body_path = tmp_path / "body.md"
    body_path.write_text(_diff_receipt(), encoding="utf-8")
    base = [sys.executable, str(SCRIPT), "--head", HEAD, "--body-file", str(body_path)]
    ok = subprocess.run([*base, "--diff-key", KEY], capture_output=True, text=True)
    assert ok.returncode == 0 and ok.stdout.strip() == "allow", ok
    denied = subprocess.run(base, capture_output=True, text=True)
    assert denied.returncode == 2 and denied.stdout.strip() == "deny", denied


def test_cli_prints_the_key_a_reviewer_stamps(pr_repo: _Repo) -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--print-diff-key", "main", "pr"],
        cwd=pr_repo.root, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == pr_repo.key()


@pytest.mark.parametrize("workflow", [WORKFLOW, POLICY_WORKFLOW])
def test_workflows_pass_the_diff_key_and_still_never_check_out_the_head(workflow: Path) -> None:
    import yaml

    text = workflow.read_text(encoding="utf-8")
    jobs = yaml.safe_load(text)["jobs"]
    for job in jobs.values():
        for step in job.get("steps", []):
            if "actions/checkout" in str(step.get("uses", "")):
                # Base checkout only; history and trees for the merge base, no
                # blobs, and no PR code checked out or run.
                assert step["with"]["ref"] == "${{ github.event.pull_request.base.sha }}"
                assert step["with"]["filter"] == "blob:none"
    # One invocation = the line plus its `\`-continued lines.
    calls = re.findall(r"python scripts/drain_review_gate\.py(?:[^\n]*\\\n)*[^\n]*", text)
    receipt_calls = [c for c in calls if "--body-file" in c and "--ledger-head-file" not in c]
    assert receipt_calls, "no receipt check found"
    for call in receipt_calls:
        assert "--diff-key" in call, call
    assert "--print-diff-key" in text


def _meta(blob: str, *, status: str = "A") -> bytes:
    return f":000000 100644 {'0' * 40} {blob} {status}".encode()


def test_one_path_cannot_spell_two_entries() -> None:
    """Round-1 finding: tab/newline-joined entries let one path equal two.

    A reviewed file named `!<TAB>M<LF>.github/workflows/payload.yml` and a
    replacement adding `!` and `.github/workflows/payload.yml` serialized
    identically, so an inert fixture could become a live workflow under the
    same key.
    """
    gate = _gate_module()
    blob = "c" * 40
    meta = _meta(blob)
    one_path = b"!\t" + meta.lstrip(b":") + b"\n.github/workflows/payload.yml"
    reviewed = meta + b"\0" + one_path + b"\0"
    replacement = meta + b"\0!\0" + meta + b"\0.github/workflows/payload.yml\0"
    assert gate.diff_key_from_raw(reviewed) != gate.diff_key_from_raw(replacement)


def test_abbreviated_ids_are_refused_not_hashed() -> None:
    """Round-1 finding: two different blobs can share an abbreviation."""
    gate = _gate_module()
    with pytest.raises(ValueError):
        gate.diff_key_from_raw(b":000000 100644 0000000 eb40e032 A\0a.py\0")


def test_diff_key_uses_full_ids_whatever_core_abbrev_says(pr_repo: _Repo) -> None:
    """`--full-index` alone printed abbreviated ids; `--no-abbrev` must win."""
    pr_repo.git("config", "core.abbrev", "7")
    short = pr_repo.key()
    pr_repo.git("config", "core.abbrev", "12")
    assert pr_repo.key() == short
    raw = subprocess.run(
        ["git", "diff", "--raw", "--no-renames", "--full-index", "--no-abbrev", "-z",
         "main", "pr"], cwd=pr_repo.root, capture_output=True, check=True,
    ).stdout
    assert re.search(rb" [0-9a-f]{40} [0-9a-f]{40} M\0", raw), raw


def _run_blocks(workflow: Path) -> list[str]:
    import yaml

    blocks = []
    for job in yaml.safe_load(workflow.read_text(encoding="utf-8"))["jobs"].values():
        blocks += [str(step["run"]) for step in job.get("steps", []) if "run" in step]
    return blocks


@pytest.mark.parametrize("workflow", [WORKFLOW, POLICY_WORKFLOW])
def test_no_step_checks_out_the_pr_head(workflow: Path) -> None:
    """No checkout-style verb puts the head's tree on disk.

    A verb list, not a proof that nothing from the head is executed.
    """
    for block in _run_blocks(workflow):
        code = "\n".join(line for line in block.splitlines() if not line.lstrip().startswith("#"))
        for verb in ("git checkout", "git switch", "git worktree", "git reset",
                     "git restore", "git read-tree", "git archive"):
            assert verb not in code, (workflow.name, verb)


def test_scope_guard_passes_the_computed_key_verbatim() -> None:
    text = POLICY_WORKFLOW.read_text(encoding="utf-8")
    assert "DIFF_KEY: ${{ steps.diffkey.outputs.key }}" in text
    assert text.count('--diff-key "${DIFF_KEY}"') == 1
    assert '"${BASE_OID}" "${HEAD_OID}"' in text
    assert "base.sha }}" in text and "head.sha }}" in text


def test_auto_enroll_keys_the_base_and_head_from_one_read() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "headRefOid,baseRefOid" in text
    assert "BASE_OID=\"$(jq -r '.baseRefOid' <<<\"$PR_JSON\")\"" in text
    assert "BASE_OID: ${{ github.event.pull_request.base.sha }}" not in text
    assert text.count('--diff-key "$DIFF_KEY"') == 1
    assert 'DIFF_KEY="$(python scripts/drain_review_gate.py --print-diff-key' in text


def test_a_submodule_change_ignored_by_config_still_changes_the_key(pr_repo: _Repo) -> None:
    """Round-2 finding: `ignore = all` in .gitmodules hid gitlink edits from git diff."""
    pr_repo.commit(
        {".gitmodules": b'[submodule "sub"]\n\tpath = sub\n\turl = ./sub\n\tignore = all\n'},
        "submodule config",
    )
    before = pr_repo.key()
    pr_repo.git("update-index", "--add", "--cacheinfo", f"160000,{'1' * 40},sub")
    pr_repo.git("commit", "-q", "-m", "add gitlink")
    added = pr_repo.key()
    assert added != before
    pr_repo.git("update-index", "--cacheinfo", f"160000,{'2' * 40},sub")
    pr_repo.git("commit", "-q", "-m", "move gitlink")
    assert pr_repo.key() != added



def test_every_pr_needs_a_receipt_and_the_check_cannot_quietly_pass() -> None:
    """#4247 was armed and queued unreviewed on 2026-10-01 (Codex P2 on #4255).

    Pins the property, not just the call: the scope job has no job-level skip,
    no continue-on-error anywhere, and its receipt decision is not swallowed.
    """
    import yaml

    root = Path(__file__).resolve().parents[1] / ".github" / "workflows"
    enroll = yaml.safe_load((root / "auto-enroll-merge.yml").read_text("utf-8"))
    enable = next(
        s for s in enroll["jobs"]["enroll"]["steps"] if s.get("name") == "Enable auto-merge"
    )
    call = re.compile(r'python scripts/drain_review_gate\.py \\\n\s*--head "\$HEAD_OID"')
    assert call.search(enable["run"])

    guard = yaml.safe_load((root / "pr-scope-guard.yml").read_text("utf-8"))
    job = guard["jobs"]["scope"]
    assert "if" not in job and "continue-on-error" not in job
    assert not any(step.get("continue-on-error") for step in job["steps"])
    compare = next(st for st in job["steps"]
                   if st.get("name") == "Compare the PR diff against release-critical paths")
    run = compare["run"]
    gate = run.index("if ! python scripts/drain_review_gate.py --blocking-review")
    assert "exit 1" in run[gate:run.index("fi\n", run.index("scope guard: BLOCKED", gate))]
    assert "|| true" not in run[gate:gate + 400]
